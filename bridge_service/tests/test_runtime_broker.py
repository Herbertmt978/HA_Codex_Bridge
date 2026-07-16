from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import random
from threading import Barrier, Event, Lock, RLock
import time
from typing import Any

import pytest

import codex_bridge_service.runtime_broker as runtime_broker_module
from codex_bridge_service.codex_app_server import (
    DEFERRED_RESPONSE,
    AppServerNotification,
    AppServerRemoteError,
    AppServerRequest,
    AppServerResponseError,
    AppServerTimeoutError,
)
from codex_bridge_service.codex_app_server_contract import (
    AppServerProtocolValidator,
    load_bundled_protocol_contract,
)
from codex_bridge_service.event_store import EventStoreAdmissionError
from codex_bridge_service.models import RunMode
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_broker import (
    RuntimeBroker,
    RuntimeBrokerError,
    RuntimeEventPayloadTooLargeError,
)
from codex_bridge_service.runtime_gate import RuntimeGate
from codex_bridge_service.runtime_policy import (
    RuntimeProtocolMismatchError,
    approval_display,
    mode_policy,
    question_display,
    validate_thread_result,
)
from codex_bridge_service.runtime_state import (
    RuntimeStateCommitUnknownError,
    RuntimeStateError,
    RuntimeStateStore,
    runtime_fingerprint,
)
from codex_bridge_service.storage import BridgeStorage, ProjectMutationError


class _ContentionTrackingRLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self.contended = Event()

    def __enter__(self) -> _ContentionTrackingRLock:
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: object | None,
        exc_value: object | None,
        traceback: object | None,
    ) -> None:
        self._lock.release()


_PROTOCOL_VALIDATOR = AppServerProtocolValidator(load_bundled_protocol_contract())


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    message: str = "condition was not met",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def _turn(turn_id: str, *, status: str = "inProgress") -> dict[str, Any]:
    return {"id": turn_id, "items": [], "status": status}


def _thread(
    thread_id: str,
    *,
    cwd: str,
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "preview": "",
        "ephemeral": False,
        "modelProvider": "openai",
        "createdAt": 1_783_936_800,
        "updatedAt": 1_783_936_800,
        "status": {"type": "idle"},
        "cwd": cwd,
        "cliVersion": "0.139.0",
        "source": "appServer",
        "turns": turns or [],
        "sessionId": f"session-{thread_id}",
    }


class ValidatorBackedAppServer:
    """In-memory app-server peer that rejects non-schema protocol fixtures."""

    def __init__(self) -> None:
        self.generation = 1
        self.ready = True
        self.requests: list[tuple[str, Any]] = []
        self.request_timeouts: list[tuple[str, float | None]] = []
        self.responses: list[
            tuple[AppServerRequest, Any, AppServerResponseError | None]
        ] = []
        self.discarded: list[tuple[str | int, int]] = []
        self.aborted_generations: list[int] = []
        self.notification_handlers: dict[
            str, Callable[[AppServerNotification], None]
        ] = {}
        self.request_handlers: dict[str, Callable[[AppServerRequest], Any]] = {}
        self._scripted: dict[str, deque[Any]] = defaultdict(deque)
        self._validator = _PROTOCOL_VALIDATOR
        self._request_number = 0
        self._thread_number = 0
        self._turn_number = 0
        self._lock = Lock()

    def script(self, method: str, *results: Any) -> None:
        with self._lock:
            self._scripted[method].extend(results)

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        with self._lock:
            self.request_timeouts.append((method, timeout_seconds))
            self._request_number += 1
            request_number = self._request_number
            self._validator.validate_client_request(
                {"id": request_number, "method": method, "params": params}
            )
            self.requests.append((method, deepcopy(params)))
            scripted = (
                self._scripted[method].popleft() if self._scripted[method] else None
            )
            result = (
                scripted
                if scripted is not None
                else self._default_result(method, params)
            )
        if isinstance(result, BaseException):
            raise result
        self._validator.validate_client_response(method, result=result)
        return deepcopy(result)

    def register_notification_handler(
        self,
        method: str,
        handler: Callable[[AppServerNotification], None],
    ) -> None:
        self.notification_handlers[method] = handler

    def register_request_handler(
        self,
        method: str,
        handler: Callable[[AppServerRequest], Any],
    ) -> None:
        self.request_handlers[method] = handler

    def emit_notification(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        generation: int | None = None,
    ) -> None:
        resolved_generation = self.generation if generation is None else generation
        payload = deepcopy(dict(params))
        self._validator.validate_server_notification(
            {"method": method, "params": payload}
        )
        self.notification_handlers[method](
            AppServerNotification(
                method=method,
                params=payload,
                generation=resolved_generation,
            )
        )

    def emit_request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        request_id: str = "provider-request-private-1",
        generation: int | None = None,
    ) -> Any:
        resolved_generation = self.generation if generation is None else generation
        payload = deepcopy(dict(params))
        self._validator.validate_server_request(
            {"id": request_id, "method": method, "params": payload}
        )
        return self.request_handlers[method](
            AppServerRequest(
                request_id=request_id,
                method=method,
                params=payload,
                generation=resolved_generation,
            )
        )

    def respond(
        self,
        request: AppServerRequest,
        *,
        result: Any = None,
        error: AppServerResponseError | None = None,
    ) -> None:
        if error is None:
            self._validator.validate_server_response(request.method, result=result)
        else:
            error_payload: dict[str, Any] = {
                "code": error.code,
                "message": error.message,
            }
            self._validator.validate_server_response(
                request.method,
                error_message={"id": request.request_id, "error": error_payload},
                is_error=True,
            )
        with self._lock:
            self.responses.append((request, deepcopy(result), error))

    def discard_server_request(
        self,
        request_id: str | int,
        expected_generation: int,
    ) -> bool:
        with self._lock:
            self.discarded.append((request_id, expected_generation))
        return expected_generation == self.generation

    def abort_generation(self, expected_generation: int) -> bool:
        with self._lock:
            if expected_generation != self.generation:
                return False
            self.aborted_generations.append(expected_generation)
            self.generation += 1
        return True

    def _default_result(self, method: str, params: Any) -> dict[str, Any]:
        if method in {"thread/start", "thread/resume"}:
            self._thread_number += 1
            remote_thread_id = (
                params["threadId"]
                if method == "thread/resume"
                else f"codex-thread-{self._thread_number}"
            )
            permission_profile = params["config"]["default_permissions"]
            sandbox = (
                {"type": "readOnly", "networkAccess": False}
                if permission_profile == "ha_observe"
                else {
                    "type": "workspaceWrite",
                    "networkAccess": False,
                    "writableRoots": [
                        str(Path(params["cwd"]) / name)
                        for name in (".agents", ".codex", ".cursor", ".git", ".vscode")
                    ],
                    "excludeSlashTmp": True,
                    "excludeTmpdirEnvVar": True,
                }
            )
            return {
                "thread": _thread(remote_thread_id, cwd=params["cwd"]),
                "model": params["model"],
                "modelProvider": "openai",
                "cwd": params["cwd"],
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "sandbox": sandbox,
                "activePermissionProfile": {
                    "id": permission_profile,
                    "extends": None,
                },
            }
        if method == "turn/start":
            self._turn_number += 1
            return {"turn": _turn(f"codex-turn-{self._turn_number}")}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"no scripted result for {method}")


class BlockingTurnStartAppServer(ValidatorBackedAppServer):
    """Pause one turn/start before the response exposes its new turn ID."""

    def __init__(self) -> None:
        super().__init__()
        self.block_next_turn_start = False
        self.turn_start_entered = Event()
        self.release_turn_start = Event()

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if method == "turn/start" and self.block_next_turn_start:
            self.block_next_turn_start = False
            self.turn_start_entered.set()
            assert self.release_turn_start.wait(2)
        return super().request(
            method,
            params,
            timeout_seconds=timeout_seconds,
        )


def _storage_and_thread(
    tmp_path: Path,
    *,
    mode: RunMode = RunMode.EDIT,
) -> tuple[BridgeStorage, Any]:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Broker",
        root_path=str(tmp_path / "workspace"),
        default_model="gpt-5.6-codex",
        default_thinking_level="high",
    )
    thread = storage.create_thread(
        title="Broker contract",
        project_id=project.project_id,
        mode=mode,
    )
    return storage, thread


def _new_thread(
    storage: BridgeStorage,
    tmp_path: Path,
    *,
    name: str,
    mode: RunMode = RunMode.EDIT,
) -> Any:
    project = storage.create_project(
        name=name,
        root_path=str(tmp_path / name.lower()),
        default_model="gpt-5.6-codex",
        default_thinking_level="high",
    )
    return storage.create_thread(
        title=name,
        project_id=project.project_id,
        mode=mode,
    )


def _broker(
    storage: BridgeStorage,
    client: ValidatorBackedAppServer,
    *,
    queue_wait_timeout_seconds: float = 2.0,
    turn_timeout_seconds: float = 5.0,
    cancel_grace_seconds: float = 0.05,
    interaction_timeout_seconds: float = 5.0,
    run_terminal_listener: Callable[[str, str, str, bool], None] | None = None,
) -> RuntimeBroker:
    broker = RuntimeBroker(
        storage=storage,
        app_server=client,
        runtime_gate=RuntimeGate(limits=ResourceLimits()),
        queue_wait_timeout_seconds=queue_wait_timeout_seconds,
        watchdog_interval_seconds=0.01,
        turn_timeout_seconds=turn_timeout_seconds,
        cancel_grace_seconds=cancel_grace_seconds,
        interaction_timeout_seconds=interaction_timeout_seconds,
        run_terminal_listener=run_terminal_listener,
    )
    broker.start()
    return broker


def _requests(
    client: ValidatorBackedAppServer,
    method: str,
) -> list[Any]:
    with client._lock:
        return [deepcopy(params) for name, params in client.requests if name == method]


def _active_ids(
    storage: BridgeStorage,
    thread_id: str,
) -> tuple[str, str, str]:
    def active_run_has_started() -> bool:
        active_run_id = storage.load_thread(thread_id).active_run_id
        return active_run_id is not None and any(
            event.event_type == "run.started"
            and event.payload.get("run_id") == active_run_id
            for event in storage.list_thread_events(thread_id)
        )

    _wait_until(
        active_run_has_started,
        message="run did not reach its started projection",
    )
    record = storage.load_thread(thread_id)
    assert record.active_run_id is not None
    assert record.codex_thread_id is not None
    turn_requests = storage.list_thread_events(thread_id)
    started = [
        event
        for event in turn_requests
        if event.event_type == "run.started"
        and event.payload.get("run_id") == record.active_run_id
    ]
    assert started
    turn_id = started[-1].payload["turn_id"]
    assert isinstance(turn_id, str)
    return record.active_run_id, record.codex_thread_id, turn_id


def _complete(
    client: ValidatorBackedAppServer,
    *,
    remote_thread_id: str,
    turn_id: str,
    status: str = "completed",
) -> None:
    client.emit_notification(
        "turn/completed",
        {
            "threadId": remote_thread_id,
            "turn": _turn(turn_id, status=status),
        },
    )


def _pending_one(broker: RuntimeBroker, thread_id: str) -> dict[str, Any]:
    pending = broker.pending_interactions(thread_id=thread_id)
    assert len(pending) == 1
    value = pending[0]
    return value.model_dump() if hasattr(value, "model_dump") else deepcopy(value)


def _assert_broker_error(
    error: pytest.ExceptionInfo[RuntimeBrokerError],
    code: str,
) -> None:
    assert error.value.code == code
    text = str(error.value)
    assert "reusable-secret" not in text
    assert "private@example.test" not in text


def _restore_durable_runtime_checkpoint_after_stopping(
    broker: RuntimeBroker,
    storage: BridgeStorage,
) -> None:
    """Simulate process loss while still stopping test worker threads cleanly."""
    checkpoint_path = storage.root / "runtime-state.json"
    durable_checkpoint = checkpoint_path.read_bytes()
    broker.close()
    checkpoint_path.write_bytes(durable_checkpoint)


def test_start_registers_nonblocking_protocol_handlers(tmp_path: Path) -> None:
    storage, _thread_record = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        assert {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        }.issubset(client.request_handlers)
        assert {
            "item/agentMessage/delta",
            "item/completed",
            "serverRequest/resolved",
            "turn/completed",
            "turn/started",
        }.issubset(client.notification_handlers)
    finally:
        broker.close()


def test_terminal_listener_receives_run_request_identity_and_unattended_flag(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    terminals: list[tuple[str, str, str, bool]] = []
    broker = _broker(
        storage,
        client,
        run_terminal_listener=lambda run_id, status, request_id, unattended: (
            terminals.append((run_id, status, request_id, unattended))
        ),
    )
    try:
        submitted = broker.submit_prompt(
            thread.thread_id,
            "Run unattended",
            client_request_id="automation:autrun_fast123",
            unattended=True,
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: bool(terminals))

        assert submitted.run_id == run_id
        assert terminals == [
            (
                run_id,
                "completed",
                "automation:autrun_fast123",
                True,
            )
        ]
    finally:
        broker.close()


def test_item_activity_metadata_is_enum_only_and_redacts_provider_content(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Project activity",
            client_request_id="item-activity-metadata",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)

        client.emit_notification(
            "item/started",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "startedAtMs": 1_783_936_800_000,
                "item": {
                    "id": "command-item",
                    "type": "commandExecution",
                    "status": "inProgress",
                    "durationMs": 123,
                    "command": "cat /private/reusable-secret",
                    "cwd": "/private/workspace",
                    "commandActions": [
                        {
                            "type": "read",
                            "name": "reusable-secret",
                            "path": "/private/reusable-secret",
                            "command": "cat /private/reusable-secret",
                        },
                        {
                            "type": "read",
                            "name": "reusable-secret",
                            "path": "/private/reusable-secret",
                            "command": "cat /private/reusable-secret",
                        },
                        {
                            "type": "listFiles",
                            "command": "find /private/workspace -type f",
                        },
                    ],
                },
            },
        )
        client.emit_notification(
            "item/completed",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "completedAtMs": 1_783_936_800_123,
                "item": {
                    "id": "command-item",
                    "type": "commandExecution",
                    "status": "completed",
                    "durationMs": 123,
                    "command": "cat /private/reusable-secret",
                    "cwd": "/private/workspace",
                    "commandActions": [
                        {
                            "type": "search",
                            "command": "rg token /private/workspace",
                            "query": "token",
                        }
                    ],
                },
            },
        )
        client.emit_notification(
            "item/completed",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "completedAtMs": 1_783_936_800_456,
                "item": {
                    "id": "web-item",
                    "type": "webSearch",
                    "query": "private reusable-secret",
                    "action": {
                        "type": "openPage",
                        "url": "https://private.example/reusable-secret",
                    },
                },
            },
        )
        client.emit_notification(
            "item/completed",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "completedAtMs": 1_783_936_800_789,
                "item": {
                    "id": "file-item",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [
                        {
                            "path": "/private/workspace/secrets.txt",
                            "diff": "- reusable-secret",
                            "kind": {"type": "update", "move_path": "/private/new"},
                        },
                        {
                            "path": "/private/workspace/new.txt",
                            "diff": "secret",
                            "kind": {"type": "add"},
                        },
                    ],
                },
            },
        )
        _complete(client, remote_thread_id=remote_thread_id, turn_id=turn_id)
        _wait_until(lambda: storage.load_thread(thread.thread_id).status == "idle")

        events = storage.list_thread_events(thread.thread_id)
        command_started = next(
            event
            for event in events
            if event.event_type == "item.started"
            and event.payload.get("item_id") == "command-item"
        )
        assert command_started.payload == {
            "run_id": _run_id,
            "item_id": "command-item",
            "item_type": "commandExecution",
            "status": "inProgress",
            "duration_ms": 123,
            "action_types": ["read", "listFiles"],
        }
        command_completed = next(
            event
            for event in events
            if event.event_type == "item.completed"
            and event.payload.get("item_id") == "command-item"
        )
        assert command_completed.payload["action_types"] == ["search"]
        web_completed = next(
            event
            for event in events
            if event.event_type == "item.completed"
            and event.payload.get("item_id") == "web-item"
        )
        assert web_completed.payload["action_type"] == "openPage"
        file_completed = next(
            event
            for event in events
            if event.event_type == "item.completed"
            and event.payload.get("item_id") == "file-item"
        )
        assert file_completed.payload["change_kinds"] == ["update", "add"]
        serialized = json.dumps([event.payload for event in events])
        for secret in (
            "reusable-secret",
            "/private/workspace",
            "private.example",
            "cat /private",
        ):
            assert secret not in serialized
    finally:
        broker.close()


@pytest.mark.parametrize(
    "gate_limits",
    [
        ResourceLimits(max_active_turns=2),
        ResourceLimits(max_queued_prompts=7),
    ],
    ids=["active-turns", "queue-capacity"],
)
def test_broker_rejects_gate_with_different_resource_limits(
    tmp_path: Path,
    gate_limits: ResourceLimits,
) -> None:
    storage, _thread_record = _storage_and_thread(tmp_path)

    with pytest.raises(ValueError, match="identical resource limits"):
        RuntimeBroker(
            storage=storage,
            app_server=ValidatorBackedAppServer(),
            runtime_gate=RuntimeGate(limits=gate_limits),
        )


def test_maximum_route_prompt_that_cannot_fit_event_envelope_is_rejected_safely(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        with pytest.raises(RuntimeEventPayloadTooLargeError):
            broker.submit_prompt(
                thread.thread_id,
                "x" * (1024 * 1024),
                client_request_id="maximum-size-prompt",
            )

        assert _requests(client, "thread/start") == []
        assert not any(
            event.event_type == "message.created"
            and event.payload.get("client_request_id") == "maximum-size-prompt"
            for event in storage.list_thread_events(thread.thread_id)
        )

        accepted = broker.submit_prompt(
            thread.thread_id,
            "The broker remains available",
            client_request_id="after-oversized-prompt",
        )
        assert accepted.status == "starting"
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("mode", "approval_policy", "permission_profile"),
    [
        (RunMode.OBSERVE, "on-request", "ha_observe"),
        (RunMode.EDIT, "on-request", "ha_bridge"),
        (RunMode.FULL_AUTO, "never", "ha_bridge"),
    ],
)
def test_new_thread_and_turn_use_managed_permission_profile_without_legacy_sandbox(
    tmp_path: Path,
    mode: RunMode,
    approval_policy: str,
    permission_profile: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path, mode=mode)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    cwd = str(storage.resolve_workspace_path(thread.workspace_path))
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Inspect the workspace",
            client_request_id="client-message-1",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)

        assert run.thread_id == thread.thread_id
        assert run.status in {"starting", "running"}
        assert _requests(client, "thread/start") == [
            {
                "cwd": cwd,
                "model": "gpt-5.6-codex",
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
                "config": {"default_permissions": permission_profile},
                "ephemeral": False,
            }
        ]
        remote_thread_id = storage.load_thread(thread.thread_id).codex_thread_id
        assert remote_thread_id is not None
        assert _requests(client, "turn/start") == [
            {
                "threadId": remote_thread_id,
                "input": [{"type": "text", "text": "Inspect the workspace"}],
                "clientUserMessageId": "client-message-1",
                "cwd": cwd,
                "model": "gpt-5.6-codex",
                "effort": "high",
                "approvalPolicy": approval_policy,
                "approvalsReviewer": "user",
            }
        ]
    finally:
        broker.close()


@pytest.mark.parametrize(
    "mismatch",
    [
        "nested_cwd",
        "instruction_source",
        "nested_provider",
        "top_provider",
        "active_status",
    ],
)
def test_thread_result_rejects_untrusted_nested_environment_before_turn_start(
    tmp_path: Path,
    mismatch: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path, mode=RunMode.EDIT)
    client = ValidatorBackedAppServer()
    cwd = str(storage.resolve_workspace_path(thread.workspace_path))
    remote_thread = _thread("untrusted-thread", cwd=cwd)
    result: dict[str, Any] = {
        "thread": remote_thread,
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": cwd,
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_bridge", "extends": None},
        "sandbox": {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": [cwd],
            "excludeSlashTmp": True,
            "excludeTmpdirEnvVar": True,
        },
    }
    outside = str(tmp_path / "private-codex-home")
    if mismatch == "nested_cwd":
        remote_thread["cwd"] = outside
    elif mismatch == "instruction_source":
        result["instructionSources"] = [f"{outside}/AGENTS.md"]
    elif mismatch == "nested_provider":
        remote_thread["modelProvider"] = "custom"
    elif mismatch == "top_provider":
        result["modelProvider"] = "custom"
    else:
        remote_thread["status"] = {"type": "active", "activeFlags": []}
    _PROTOCOL_VALIDATOR.validate_client_response("thread/start", result=result)
    client.script("thread/start", result)
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject unsafe thread metadata",
            client_request_id=f"unsafe-thread-{mismatch}",
        )
        _wait_until(
            lambda: any(
                event.event_type == "run.failed"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )
        assert _requests(client, "turn/start") == []
        assert storage.load_thread(thread.thread_id).status == "error"
    finally:
        broker.close()


def test_thread_result_rejects_bare_sandbox_echo(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    result = {
        "thread": _thread("bare-sandbox-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_bridge", "extends": None},
        "sandbox": "workspace-write",
    }

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=mode_policy(RunMode.EDIT, workspace),
        )


@pytest.mark.parametrize("mode", [RunMode.EDIT, RunMode.FULL_AUTO])
@pytest.mark.parametrize(
    "root_shape",
    ["empty", "workspace", "supplemental", "workspace-plus", "maximum"],
)
def test_thread_result_accepts_bounded_supplemental_writable_roots(
    tmp_path: Path,
    mode: RunMode,
    root_shape: str,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    policy = mode_policy(mode, workspace)
    supplemental = [
        str(workspace / name)
        for name in (".agents", ".codex", ".cursor", ".git", ".vscode")
    ]
    if root_shape == "empty":
        roots = []
    elif root_shape == "workspace":
        roots = [str(workspace)]
    elif root_shape == "supplemental":
        roots = supplemental
    elif root_shape == "workspace-plus":
        roots = [str(workspace), *supplemental]
    else:
        roots = [str(workspace / f"root-{index}") for index in range(64)]
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox["writableRoots"] = roots
    result = {
        "thread": _thread("supplemental-roots-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": policy.approval_policy,
        "approvalsReviewer": "user",
        "activePermissionProfile": {
            "id": policy.permission_profile,
            "extends": None,
        },
        "sandbox": sandbox,
    }

    assert (
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )
        == "supplemental-roots-thread"
    )


@pytest.mark.parametrize(
    "invalid_roots",
    [
        "not-a-list",
        ("{workspace}/.codex",),
        [1],
        [""],
        ["relative/path"],
        ["{workspace}/./.codex"],
        ["{workspace}/.."],
        ["{workspace}/sub/../.codex"],
        ["{workspace}/../sibling"],
        ["{workspace}/../escape"],
        ["{outside}"],
        ["{workspace}/.codex", "{workspace}/.codex"],
        ["{workspace}/invalid\0root"],
        ["{workspace}//double-separator"],
        ["{workspace}/root-{index}" for index in range(65)],
    ],
    ids=[
        "wrong-type",
        "tuple",
        "non-string",
        "empty",
        "relative",
        "dot-component",
        "parent",
        "contained-parent",
        "sibling",
        "traversal",
        "outside",
        "duplicate",
        "nul",
        "noncanonical-separator",
        "too-many",
    ],
)
def test_thread_result_rejects_unbounded_or_noncanonical_writable_roots(
    tmp_path: Path,
    invalid_roots: object,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    outside = (tmp_path / "outside").resolve()
    policy = mode_policy(RunMode.EDIT, workspace)
    roots = invalid_roots
    if isinstance(roots, list):
        roots = [
            value.format(workspace=workspace, outside=outside, index=index)
            if isinstance(value, str)
            else value
            for index, value in enumerate(roots)
        ]
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox["writableRoots"] = roots
    result = {
        "thread": _thread("unsafe-roots-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": policy.approval_policy,
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_bridge", "extends": None},
        "sandbox": sandbox,
    }

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )


def test_thread_result_rejects_writable_root_symlink_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    outside = (tmp_path / "outside").resolve()
    workspace.mkdir()
    outside.mkdir()
    linked = workspace / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    policy = mode_policy(RunMode.EDIT, workspace)
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox["writableRoots"] = [str(linked)]
    result = {
        "thread": _thread("symlink-root-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_bridge", "extends": None},
        "sandbox": sandbox,
    }

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )


def test_thread_result_accepts_selected_workspace_symlink(
    tmp_path: Path,
) -> None:
    physical_workspace = (tmp_path / "physical-workspace").resolve()
    physical_workspace.mkdir()
    workspace = tmp_path / "workspace-link"
    try:
        workspace.symlink_to(physical_workspace, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    policy = mode_policy(RunMode.EDIT, workspace)
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox["writableRoots"] = [str(workspace), str(workspace / ".codex")]
    result = {
        "thread": _thread("symlink-workspace-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_bridge", "extends": None},
        "sandbox": sandbox,
    }

    assert (
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )
        == "symlink-workspace-thread"
    )


@pytest.mark.parametrize("mode", [RunMode.OBSERVE, RunMode.EDIT])
def test_thread_result_rejects_extra_sandbox_fields(
    tmp_path: Path,
    mode: RunMode,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    policy = mode_policy(mode, workspace)
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox["dangerFullAccess"] = True
    result = {
        "thread": _thread("extra-sandbox-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": policy.approval_policy,
        "approvalsReviewer": "user",
        "activePermissionProfile": {
            "id": policy.permission_profile,
            "extends": None,
        },
        "sandbox": sandbox,
    }

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )


@pytest.mark.parametrize(
    ("mode", "field", "value"),
    [
        (RunMode.OBSERVE, "networkAccess", 0),
        (RunMode.EDIT, "networkAccess", 0),
        (RunMode.EDIT, "excludeSlashTmp", 1),
        (RunMode.EDIT, "excludeTmpdirEnvVar", 1),
    ],
)
def test_thread_result_rejects_integer_sandbox_booleans(
    tmp_path: Path,
    mode: RunMode,
    field: str,
    value: int,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    policy = mode_policy(mode, workspace)
    sandbox = deepcopy(policy.sandbox_policy)
    sandbox[field] = value
    result = {
        "thread": _thread("typed-sandbox-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": policy.approval_policy,
        "approvalsReviewer": "user",
        "activePermissionProfile": {
            "id": policy.permission_profile,
            "extends": None,
        },
        "sandbox": sandbox,
    }

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=policy,
        )


def test_observe_result_rejects_workspace_write_before_turn_start(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path, mode=RunMode.OBSERVE)
    client = ValidatorBackedAppServer()
    cwd = str(storage.resolve_workspace_path(thread.workspace_path))
    result = {
        "thread": _thread("writable-observe-thread", cwd=cwd),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": cwd,
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "activePermissionProfile": {"id": "ha_observe", "extends": None},
        "sandbox": {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": [cwd],
            "excludeSlashTmp": True,
            "excludeTmpdirEnvVar": True,
        },
    }
    _PROTOCOL_VALIDATOR.validate_client_response("thread/start", result=result)
    client.script("thread/start", result)
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Inspect without changing files",
            client_request_id="observe-must-be-read-only",
        )
        _wait_until(
            lambda: any(
                event.event_type == "run.failed"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )

        assert _requests(client, "turn/start") == []
        assert storage.load_thread(thread.thread_id).status == "error"
    finally:
        broker.close()


@pytest.mark.parametrize(
    "active_profile",
    [
        None,
        {"id": "ha_bridge", "extends": None},
        {"id": "ha_observe"},
        {"id": "ha_observe", "extends": ":read-only"},
    ],
    ids=["missing", "wrong-id", "missing-provenance", "unexpected-parent"],
)
def test_observe_result_requires_exact_managed_profile_provenance(
    tmp_path: Path,
    active_profile: dict[str, object] | None,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    result: dict[str, object] = {
        "thread": _thread("observe-profile-thread", cwd=str(workspace)),
        "model": "gpt-5.6-codex",
        "modelProvider": "openai",
        "cwd": str(workspace),
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "sandbox": {"type": "readOnly", "networkAccess": False},
    }
    if active_profile is not None:
        result["activePermissionProfile"] = active_profile

    with pytest.raises(RuntimeProtocolMismatchError):
        validate_thread_result(
            result,
            expected_cwd=workspace,
            expected_model="gpt-5.6-codex",
            policy=mode_policy(RunMode.OBSERVE, workspace),
        )


@pytest.mark.parametrize(
    "private_text",
    [
        "inspect [/data/codex-home/auth.json]",
        r"inspect {C:\Codex\auth.json}",
        r"inspect|\\server\share\auth.json;",
    ],
    ids=["posix", "drive", "unc"],
)
def test_projection_denies_punctuation_wrapped_absolute_paths(
    tmp_path: Path,
    private_text: str,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    assert (
        approval_display(
            "item/fileChange/requestApproval",
            {"reason": private_text},
            expected_cwd=workspace,
        )
        is None
    )
    assert (
        approval_display(
            "item/commandExecution/requestApproval",
            {
                "command": private_text,
                "commandActions": [
                    {
                        "type": "listFiles",
                        "command": private_text,
                        "path": str(workspace),
                    }
                ],
                "cwd": str(workspace),
            },
            expected_cwd=workspace,
        )
        is None
    )
    assert (
        question_display(
            {
                "questions": [
                    {
                        "id": "scope",
                        "header": "Scope",
                        "question": private_text,
                        "options": [],
                        "isOther": True,
                        "isSecret": False,
                    }
                ]
            }
        )
        is None
    )


def test_existing_thread_resumes_then_starts_a_fresh_turn_with_safe_overrides(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path, mode=RunMode.EDIT)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    cwd = str(storage.resolve_workspace_path(thread.workspace_path))
    try:
        broker.submit_prompt(
            thread.thread_id,
            "First",
            client_request_id="client-first",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, first_turn_id = _active_ids(
            storage, thread.thread_id
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=first_turn_id,
        )
        _wait_until(lambda: storage.load_thread(thread.thread_id).status == "idle")

        broker.submit_prompt(
            thread.thread_id,
            "Follow up",
            client_request_id="client-follow-up",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 2)

        assert _requests(client, "thread/resume") == [
            {
                "threadId": remote_thread_id,
                "cwd": cwd,
                "model": "gpt-5.6-codex",
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "config": {"default_permissions": "ha_bridge"},
            }
        ]
        assert _requests(client, "turn/start")[-1] == {
            "threadId": remote_thread_id,
            "input": [{"type": "text", "text": "Follow up"}],
            "clientUserMessageId": "client-follow-up",
            "cwd": cwd,
            "model": "gpt-5.6-codex",
            "effort": "high",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
        }
    finally:
        broker.close()


def test_active_thread_steers_and_cancel_interrupts_with_exact_preconditions(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, cancel_grace_seconds=1.0)
    try:
        first = broker.submit_prompt(
            thread.thread_id,
            "Start",
            client_request_id="client-start",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)

        steered = broker.submit_prompt(
            thread.thread_id,
            "Use the smaller fix",
            client_request_id="client-steer",
        )
        _wait_until(lambda: len(_requests(client, "turn/steer")) == 1)
        assert steered.run_id == first.run_id
        assert _requests(client, "turn/steer") == [
            {
                "threadId": remote_thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": "Use the smaller fix"}],
                "clientUserMessageId": "client-steer",
            }
        ]
        steer_event = next(
            event
            for event in storage.event_store.replay(
                after_cursor=0,
                scopes=("thread",),
                thread_ids=(thread.thread_id,),
            ).events
            if event.event_type == "message.created"
            and event.payload.get("client_request_id") == "client-steer"
        )
        runtime_state = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert steer_event.operation_id is not None
        assert runtime_state["_bridge_operation"]["operation_id"] == (
            steer_event.operation_id
        )

        cancelling = broker.cancel_run(thread.thread_id, run_id=first.run_id)
        assert cancelling.status == "cancelling"
        assert _requests(client, "turn/interrupt") == [
            {"threadId": remote_thread_id, "turnId": turn_id}
        ]
    finally:
        broker.close()


@pytest.mark.parametrize("failure", ["timeout", "mismatched_turn"])
def test_unknown_steer_outcome_is_nonreplayable_and_aborts_generation(
    tmp_path: Path,
    failure: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="UnknownSteerQueued")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Start",
            client_request_id=f"unknown-steer-active-{failure}",
        )
        _active_ids(storage, thread.thread_id)
        queued = broker.submit_prompt(
            queued_thread.thread_id,
            "Must be cleared with the unknown generation",
            client_request_id=f"unknown-steer-queued-{failure}",
        )
        assert queued.status == "queued"
        scripted: object = (
            AppServerTimeoutError("turn/steer")
            if failure == "timeout"
            else {"turnId": "mismatched-turn"}
        )
        client.script("turn/steer", scripted)

        request_id = f"unknown-steer-follow-up-{failure}"
        with pytest.raises(RuntimeBrokerError) as unknown:
            broker.submit_prompt(
                thread.thread_id,
                "This may have reached Codex",
                client_request_id=request_id,
            )
        _assert_broker_error(unknown, "steer_outcome_unknown")

        with pytest.raises(RuntimeBrokerError) as replay:
            broker.submit_prompt(
                thread.thread_id,
                "This may have reached Codex",
                client_request_id=request_id,
            )
        _assert_broker_error(replay, "steer_outcome_unknown")
        assert len(_requests(client, "turn/steer")) == 1
        assert client.aborted_generations == [1]
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        assert broker.runtime_snapshot().queued_prompts == 0
        events = storage.list_thread_events(thread.thread_id)
        assert any(event.event_type == "run.steer_outcome_unknown" for event in events)
        assert any(event.event_type == "run.interrupted" for event in events)
        stored_events = storage.event_store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=(thread.thread_id,),
        ).events
        uncertain = next(
            event
            for event in stored_events
            if event.event_type == "run.steer_outcome_unknown"
        )
        interrupted = next(
            event for event in stored_events if event.event_type == "run.interrupted"
        )
        assert uncertain.operation_id is not None
        assert uncertain.operation_id == interrupted.operation_id
        assert storage.load_thread(queued_thread.thread_id).status == "error"
    finally:
        broker.close()


def test_client_request_id_is_globally_idempotent_and_other_threads_queue(
    tmp_path: Path,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    second_thread = _new_thread(storage, tmp_path, name="Second")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        first = broker.submit_prompt(
            first_thread.thread_id,
            "First",
            client_request_id="same-global-request",
        )
        duplicate = broker.submit_prompt(
            first_thread.thread_id,
            "First",
            client_request_id="same-global-request",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        queued = broker.submit_prompt(
            second_thread.thread_id,
            "Second",
            client_request_id="different-request",
        )

        assert duplicate == first
        assert queued.status == "queued"
        assert len(_requests(client, "thread/start")) == 1

        _run_id, remote_thread_id, turn_id = _active_ids(
            storage, first_thread.thread_id
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(
            lambda: len(_requests(client, "thread/start")) == 2,
            message="queued prompt was not promoted after the active turn completed",
        )
        _wait_until(
            lambda: storage.load_thread(second_thread.thread_id).status == "running"
        )
    finally:
        broker.close()


def test_total_run_deadline_includes_queue_wait_and_active_time(
    tmp_path: Path,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    second_thread = _new_thread(storage, tmp_path, name="TotalDeadline")
    client = ValidatorBackedAppServer()
    limits = ResourceLimits(
        run_total_timeout_seconds=2.0,
        run_idle_timeout_seconds=5.0,
        cancel_grace_seconds=0.05,
    )
    broker = RuntimeBroker(
        storage=storage,
        app_server=client,
        runtime_gate=RuntimeGate(limits=limits),
        resource_limits=limits,
        queue_wait_timeout_seconds=2.0,
        watchdog_interval_seconds=0.005,
        turn_timeout_seconds=2.0,
        cancel_grace_seconds=0.05,
        interaction_timeout_seconds=5.0,
    )
    broker.start()
    try:
        broker.submit_prompt(
            first_thread.thread_id,
            "Hold the global lease",
            client_request_id="total-deadline-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(
            storage, first_thread.thread_id
        )

        queued = broker.submit_prompt(
            second_thread.thread_id,
            "Queue time consumes the same total budget",
            client_request_id="total-deadline-queued",
        )
        assert queued.status == "queued"
        time.sleep(0.75)
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(
            lambda: (
                len(_requests(client, "turn/start")) == 2
                or storage.load_thread(second_thread.thread_id).status == "error"
            ),
            timeout=3.0,
            message="queued run neither started nor exhausted its total budget",
        )
        turn_start_timeouts = [
            timeout
            for method, timeout in client.request_timeouts
            if method == "turn/start"
        ]
        if len(turn_start_timeouts) == 2:
            assert turn_start_timeouts[-1] is not None
            assert 0 < turn_start_timeouts[-1] < 1.25
        else:
            assert len(turn_start_timeouts) == 1

        _wait_until(
            lambda: storage.load_thread(second_thread.thread_id).status == "error",
            timeout=3.0,
            message="queued and active phases received separate total-timeout budgets",
        )
        assert broker.runtime_snapshot().active_turns == 0
    finally:
        broker.close()


def test_start_requests_keep_the_control_rpc_timeout_cap(tmp_path: Path) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    limits = ResourceLimits(
        run_total_timeout_seconds=60 * 60,
        run_idle_timeout_seconds=60 * 60,
    )
    broker = RuntimeBroker(
        storage=storage,
        app_server=client,
        runtime_gate=RuntimeGate(limits=limits),
        resource_limits=limits,
    )
    broker.start()
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Keep control requests bounded",
            client_request_id="control-rpc-timeout-cap",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)

        start_timeouts = {
            method: timeout
            for method, timeout in client.request_timeouts
            if method in {"thread/start", "turn/start"}
        }
        assert start_timeouts.keys() == {"thread/start", "turn/start"}
        assert start_timeouts["thread/start"] == pytest.approx(30.0)
        assert start_timeouts["turn/start"] == pytest.approx(30.0)
    finally:
        broker.close()


def test_uploaded_attachments_remain_unselected_for_a_text_only_prompt(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="private.txt",
        mime_type="text/plain",
        content=b"private input",
    )
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Summarize the workspace without selecting uploads",
            client_request_id="client-text-only-after-upload",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)

        runtime_run = broker._state.runs[run.run_id]
        assert runtime_run.attachment_ids == []
        assert runtime_run.attachment_manifest_fingerprint == runtime_fingerprint([])
        assert _requests(client, "turn/start")[0]["input"] == [
            {
                "type": "text",
                "text": "Summarize the workspace without selecting uploads",
            }
        ]
    finally:
        broker.close()


def test_terminal_run_compaction_preserves_prompt_idempotency_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_broker_module, "_MAX_TERMINAL_RUNS", 1)
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    requests = [(f"request-{index}", f"Prompt {index}") for index in range(3)]
    try:
        first_run_id: str | None = None
        for index, (request_id, prompt) in enumerate(requests, start=1):
            run = broker.submit_prompt(
                thread.thread_id,
                prompt,
                client_request_id=request_id,
            )
            first_run_id = first_run_id or run.run_id
            _wait_until(lambda: len(_requests(client, "turn/start")) == index)
            _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
            _complete(
                client,
                remote_thread_id=remote_thread_id,
                turn_id=turn_id,
            )
            _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

        checkpoint = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert len(checkpoint["runs"]) == 1
        assert len(checkpoint["request_idempotency"]) == 3

        duplicate = broker.submit_prompt(
            thread.thread_id,
            "Prompt 0",
            client_request_id="request-0",
        )
        assert duplicate.run_id == first_run_id
        assert duplicate.status == "completed"
        assert len(_requests(client, "turn/start")) == 3
    finally:
        broker.close()


def test_idempotency_capacity_refuses_new_work_without_leaking_a_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_broker_module, "_MAX_REQUEST_OUTCOMES", 1)
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "First",
            client_request_id="capacity-first",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)

        with pytest.raises(RuntimeBrokerError) as full:
            broker.submit_prompt(
                thread.thread_id,
                "Second",
                client_request_id="capacity-second",
            )

        _assert_broker_error(full, "runtime_idempotency_capacity")
        assert broker.runtime_snapshot().active_turns == 1
        assert broker.runtime_snapshot().queued_prompts == 0
        assert len(_requests(client, "turn/start")) == 1
    finally:
        broker.close()


def test_active_thread_cannot_be_deleted_while_broker_owns_its_turn(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Keep the active chat",
            client_request_id="delete-active-owner",
        )
        _active_ids(storage, thread.thread_id)

        with pytest.raises(RuntimeBrokerError) as busy:
            broker.delete_thread(thread.thread_id)

        _assert_broker_error(busy, "runtime_thread_busy")
        assert busy.value.status_code == 409
        assert busy.value.public_detail() == {
            "code": "runtime_thread_busy",
            "retryable": True,
        }
        assert storage.load_thread(thread.thread_id).thread_id == thread.thread_id
    finally:
        broker.close()


def test_broker_thread_delete_rejects_held_automation_preparation_without_purge(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Seed retained runtime history",
            client_request_id="automation-delete-thread-seed",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(client, remote_thread_id=remote_thread_id, turn_id=turn_id)
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        state_path = storage.root / "runtime-state.json"
        before_state = state_path.read_bytes()
        before_runtime = json.loads(before_state)
        assert run.run_id in before_runtime["runs"]
        assert "automation-delete-thread-seed" in before_runtime["request_idempotency"]

        with storage.prepare_automation_target(
            {"kind": "continue_thread", "thread_id": thread.thread_id},
            title="Automation continuation",
            mode=RunMode.FULL_AUTO,
        ):
            before_thread = storage.load_thread(thread.thread_id).model_dump()
            with pytest.raises(ProjectMutationError, match="reserved"):
                broker.delete_thread(thread.thread_id)

            assert state_path.read_bytes() == before_state
            assert storage.load_thread(thread.thread_id).model_dump() == before_thread
            assert storage.load_thread(thread.thread_id).thread_id == thread.thread_id
            assert broker.runtime_snapshot().active_turns == 0
    finally:
        broker.close()


def test_broker_project_delete_rejects_held_automation_preparation_without_purge(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    project_id = thread.project_id
    assert project_id is not None
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Seed retained project runtime history",
            client_request_id="automation-delete-project-seed",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(client, remote_thread_id=remote_thread_id, turn_id=turn_id)
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        state_path = storage.root / "runtime-state.json"
        before_state = state_path.read_bytes()
        before_runtime = json.loads(before_state)
        assert run.run_id in before_runtime["runs"]
        assert "automation-delete-project-seed" in before_runtime["request_idempotency"]
        project_path = storage._project_path(project_id)
        thread_path = storage._thread_path(thread.thread_id)

        with storage.prepare_automation_target(
            {"kind": "standalone", "project_id": project_id},
            title="Automation standalone",
            mode=RunMode.FULL_AUTO,
        ) as prepared:
            prepared_path = storage._thread_path(prepared.thread_id)
            with pytest.raises(ProjectMutationError, match="reserved"):
                broker.delete_project(project_id)

            assert state_path.read_bytes() == before_state
            assert project_path.exists()
            assert thread_path.exists()
            assert prepared_path.exists()
            assert storage.load_project(project_id).project_id == project_id
    finally:
        broker.close()


def test_thread_delete_waits_for_inflight_steer_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    entered = Event()
    release = Event()
    original_request = client.request

    def blocked_steer(
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if method == "turn/steer":
            entered.set()
            assert release.wait(2)
        return original_request(method, params, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(client, "request", blocked_steer)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Start the owned turn",
            client_request_id="delete-steer-active",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        event_path = storage._event_log_path(thread.thread_id)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                broker.submit_prompt,
                thread.thread_id,
                "Publish this steer exactly once",
                client_request_id="delete-steer-inflight",
            )
            assert entered.wait(1)
            _complete(
                client,
                remote_thread_id=remote_thread_id,
                turn_id=turn_id,
            )
            _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

            with pytest.raises(RuntimeBrokerError) as busy:
                broker.delete_thread(thread.thread_id)
            _assert_broker_error(busy, "runtime_thread_busy")

            release.set()
            steered = future.result(timeout=1)

        assert steered.status == "completed"
        events = storage.list_thread_events(thread.thread_id)
        steered_messages = [
            event
            for event in events
            if event.event_type == "message.created"
            and event.payload.get("client_request_id") == "delete-steer-inflight"
        ]
        assert len(steered_messages) == 1

        broker.delete_thread(thread.thread_id)
        assert not event_path.exists()
        time.sleep(0.05)
        assert not event_path.exists()
    finally:
        release.set()
        broker.close()


def test_thread_delete_waits_for_inflight_cancel_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    # Keep the cancellation watchdog outside this test's intentionally
    # blocked interrupt window. The contract under test is deletion/publication
    # ordering, not watchdog expiry under scheduler load.
    broker = _broker(storage, client, cancel_grace_seconds=5.0)
    entered = Event()
    release = Event()
    original_request = client.request

    def blocked_interrupt(
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if method == "turn/interrupt":
            entered.set()
            assert release.wait(2)
        return original_request(method, params, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(client, "request", blocked_interrupt)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Cancel without racing deletion",
            client_request_id="delete-cancel-active",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        event_path = storage._event_log_path(thread.thread_id)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                broker.cancel_run,
                thread.thread_id,
                run_id=run.run_id,
            )
            assert entered.wait(1)
            _complete(
                client,
                remote_thread_id=remote_thread_id,
                turn_id=turn_id,
            )
            _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

            with pytest.raises(RuntimeBrokerError) as busy:
                broker.delete_thread(thread.thread_id)
            _assert_broker_error(busy, "runtime_thread_busy")

            release.set()
            cancelled = future.result(timeout=1)

        assert cancelled.status == "completed"
        broker.delete_thread(thread.thread_id)
        assert not event_path.exists()
        time.sleep(0.05)
        assert not event_path.exists()
    finally:
        release.set()
        broker.close()


def test_queued_thread_cannot_be_deleted_while_broker_owns_its_lease(
    tmp_path: Path,
) -> None:
    storage, active_thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="QueuedDelete")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            active_thread.thread_id,
            "Hold the runtime",
            client_request_id="delete-queue-active",
        )
        queued = broker.submit_prompt(
            queued_thread.thread_id,
            "Wait in the queue",
            client_request_id="delete-queue-owner",
        )
        assert queued.status == "queued"

        with pytest.raises(RuntimeBrokerError) as busy:
            broker.delete_thread(queued_thread.thread_id)

        _assert_broker_error(busy, "runtime_thread_busy")
        assert storage.load_thread(queued_thread.thread_id).status == "queued"
        assert broker.runtime_snapshot().queued_prompts == 1
    finally:
        broker.close()


def test_thread_cannot_own_more_than_one_queued_prompt(tmp_path: Path) -> None:
    storage, active_thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="SingleQueuedOwner")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            active_thread.thread_id,
            "Hold the runtime",
            client_request_id="single-queue-active",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(
            storage,
            active_thread.thread_id,
        )
        first = broker.submit_prompt(
            queued_thread.thread_id,
            "Wait once",
            client_request_id="single-queue-first",
        )
        assert first.status == "queued"

        with pytest.raises(RuntimeBrokerError) as duplicate:
            broker.submit_prompt(
                queued_thread.thread_id,
                "Do not queue twice",
                client_request_id="single-queue-second",
            )
        _assert_broker_error(duplicate, "thread_prompt_pending")
        assert duplicate.value.retryable is True
        record = storage.load_thread(queued_thread.thread_id)
        assert record.status == "queued"
        assert [
            run.run_id
            for run in broker._state.runs.values()
            if run.thread_id == queued_thread.thread_id and run.status == "queued"
        ] == [first.run_id]
        assert broker.runtime_snapshot().queued_prompts == 1

        cancelled = broker.cancel_run(queued_thread.thread_id, run_id=first.run_id)
        assert cancelled.status == "cancelled"
        _wait_until(
            lambda: storage.load_thread(queued_thread.thread_id).status == "idle"
        )
        assert broker.runtime_snapshot().queued_prompts == 0
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        broker.close()


def test_project_cascade_delete_is_blocked_when_a_child_thread_is_owned(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    project_id = storage.load_thread(thread.thread_id).project_id
    assert project_id is not None
    sibling = storage.create_thread(
        title="Idle sibling",
        project_id=project_id,
        mode=RunMode.EDIT,
    )
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Keep every project child",
            client_request_id="delete-project-owner",
        )
        _active_ids(storage, thread.thread_id)

        with pytest.raises(RuntimeBrokerError) as busy:
            broker.delete_project(project_id)

        _assert_broker_error(busy, "runtime_thread_busy")
        assert storage.load_project(project_id).project_id == project_id
        assert storage.load_thread(thread.thread_id).thread_id == thread.thread_id
        assert storage.load_thread(sibling.thread_id).thread_id == sibling.thread_id
    finally:
        broker.close()


def test_thread_create_is_serialized_before_broker_project_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Create-delete race",
        root_path=str(tmp_path / "workspace"),
    )
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    mutation_lock = _ContentionTrackingRLock()
    storage._thread_mutation_lock = mutation_lock
    save_entered = Event()
    release_save = Event()
    original_commit = storage._commit_prepared_thread_with_events_locked

    def blocked_commit(record: Any, events: Any) -> None:
        save_entered.set()
        assert release_save.wait(2)
        original_commit(record, events)

    monkeypatch.setattr(
        storage,
        "_commit_prepared_thread_with_events_locked",
        blocked_commit,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            create_future = pool.submit(
                storage.create_thread,
                title="Created before deletion",
                mode=RunMode.EDIT,
                project_id=project.project_id,
            )
            assert save_entered.wait(1)
            delete_future = pool.submit(broker.delete_project, project.project_id)
            delete_was_serialized = mutation_lock.contended.wait(1)
            release_save.set()
            created = create_future.result(timeout=2)
            delete_future.result(timeout=2)

        # The dedicated automation admission lock may serialize deletion
        # before the thread lock becomes contended.
        assert (
            delete_was_serialized
            or not storage._project_path(project.project_id).exists()
        )
        assert not storage._project_path(project.project_id).exists()
        assert not storage._thread_path(created.thread_id).exists()
        assert not storage._event_log_path(created.thread_id).exists()
    finally:
        release_save.set()
        broker.close()


def test_rejected_special_project_delete_does_not_purge_runtime_history(
    tmp_path: Path,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    thread = storage.create_thread(title="Direct chat", mode=RunMode.EDIT)
    assert thread.project_id is not None
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Retain special project history",
            client_request_id="delete-special-owner",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        checkpoint_path = storage.root / "runtime-state.json"
        before = checkpoint_path.read_bytes()

        with pytest.raises(ProjectMutationError):
            broker.delete_project(thread.project_id)

        assert checkpoint_path.read_bytes() == before
        assert storage.load_project(thread.project_id).project_id == thread.project_id
        assert storage.load_thread(thread.thread_id).thread_id == thread.thread_id
    finally:
        broker.close()


def test_deleting_terminal_thread_reclaims_idempotency_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_broker_module, "_MAX_REQUEST_OUTCOMES", 1)
    storage, old_thread = _storage_and_thread(tmp_path)
    next_thread = _new_thread(storage, tmp_path, name="CapacityAfterDelete")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        old_run = broker.submit_prompt(
            old_thread.thread_id,
            "Retained terminal request",
            client_request_id="delete-capacity-old",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, old_thread.thread_id)
        assert (
            client.emit_request(
                "item/tool/requestUserInput",
                {
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                    "itemId": "delete-capacity-question",
                    "questions": [
                        {
                            "id": "scope",
                            "header": "Scope",
                            "question": "Which scope should be retained?",
                            "options": [
                                {
                                    "label": "Source",
                                    "description": "Retain source only.",
                                }
                            ],
                            "isOther": False,
                            "isSecret": False,
                        }
                    ],
                },
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, old_thread.thread_id)
        broker.answer_user_input(
            thread_id=old_thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=old_run.run_id,
            turn_id=turn_id,
            item_id="delete-capacity-question",
            answers={"scope": ["Source"]},
            client_request_id="delete-capacity-answer",
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        retained_checkpoint = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert pending["interaction_id"] in retained_checkpoint["interactions"]

        with pytest.raises(RuntimeBrokerError) as full:
            broker.submit_prompt(
                next_thread.thread_id,
                "Capacity is still full",
                client_request_id="delete-capacity-blocked",
            )
        _assert_broker_error(full, "runtime_idempotency_capacity")

        broker.delete_thread(old_thread.thread_id)

        checkpoint = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert all(
            run["thread_id"] != old_thread.thread_id
            for run in checkpoint["runs"].values()
        )
        assert all(
            interaction["thread_id"] != old_thread.thread_id
            for interaction in checkpoint["interactions"].values()
        )
        assert all(
            outcome["thread_id"] != old_thread.thread_id
            for outcome in checkpoint["request_idempotency"].values()
        )
        accepted = broker.submit_prompt(
            next_thread.thread_id,
            "Capacity was reclaimed",
            client_request_id="delete-capacity-new",
        )
        assert accepted.status in {"starting", "running"}
    finally:
        broker.close()


def test_deletion_state_persistence_failure_keeps_thread_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Create terminal state",
            client_request_id="delete-persist-owner",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

        def fail_save(_state: object) -> None:
            raise RuntimeStateError("injected deletion checkpoint failure")

        monkeypatch.setattr(broker._store, "save", fail_save)
        with pytest.raises(RuntimeStateError):
            broker.delete_thread(thread.thread_id)

        assert storage.load_thread(thread.thread_id).thread_id == thread.thread_id
        with pytest.raises(RuntimeBrokerError) as fatal:
            broker.submit_prompt(
                thread.thread_id,
                "Broker remains unavailable",
                client_request_id="delete-persist-retry",
            )
        _assert_broker_error(fatal, "app_server_unavailable")
    finally:
        broker.close()


def test_submission_persistence_failure_releases_reserved_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)

    def fail_save(_state: object, *, events: object) -> None:
        del events
        raise RuntimeStateError("injected checkpoint failure")

    monkeypatch.setattr(broker._store, "save_with_events", fail_save)
    try:
        with pytest.raises(RuntimeStateError):
            broker.submit_prompt(
                thread.thread_id,
                "Must roll back",
                client_request_id="persistence-failure",
            )

        assert broker.runtime_snapshot().active_turns == 0
        assert broker.runtime_snapshot().queued_prompts == 0
        assert client.requests == []
        record = storage.load_thread(thread.thread_id)
        assert record.active_run_id is None
        assert record.status == "error"
        with pytest.raises(RuntimeBrokerError) as fatal:
            broker.submit_prompt(
                thread.thread_id,
                "Must remain unavailable",
                client_request_id="persistence-failure-retry",
            )
        _assert_broker_error(fatal, "app_server_unavailable")
    finally:
        broker.close()


def test_submission_admission_failure_is_retryable_without_fatalizing_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    original_save = broker._store.save_with_events

    def reject_before_state(_state: object, *, events: object) -> None:
        del events
        raise EventStoreAdmissionError("injected pre-state journal rejection")

    monkeypatch.setattr(broker._store, "save_with_events", reject_before_state)
    try:
        with pytest.raises(EventStoreAdmissionError):
            broker.submit_prompt(
                thread.thread_id,
                "Retry after journal admission",
                client_request_id="journal-admission-rejected",
            )

        assert broker.runtime_snapshot().active_turns == 0
        assert broker.runtime_snapshot().queued_prompts == 0
        assert client.requests == []
        monkeypatch.setattr(broker._store, "save_with_events", original_save)

        accepted = broker.submit_prompt(
            thread.thread_id,
            "Journal admission recovered",
            client_request_id="journal-admission-recovered",
        )
        assert accepted.status == "starting"
    finally:
        broker.close()


def test_submission_does_not_overwrite_a_prepared_unknown_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    rollback_called = False

    def fail_after_prepare(_state: object, *, events: object) -> None:
        del events
        raise RuntimeStateCommitUnknownError("prepared operation is unresolved")

    original_rollback = broker._rollback_submission_locked

    def observe_rollback(*args: object, **kwargs: object) -> None:
        nonlocal rollback_called
        rollback_called = True
        original_rollback(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(broker._store, "save_with_events", fail_after_prepare)
    monkeypatch.setattr(broker, "_rollback_submission_locked", observe_rollback)
    try:
        with pytest.raises(RuntimeStateCommitUnknownError):
            broker.submit_prompt(
                thread.thread_id,
                "Keep the prepared operation authoritative",
                client_request_id="prepared-operation-unknown",
            )

        assert rollback_called is False
        assert "prepared-operation-unknown" in broker._state.request_idempotency
        assert any(
            run.client_request_id == "prepared-operation-unknown"
            for run in broker._state.runs.values()
        )
        assert broker.runtime_snapshot().active_turns == 0
        assert broker.runtime_snapshot().queued_prompts == 0
    finally:
        broker.close()


def test_interaction_persistence_failure_discards_private_provider_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Fail the interaction checkpoint",
            client_request_id="interaction-persistence-run",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))

        def fail_save(_state: object) -> None:
            raise RuntimeStateError("injected interaction checkpoint failure")

        monkeypatch.setattr(broker._store, "save", fail_save)
        with pytest.raises(RuntimeStateError):
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "interaction-persistence-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-interaction-persistence",
            )

        assert broker.pending_interactions(thread.thread_id) == ()
        assert broker._server_requests == {}
        interaction = next(iter(broker._state.interactions.values()))
        assert interaction.status == "expired"
        assert interaction.display is None
        assert client.discarded == [("provider-interaction-persistence", 1)]
        assert client.aborted_generations == [1]
        assert broker.runtime_snapshot().active_turns == 0
        with pytest.raises(RuntimeBrokerError) as fatal:
            broker.submit_prompt(
                thread.thread_id,
                "Broker remains unavailable",
                client_request_id="interaction-persistence-retry",
            )
        _assert_broker_error(fatal, "app_server_unavailable")
    finally:
        broker.close()


def test_concurrent_duplicate_prompt_has_one_owner_and_payload_is_bound(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    barrier = Barrier(8)

    def submit() -> Any:
        barrier.wait()
        return broker.submit_prompt(
            thread.thread_id,
            "One bounded request",
            client_request_id="concurrent-request",
        )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            runs = list(executor.map(lambda _index: submit(), range(8)))

        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        assert {run.run_id for run in runs} == {runs[0].run_id}
        assert len(_requests(client, "thread/start")) == 1
        with pytest.raises(RuntimeBrokerError) as conflict:
            broker.submit_prompt(
                thread.thread_id,
                "Different input",
                client_request_id="concurrent-request",
            )
        _assert_broker_error(conflict, "runtime_request_conflict")
    finally:
        broker.close()


def test_notifications_preserve_repeated_deltas_and_terminal_replays_do_not_regress(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Stream",
            client_request_id="client-stream",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "agent-message-1",
                "delta": "Hello",
            },
        )
        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "agent-message-1",
                "delta": "Hello",
            },
        )
        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "agent-message-1",
                "delta": "a\n  b",
            },
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        client.emit_notification(
            "turn/started",
            {"threadId": remote_thread_id, "turn": _turn(turn_id)},
        )
        _wait_until(lambda: storage.load_thread(thread.thread_id).status == "idle")

        events = storage.list_thread_events(thread.thread_id)
        deltas = [event for event in events if event.event_type == "message.delta"]
        terminals = [event for event in events if event.event_type == "run.completed"]
        assert [event.payload["text"] for event in deltas] == [
            "Hello",
            "Hello",
            "a\n  b",
        ]
        assert len(terminals) == 1
        assert storage.load_thread(thread.thread_id).active_run_id is None
    finally:
        broker.close()


@pytest.mark.parametrize("seed", range(8))
def test_seeded_reordered_notifications_release_once_without_cross_turn_state(
    tmp_path: Path,
    seed: int,
) -> None:
    storage, thread = _storage_and_thread(tmp_path / str(seed))
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, cancel_grace_seconds=1.0)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Permutation safety",
            client_request_id=f"permutation-{seed}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        broker.cancel_run(thread.thread_id, run_id=run.run_id)

        current_item = {
            "threadId": remote_thread_id,
            "turnId": turn_id,
            "completedAtMs": 1_783_936_800_000,
            "item": {
                "id": "permuted-item",
                "type": "agentMessage",
                "text": "safe current output",
            },
        }
        wrong_item = {
            "threadId": remote_thread_id,
            "turnId": f"wrong-{turn_id}",
            "completedAtMs": 1_783_936_800_000,
            "item": {
                "id": "wrong-turn-item",
                "type": "agentMessage",
                "text": "must not project",
            },
        }
        actions: list[tuple[str, dict[str, Any], int]] = [
            (
                "turn/started",
                {"threadId": remote_thread_id, "turn": _turn(turn_id)},
                client.generation,
            ),
            ("item/completed", current_item, client.generation),
            ("item/completed", current_item, client.generation),
            ("item/completed", wrong_item, client.generation),
            ("item/completed", current_item, client.generation + 99),
            (
                "turn/completed",
                {
                    "threadId": remote_thread_id,
                    "turn": _turn(turn_id, status="interrupted"),
                },
                client.generation,
            ),
            (
                "turn/completed",
                {
                    "threadId": remote_thread_id,
                    "turn": _turn(turn_id, status="interrupted"),
                },
                client.generation,
            ),
        ]
        random.Random(seed).shuffle(actions)
        for method, params, generation in actions:
            client.emit_notification(method, params, generation=generation)

        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        events = storage.list_thread_events(thread.thread_id)
        terminals = [event for event in events if event.event_type == "run.cancelled"]
        projected_items = [
            event for event in events if event.event_type == "message.completed"
        ]
        assert len(terminals) == 1
        assert len(projected_items) <= 1
        assert all(
            event.payload.get("item_id") != "wrong-turn-item" for event in events
        )
        assert broker.runtime_snapshot().queued_prompts == 0
        record = storage.load_thread(thread.thread_id)
        assert record.active_run_id is None
        assert record.active_turn_id is None
    finally:
        broker.close()


def test_late_previous_turn_notifications_cannot_bind_a_resumed_run(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = BlockingTurnStartAppServer()
    broker = _broker(storage, client)
    try:
        first = broker.submit_prompt(
            thread.thread_id,
            "First turn",
            client_request_id="cross-turn-first",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        first_run_id, remote_thread_id, old_turn_id = _active_ids(
            storage,
            thread.thread_id,
        )
        assert first_run_id == first.run_id
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=old_turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

        client.block_next_turn_start = True
        second = broker.submit_prompt(
            thread.thread_id,
            "Second turn",
            client_request_id="cross-turn-second",
        )
        assert client.turn_start_entered.wait(1)

        client.emit_notification(
            "turn/started",
            {"threadId": remote_thread_id, "turn": _turn(old_turn_id)},
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=old_turn_id,
        )

        assert broker.runtime_snapshot().active_turns == 1
        assert storage.load_thread(thread.thread_id).active_run_id == second.run_id
        assert (
            len(
                [
                    event
                    for event in storage.list_thread_events(thread.thread_id)
                    if event.event_type == "run.completed"
                ]
            )
            == 1
        )

        new_turn_id = "codex-turn-2"
        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": new_turn_id,
                "itemId": "early-current-message",
                "delta": "Buffered current output",
            },
        )
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=new_turn_id,
        )

        client.release_turn_start.set()
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        _wait_until(
            lambda: (
                len(
                    [
                        event
                        for event in storage.list_thread_events(thread.thread_id)
                        if event.event_type == "run.completed"
                    ]
                )
                == 2
            )
        )
        events = storage.list_thread_events(thread.thread_id)
        second_started = [
            event
            for event in events
            if event.event_type == "run.started"
            and event.payload.get("run_id") == second.run_id
        ]
        assert len(second_started) == 1
        assert second_started[0].payload["turn_id"] == new_turn_id
        assert any(
            event.event_type == "message.delta"
            and event.payload.get("text") == "Buffered current output"
            for event in events
        )
        assert (
            len([event for event in events if event.event_type == "run.completed"]) == 2
        )
    finally:
        client.release_turn_start.set()
        broker.close()


def test_pre_response_requests_are_replayed_only_for_the_validated_turn(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = BlockingTurnStartAppServer()
    client.block_next_turn_start = True
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Buffer approval until the turn is identified",
            client_request_id="pre-response-approval-run",
        )
        assert client.turn_start_entered.wait(1)
        record = storage.load_thread(thread.thread_id)
        assert record.codex_thread_id is not None
        remote_thread_id = record.codex_thread_id
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))

        def request_params(turn_id: str, item_id: str) -> dict[str, Any]:
            return {
                "command": "python -m pytest -q",
                "commandActions": [
                    {
                        "type": "listFiles",
                        "command": "python -m pytest -q",
                        "path": workspace,
                    }
                ],
                "cwd": workspace,
                "itemId": item_id,
                "startedAtMs": 1_783_936_800_000,
                "threadId": remote_thread_id,
                "turnId": turn_id,
            }

        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                request_params("codex-turn-stale", "stale-item"),
                request_id="provider-pre-response-stale",
            )
            is DEFERRED_RESPONSE
        )
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                request_params("codex-turn-1", "current-item"),
                request_id="provider-pre-response-current",
            )
            is DEFERRED_RESPONSE
        )
        assert broker.pending_interactions(thread.thread_id) == ()

        client.release_turn_start.set()
        _wait_until(lambda: len(broker.pending_interactions(thread.thread_id)) == 1)
        pending = _pending_one(broker, thread.thread_id)
        _wait_until(
            lambda: any(
                response[0].request_id == "provider-pre-response-stale"
                for response in client.responses
            )
        )
        stale_response = next(
            response
            for response in client.responses
            if response[0].request_id == "provider-pre-response-stale"
        )
        assert stale_response[1] == {"decision": "decline"}
        assert pending["item_id"] == "current-item"

        decided = broker.decide_approval(
            thread_id=thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=run.run_id,
            turn_id="codex-turn-1",
            item_id="current-item",
            decision="decline",
            client_request_id="pre-response-current-decision",
        )
        assert decided.status == "declined"
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id="codex-turn-1",
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        client.release_turn_start.set()
        broker.close()


def test_pre_response_request_resolution_replays_after_its_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = BlockingTurnStartAppServer()
    client.block_next_turn_start = True
    broker = _broker(storage, client)
    replay_entered = Event()
    release_replay = Event()
    original_replay = broker._replay_pre_response_callbacks

    def paused_replay(*args: Any, **kwargs: Any) -> None:
        replay_entered.set()
        assert release_replay.wait(2)
        original_replay(*args, **kwargs)

    monkeypatch.setattr(broker, "_replay_pre_response_callbacks", paused_replay)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Resolve an early approval in order",
            client_request_id="pre-response-resolution-run",
        )
        assert client.turn_start_entered.wait(1)
        record = storage.load_thread(thread.thread_id)
        assert record.codex_thread_id is not None
        remote_thread_id = record.codex_thread_id
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))

        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "pre-response-resolved-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": "codex-turn-1",
                },
                request_id="provider-pre-response-resolved",
            )
            is DEFERRED_RESPONSE
        )
        client.release_turn_start.set()
        assert replay_entered.wait(1)
        client.emit_notification(
            "serverRequest/resolved",
            {
                "requestId": "provider-pre-response-resolved",
                "threadId": remote_thread_id,
            },
        )
        assert broker.pending_interactions(thread.thread_id) == ()

        release_replay.set()
        _wait_until(
            lambda: (
                client.discarded
                == [("provider-pre-response-resolved", client.generation)]
            )
        )
        assert broker.pending_interactions(thread.thread_id) == ()
        interaction_events = [
            event.event_type
            for event in storage.list_thread_events(thread.thread_id)
            if event.event_type.startswith("interaction.")
        ]
        assert interaction_events == ["interaction.created", "interaction.expired"]
        assert client.responses == []
        resolved_event = next(
            event
            for event in storage.event_store.replay(
                after_cursor=0,
                scopes=("thread",),
                thread_ids=(thread.thread_id,),
            ).events
            if event.event_type == "interaction.expired"
        )
        runtime_state = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert resolved_event.operation_id is not None
        assert runtime_state["_bridge_operation"]["operation_id"] == (
            resolved_event.operation_id
        )

        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id="codex-turn-1",
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        client.release_turn_start.set()
        release_replay.set()
        broker.close()


def test_callbacks_arriving_during_pre_response_replay_preserve_fifo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = BlockingTurnStartAppServer()
    client.block_next_turn_start = True
    broker = _broker(storage, client)
    replay_entered = Event()
    release_replay = Event()
    original_replay = broker._replay_pre_response_callbacks

    def paused_replay(*args: Any, **kwargs: Any) -> None:
        replay_entered.set()
        assert release_replay.wait(2)
        original_replay(*args, **kwargs)

    monkeypatch.setattr(broker, "_replay_pre_response_callbacks", paused_replay)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Preserve callback order",
            client_request_id="pre-response-callback-fifo",
        )
        assert client.turn_start_entered.wait(1)
        record = storage.load_thread(thread.thread_id)
        assert record.codex_thread_id is not None
        remote_thread_id = record.codex_thread_id

        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": "codex-turn-1",
                "itemId": "ordered-message",
                "delta": "A",
            },
        )
        client.release_turn_start.set()
        assert replay_entered.wait(1)

        client.emit_notification(
            "item/agentMessage/delta",
            {
                "threadId": remote_thread_id,
                "turnId": "codex-turn-1",
                "itemId": "ordered-message",
                "delta": "B",
            },
        )
        release_replay.set()
        _wait_until(
            lambda: (
                len(
                    [
                        event
                        for event in storage.list_thread_events(thread.thread_id)
                        if event.event_type == "message.delta"
                    ]
                )
                == 2
            )
        )

        deltas = [
            event.payload["text"]
            for event in storage.list_thread_events(thread.thread_id)
            if event.event_type == "message.delta"
        ]
        assert deltas == ["A", "B"]
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id="codex-turn-1",
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        client.release_turn_start.set()
        release_replay.set()
        broker.close()


def test_generation_change_interrupts_active_run_and_clears_queued_prompts(
    tmp_path: Path,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    second_thread = _new_thread(storage, tmp_path, name="Queued")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            first_thread.thread_id,
            "Active",
            client_request_id="client-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        broker.submit_prompt(
            second_thread.thread_id,
            "Queued",
            client_request_id="client-queued",
        )
        assert storage.load_thread(second_thread.thread_id).status == "queued"

        client.generation = 2

        _wait_until(
            lambda: storage.load_thread(first_thread.thread_id).status == "error"
        )
        _wait_until(
            lambda: storage.load_thread(second_thread.thread_id).status == "error"
        )
        assert broker.runtime_snapshot().active_turns == 0
        assert broker.runtime_snapshot().queued_prompts == 0
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(first_thread.thread_id)
                + storage.list_thread_events(second_thread.thread_id)
            ]
        )
        assert "run.interrupted" in serialized
        assert "run.queue_cleared" in serialized
        queued_events = storage.event_store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=(second_thread.thread_id,),
        ).events
        queue_cleared = next(
            event for event in queued_events if event.event_type == "run.queue_cleared"
        )
        interrupted = next(
            event for event in queued_events if event.event_type == "run.interrupted"
        )
        assert queue_cleared.operation_id is not None
        assert queue_cleared.operation_id == interrupted.operation_id
    finally:
        broker.close()


def test_unknown_turn_start_outcome_aborts_generation_before_releasing_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    second_thread = _new_thread(storage, tmp_path, name="UnknownTurnQueued")
    client = ValidatorBackedAppServer()
    entered = Event()
    release = Event()
    original_request = client.request

    def uncertain_request(
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if method == "turn/start":
            with client._lock:
                client.requests.append((method, deepcopy(params)))
            entered.set()
            assert release.wait(2)
            raise AppServerTimeoutError(method)
        return original_request(
            method,
            params,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(client, "request", uncertain_request)
    broker = _broker(storage, client)
    try:
        first = broker.submit_prompt(
            first_thread.thread_id,
            "Outcome may be unknown",
            client_request_id="unknown-turn-active",
        )
        assert entered.wait(1)
        queued = broker.submit_prompt(
            second_thread.thread_id,
            "Must never be promoted",
            client_request_id="unknown-turn-queued",
        )
        assert queued.status == "queued"
        assert broker.runtime_snapshot().active_turns == 1
        assert broker.runtime_snapshot().queued_prompts == 1

        release.set()
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        assert broker.runtime_snapshot().queued_prompts == 0
        assert client.aborted_generations == [1]
        assert len(_requests(client, "turn/start")) == 1
        _wait_until(
            lambda: (
                storage.load_thread(first.thread_id).status == "error"
                and storage.load_thread(queued.thread_id).status == "error"
            )
        )
        assert storage.load_thread(first.thread_id).status == "error"
        assert storage.load_thread(queued.thread_id).status == "error"
        assert any(
            event.event_type == "run.queue_cleared"
            for event in storage.list_thread_events(queued.thread_id)
        )
    finally:
        release.set()
        broker.close()


def test_cancel_during_blocked_turn_start_aborts_generation_and_queue(
    tmp_path: Path,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="CancelStartingQueued")
    client = BlockingTurnStartAppServer()
    client.block_next_turn_start = True
    broker = _broker(storage, client)
    try:
        active = broker.submit_prompt(
            first_thread.thread_id,
            "Cancel while turn start is in flight",
            client_request_id="cancel-starting-active",
        )
        assert client.turn_start_entered.wait(1)
        queued = broker.submit_prompt(
            queued_thread.thread_id,
            "Must not start after an uncertain cancellation",
            client_request_id="cancel-starting-queued",
        )
        assert queued.status == "queued"

        cancelled = broker.cancel_run(first_thread.thread_id, run_id=active.run_id)
        assert cancelled.status == "cancelled"
        assert client.aborted_generations == [1]
        assert broker.runtime_snapshot().queued_prompts == 0

        client.release_turn_start.set()
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        _wait_until(
            lambda: storage.load_thread(first_thread.thread_id).status == "idle"
        )
        _wait_until(
            lambda: storage.load_thread(queued_thread.thread_id).status == "error"
        )
        _wait_until(
            lambda: len(_requests(client, "turn/start")) == 1,
            message="released turn/start request was not recorded",
        )
        assert any(
            event.event_type == "run.cancelled"
            for event in storage.list_thread_events(first_thread.thread_id)
        )
        assert any(
            event.event_type == "run.queue_cleared"
            for event in storage.list_thread_events(queued_thread.thread_id)
        )
    finally:
        client.release_turn_start.set()
        broker.close()


def test_cancel_watchdog_aborts_only_the_matching_generation(tmp_path: Path) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, cancel_grace_seconds=0.03)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Wait",
            client_request_id="client-cancel-watchdog",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        broker.cancel_run(thread.thread_id, run_id=run.run_id)

        _wait_until(lambda: client.aborted_generations == [1])
        _wait_until(lambda: storage.load_thread(thread.thread_id).status == "idle")
        assert broker.runtime_snapshot().active_turns == 0
        assert any(
            event.event_type == "run.cancelled"
            for event in storage.list_thread_events(thread.thread_id)
        )
    finally:
        broker.close()


def test_remote_failure_has_no_cli_fallback_and_never_persists_raw_cause(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    raw = RuntimeError(
        "Authorization: Bearer reusable-secret private@example.test /data/codex-home/auth.json"
    )
    rejected = AppServerRemoteError(method="thread/start", code=-32042)
    rejected.__cause__ = raw
    client.script("thread/start", rejected)
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Must stay on app-server",
            client_request_id="client-no-fallback",
        )
        _wait_until(
            lambda: any(
                event.event_type == "run.failed"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )

        assert [method for method, _params in client.requests] == ["thread/start"]
        record = storage.load_thread(thread.thread_id)
        events = storage.list_thread_events(thread.thread_id)
        serialized = json.dumps(
            {
                "thread": record.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
            }
        )
        assert "reusable-secret" not in serialized
        assert "private@example.test" not in serialized
        assert "/data/codex-home" not in serialized
        assert any(event.event_type == "run.failed" for event in events)
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("method", "params", "expected_kind"),
    [
        (
            "item/commandExecution/requestApproval",
            {
                "command": "python -m pytest -q",
                "commandActions": [
                    {
                        "type": "listFiles",
                        "command": "python -m pytest -q",
                        "path": "__WORKSPACE__",
                    }
                ],
                "cwd": "__WORKSPACE__",
                "itemId": "command-item-1",
                "startedAtMs": 1_783_936_800_000,
                "threadId": "__REMOTE_THREAD__",
                "turnId": "__TURN__",
            },
            "command_approval",
        ),
        (
            "item/fileChange/requestApproval",
            {
                "itemId": "file-item-1",
                "reason": "Update files inside the workspace",
                "startedAtMs": 1_783_936_800_000,
                "threadId": "__REMOTE_THREAD__",
                "turnId": "__TURN__",
            },
            "file_change_approval",
        ),
    ],
)
def test_command_and_file_approvals_defer_then_respond_idempotently(
    tmp_path: Path,
    method: str,
    params: dict[str, Any],
    expected_kind: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Approve safely",
            client_request_id="client-approval-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        params = deepcopy(params)
        params["threadId"] = remote_thread_id
        params["turnId"] = turn_id
        if params.get("cwd") == "__WORKSPACE__":
            params["cwd"] = str(storage.resolve_workspace_path(thread.workspace_path))
        for action in params.get("commandActions", []):
            if action.get("path") == "__WORKSPACE__":
                action["path"] = str(
                    storage.resolve_workspace_path(thread.workspace_path)
                )
        if method == "item/fileChange/requestApproval":
            client.emit_notification(
                "item/fileChange/patchUpdated",
                {
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                    "itemId": params["itemId"],
                    "changes": [
                        {
                            "path": "src/app.py",
                            "diff": "@@ -1 +1 @@\n-old\n+new\n",
                            "kind": {"type": "update", "move_path": None},
                        }
                    ],
                },
            )

        callback_result = client.emit_request(method, params)

        assert callback_result is DEFERRED_RESPONSE
        pending = _pending_one(broker, thread.thread_id)
        assert pending["kind"] == expected_kind
        assert pending["thread_id"] == thread.thread_id
        assert pending["turn_id"] == turn_id
        assert pending["item_id"] == params["itemId"]
        safe_pending = json.dumps(pending)
        assert "provider-request-private-1" not in safe_pending
        assert (
            str(storage.resolve_workspace_path(thread.workspace_path))
            not in safe_pending
        )
        created_event = next(
            event
            for event in reversed(
                storage.event_store.replay(
                    after_cursor=0,
                    scopes=("thread",),
                    thread_ids=(thread.thread_id,),
                ).events
            )
            if event.event_type == "interaction.created"
            and event.payload.get("interaction_id") == pending["interaction_id"]
        )
        assert created_event.operation_id is not None
        assert created_event.scope_sequence == pending["event_id"]

        kwargs = {
            "thread_id": thread.thread_id,
            "interaction_id": pending["interaction_id"],
            "run_id": run.run_id,
            "turn_id": turn_id,
            "item_id": params["itemId"],
            "decision": "accept",
            "client_request_id": "client-decision-1",
        }
        first = broker.decide_approval(**kwargs)
        duplicate = broker.decide_approval(**kwargs)
        _wait_until(lambda: len(client.responses) == 1)

        assert duplicate == first
        assert client.responses[0][1] == {"decision": "accept"}
        assert client.responses[0][2] is None
        resolved_event = next(
            event
            for event in reversed(
                storage.event_store.replay(
                    after_cursor=created_event.cursor,
                    scopes=("thread",),
                    thread_ids=(thread.thread_id,),
                ).events
            )
            if event.event_type == "interaction.resolved"
            and event.payload.get("interaction_id") == pending["interaction_id"]
        )
        assert resolved_event.operation_id is not None

        with pytest.raises(RuntimeBrokerError) as changed_replay:
            broker.decide_approval(**{**kwargs, "decision": "decline"})
        _assert_broker_error(changed_replay, "runtime_request_conflict")

        with pytest.raises(RuntimeBrokerError) as mismatch:
            broker.decide_approval(**{**kwargs, "thread_id": "other-thread"})
        _assert_broker_error(mismatch, "turn_changed")
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("method", "params", "expected"),
    [
        (
            "item/commandExecution/requestApproval",
            {
                "command": "python -m pytest -q",
                "commandActions": [],
                "cwd": "__WORKSPACE__",
                "itemId": "scheduled-command",
                "startedAtMs": 1_783_936_800_000,
            },
            {"decision": "decline"},
        ),
        (
            "item/fileChange/requestApproval",
            {
                "itemId": "scheduled-file",
                "reason": "Update the workspace",
                "startedAtMs": 1_783_936_800_000,
            },
            {"decision": "decline"},
        ),
        (
            "item/tool/requestUserInput",
            {
                "itemId": "scheduled-question",
                "questions": [
                    {
                        "id": "scope",
                        "header": "Scope",
                        "question": "Which scope should be used?",
                        "options": [],
                        "isOther": True,
                        "isSecret": False,
                    }
                ],
            },
            {"answers": {"scope": {"answers": []}}},
        ),
    ],
)
def test_unattended_runs_fail_closed_without_pending_interactions(
    tmp_path: Path,
    method: str,
    params: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Run without an administrator present",
            client_request_id=f"unattended-{method}",
            unattended=True,
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(
            storage,
            thread.thread_id,
        )
        payload = deepcopy(params)
        payload["threadId"] = remote_thread_id
        payload["turnId"] = turn_id
        if payload.get("cwd") == "__WORKSPACE__":
            payload["cwd"] = str(storage.resolve_workspace_path(thread.workspace_path))

        result = client.emit_request(method, payload)

        assert result == expected
        assert broker.pending_interactions(thread.thread_id) == ()
        assert all(
            event.event_type != "interaction.created"
            for event in storage.list_thread_events(thread.thread_id)
        )
    finally:
        broker.close()


def test_expired_interaction_aborts_generation_and_releases_runtime(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="TimeoutQueued")
    client = ValidatorBackedAppServer()
    broker = _broker(
        storage,
        client,
        queue_wait_timeout_seconds=10.0,
        turn_timeout_seconds=15.0,
        # Leave enough time to enqueue the follow-up even on loaded CI hosts;
        # this test owns expiry behavior after the queue state is established.
        interaction_timeout_seconds=5.0,
    )
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Wait for approval",
            client_request_id="interaction-timeout-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "timeout-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-timeout",
            )
            is DEFERRED_RESPONSE
        )
        queued = broker.submit_prompt(
            queued_thread.thread_id,
            "Must not start after timeout",
            client_request_id="interaction-timeout-queued",
        )
        assert queued.status == "queued"

        _wait_until(
            lambda: client.aborted_generations == [1],
            timeout=7.0,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        assert broker.runtime_snapshot().queued_prompts == 0
        assert client.discarded == [("provider-timeout", 1)]
        assert client.responses == []
        assert len(_requests(client, "turn/start")) == 1
        assert storage.load_thread(thread.thread_id).status == "error"
        _wait_until(
            lambda: storage.load_thread(queued_thread.thread_id).status == "error",
            timeout=5.0,
            message="queued thread did not publish its interrupted projection",
        )
        _wait_until(
            lambda: any(
                event.event_type == "run.failed"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )
        events = storage.list_thread_events(thread.thread_id)
        expired_events = [
            event for event in events if event.event_type == "interaction.expired"
        ]
        assert len(expired_events) == 1
        stored_expired = next(
            event
            for event in storage.event_store.replay(
                after_cursor=0,
                scopes=("thread",),
                thread_ids=(thread.thread_id,),
            ).events
            if event.event_id == expired_events[0].event_id
        )
        assert stored_expired.operation_id is not None
        assert len([event for event in events if event.event_type == "run.failed"]) == 1
        replay = broker.submit_prompt(
            thread.thread_id,
            "Wait for approval",
            client_request_id="interaction-timeout-active",
        )
        assert replay.run_id == run.run_id
        assert replay.status == "failed"
    finally:
        broker.close()


def test_pending_interaction_refreshes_idle_deadline_for_user_response(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    limits = ResourceLimits(run_idle_timeout_seconds=0.8)
    broker = RuntimeBroker(
        storage=storage,
        app_server=client,
        runtime_gate=RuntimeGate(limits=limits),
        resource_limits=limits,
        queue_wait_timeout_seconds=2.0,
        watchdog_interval_seconds=0.01,
        turn_timeout_seconds=5.0,
        cancel_grace_seconds=0.05,
        interaction_timeout_seconds=2.0,
    )
    broker.start()
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Ask after some work",
            client_request_id="interaction-idle-refresh",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        time.sleep(0.35)
        assert (
            client.emit_request(
                "item/tool/requestUserInput",
                {
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                    "itemId": "idle-refresh-question",
                    "questions": [
                        {
                            "id": "scope",
                            "header": "Scope",
                            "question": "Which scope should be used?",
                            "options": [],
                            "isOther": True,
                            "isSecret": False,
                        }
                    ],
                },
                request_id="provider-idle-refresh",
            )
            is DEFERRED_RESPONSE
        )
        time.sleep(1.0)

        assert client.aborted_generations == []
        pending = _pending_one(broker, thread.thread_id)
        result = broker.answer_user_input(
            thread_id=thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=run.run_id,
            turn_id=turn_id,
            item_id="idle-refresh-question",
            answers={"scope": ["Source only"]},
            client_request_id="idle-refresh-answer",
        )
        assert result.status == "answered"
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        broker.close()


def test_cancel_approval_transitions_run_to_cancelled(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Cancel from approval",
            client_request_id="cancel-decision-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "cancel-decision-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-cancel-decision",
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        result = broker.decide_approval(
            thread_id=thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=run.run_id,
            turn_id=turn_id,
            item_id="cancel-decision-item",
            decision="cancel",
            client_request_id="cancel-decision-response",
        )
        assert result.status == "cancelled"
        assert client.responses[-1][1] == {"decision": "cancel"}

        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
            status="interrupted",
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        events = storage.list_thread_events(thread.thread_id)
        assert (
            len([event for event in events if event.event_type == "run.cancelled"]) == 1
        )
        assert not any(event.event_type == "run.interrupted" for event in events)
    finally:
        broker.close()


def test_unknown_cancel_response_still_activates_cancel_watchdog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, cancel_grace_seconds=0.03)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Cancel despite uncertain write",
            client_request_id="cancel-unknown-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "cancel-unknown-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-cancel-unknown",
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        def fail_response(*_args: object, **_kwargs: object) -> None:
            raise OSError("pipe failed")

        monkeypatch.setattr(client, "respond", fail_response)
        with pytest.raises(RuntimeBrokerError) as unknown:
            broker.decide_approval(
                thread_id=thread.thread_id,
                interaction_id=pending["interaction_id"],
                run_id=run.run_id,
                turn_id=turn_id,
                item_id="cancel-unknown-item",
                decision="cancel",
                client_request_id="cancel-unknown-response",
            )
        _assert_broker_error(unknown, "interaction_outcome_unknown")

        _wait_until(lambda: client.aborted_generations == [1])
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        _wait_until(
            lambda: any(
                event.event_type == "run.cancelled"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )
        assert any(
            event.event_type == "run.cancelled"
            for event in storage.list_thread_events(thread.thread_id)
        )
    finally:
        broker.close()


def test_provider_resolution_race_after_cancel_write_still_cancels_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, cancel_grace_seconds=0.03)
    original_respond = client.respond
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Cancel across provider resolution race",
            client_request_id="cancel-resolved-race-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "cancel-race-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-cancel-race",
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        def resolve_during_response(
            request: AppServerRequest,
            *,
            result: Any = None,
            error: AppServerResponseError | None = None,
        ) -> None:
            original_respond(request, result=result, error=error)
            client.emit_notification(
                "serverRequest/resolved",
                {
                    "requestId": request.request_id,
                    "threadId": remote_thread_id,
                },
            )

        monkeypatch.setattr(client, "respond", resolve_during_response)
        with pytest.raises(RuntimeBrokerError) as unknown:
            broker.decide_approval(
                thread_id=thread.thread_id,
                interaction_id=pending["interaction_id"],
                run_id=run.run_id,
                turn_id=turn_id,
                item_id="cancel-race-item",
                decision="cancel",
                client_request_id="cancel-race-response",
            )
        _assert_broker_error(unknown, "interaction_outcome_unknown")

        _wait_until(lambda: client.aborted_generations == [1])
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
        _wait_until(
            lambda: any(
                event.event_type == "run.cancelled"
                for event in storage.list_thread_events(thread.thread_id)
            )
        )
        assert any(
            event.event_type == "run.cancelled"
            for event in storage.list_thread_events(thread.thread_id)
        )
    finally:
        broker.close()


@pytest.mark.parametrize("unsafe_name", ["bad:name", "bad\x01name", "CON.txt"])
def test_nonportable_contained_command_path_is_denied_without_fatal_state(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject unsafe path",
            client_request_id=f"unsafe-command-{unsafe_name.encode().hex()}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = storage.resolve_workspace_path(thread.workspace_path)

        result = client.emit_request(
            "item/commandExecution/requestApproval",
            {
                "command": "python -m pytest -q",
                "commandActions": [
                    {
                        "type": "listFiles",
                        "command": "python -m pytest -q",
                        "path": str(workspace / unsafe_name),
                    }
                ],
                "cwd": str(workspace),
                "itemId": "unsafe-command-item",
                "startedAtMs": 1_783_936_800_000,
                "threadId": remote_thread_id,
                "turnId": turn_id,
            },
            request_id="provider-unsafe-command",
        )

        assert result == {"decision": "decline"}
        assert broker.pending_interactions(thread.thread_id) == ()
        assert broker.runtime_snapshot().active_turns == 1
        RuntimeStateStore(storage.root).load()
        _complete(
            client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)
    finally:
        broker.close()


@pytest.mark.parametrize(
    "command_actions",
    [None, [], [{"type": "unknown", "command": "opaque-provider-command"}]],
    ids=["missing", "empty", "unknown"],
)
def test_opaque_command_actions_are_automatically_denied(
    tmp_path: Path,
    command_actions: object,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject opaque command",
            client_request_id=f"opaque-command-{type(command_actions).__name__}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        params: dict[str, Any] = {
            "command": "opaque-provider-command",
            "cwd": workspace,
            "itemId": "opaque-command-item",
            "startedAtMs": 1_783_936_800_000,
            "threadId": remote_thread_id,
            "turnId": turn_id,
        }
        if command_actions is not None:
            params["commandActions"] = command_actions

        assert client.emit_request(
            "item/commandExecution/requestApproval",
            params,
            request_id="provider-opaque-command",
        ) == {"decision": "decline"}
        assert broker.pending_interactions(thread.thread_id) == ()
        RuntimeStateStore(storage.root).load()
    finally:
        broker.close()


@pytest.mark.parametrize("path_field", ["command", "reason"])
def test_absolute_path_text_is_not_projected_in_command_approval(
    tmp_path: Path,
    path_field: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject private path projection",
            client_request_id=f"absolute-path-{path_field}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        params: dict[str, Any] = {
            "command": "python -m pytest -q",
            "commandActions": [
                {
                    "type": "listFiles",
                    "command": "python -m pytest -q",
                    "path": workspace,
                }
            ],
            "cwd": workspace,
            "itemId": "absolute-path-item",
            "startedAtMs": 1_783_936_800_000,
            "threadId": remote_thread_id,
            "turnId": turn_id,
        }
        private_text = f"Inspect {workspace}/private.txt"
        params[path_field] = private_text

        assert client.emit_request(
            "item/commandExecution/requestApproval",
            params,
            request_id="provider-absolute-path",
        ) == {"decision": "decline"}
        assert broker.pending_interactions(thread.thread_id) == ()
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert private_text not in serialized
    finally:
        broker.close()


def test_absolute_path_reason_is_not_projected_in_file_change_approval(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject private file-change reason",
            client_request_id="absolute-file-change-reason",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        client.emit_notification(
            "item/fileChange/patchUpdated",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "absolute-file-change-item",
                "changes": [
                    {
                        "path": "src/app.py",
                        "diff": "@@ -1 +1 @@\n-old\n+new\n",
                        "kind": {"type": "update", "move_path": None},
                    }
                ],
            },
        )

        assert client.emit_request(
            "item/fileChange/requestApproval",
            {
                "itemId": "absolute-file-change-item",
                "reason": f"Inspect {workspace}/private.txt",
                "startedAtMs": 1_783_936_800_000,
                "threadId": remote_thread_id,
                "turnId": turn_id,
            },
            request_id="provider-absolute-file-change-reason",
        ) == {"decision": "decline"}
        assert broker.pending_interactions(thread.thread_id) == ()
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert f"Inspect {workspace}/private.txt" not in serialized
    finally:
        broker.close()


def test_large_valid_patch_is_split_into_bounded_durable_events(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Process a large patch",
            client_request_id="large-patch-run",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        client.emit_notification(
            "item/fileChange/patchUpdated",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "large-patch-item",
                "changes": [
                    {
                        "path": f"src/file-{index}.py",
                        "diff": "x" * (64 * 1024),
                        "kind": {"type": "update", "move_path": None},
                    }
                    for index in range(17)
                ],
            },
        )

        patch_events = [
            event
            for event in storage.list_thread_events(thread.thread_id)
            if event.event_type == "patch.updated"
        ]
        assert len(patch_events) > 1
        assert [event.payload["chunk_index"] for event in patch_events] == list(
            range(len(patch_events))
        )
        assert all(
            event.payload["chunk_count"] == len(patch_events) for event in patch_events
        )
        assert sum(len(event.payload["changes"]) for event in patch_events) == 17
        assert all(
            len(
                json.dumps(
                    event.payload,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            <= storage.event_store.max_event_payload_bytes
            for event in patch_events
        )
        assert broker.submit_prompt(
            thread.thread_id,
            "Steer after the split patch",
            client_request_id="after-large-patch",
        ).status in {"starting", "running"}
    finally:
        broker.close()


def test_nonportable_patch_path_is_ignored_without_corrupting_checkpoint(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject unsafe patch",
            client_request_id="unsafe-patch-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        client.emit_notification(
            "item/fileChange/patchUpdated",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "unsafe-patch-item",
                "changes": [
                    {
                        "path": "bad:name",
                        "diff": "@@ -1 +1 @@\n-old\n+new\n",
                        "kind": {"type": "update", "move_path": None},
                    }
                ],
            },
        )
        result = client.emit_request(
            "item/fileChange/requestApproval",
            {
                "itemId": "unsafe-patch-item",
                "reason": "Unsafe portable path",
                "startedAtMs": 1_783_936_800_000,
                "threadId": remote_thread_id,
                "turnId": turn_id,
            },
            request_id="provider-unsafe-patch",
        )

        assert result == {"decision": "decline"}
        assert broker.pending_interactions(thread.thread_id) == ()
        RuntimeStateStore(storage.root).load()
        assert not any(
            event.event_type == "patch.updated"
            for event in storage.list_thread_events(thread.thread_id)
        )
    finally:
        broker.close()


def test_rename_approval_displays_source_and_destination_paths(tmp_path: Path) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Rename safely",
            client_request_id="rename-approval-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        client.emit_notification(
            "item/fileChange/patchUpdated",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "rename-item",
                "changes": [
                    {
                        "path": "src/old.py",
                        "diff": "",
                        "kind": {"type": "update", "move_path": "src/new.py"},
                    }
                ],
            },
        )
        assert (
            client.emit_request(
                "item/fileChange/requestApproval",
                {
                    "itemId": "rename-item",
                    "reason": "Rename a source file",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id="provider-rename",
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)
        assert pending["display"]["workspace_paths"] == [
            "src/old.py",
            "src/new.py",
        ]
        broker.decide_approval(
            thread_id=thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=run.run_id,
            turn_id=turn_id,
            item_id="rename-item",
            decision="decline",
            client_request_id="rename-decline",
        )
    finally:
        broker.close()


def test_blocked_interaction_write_does_not_hold_broker_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    release = Event()
    entered = Event()
    original_respond = client.respond
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Approve without blocking runtime state",
            client_request_id="client-nonblocking-response",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "Get-ChildItem",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "Get-ChildItem",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "blocked-write-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        def blocked_respond(
            request: AppServerRequest,
            *,
            result: Any = None,
            error: AppServerResponseError | None = None,
        ) -> None:
            entered.set()
            assert release.wait(2)
            original_respond(request, result=result, error=error)

        monkeypatch.setattr(client, "respond", blocked_respond)
        kwargs = {
            "interaction_id": pending["interaction_id"],
            "thread_id": thread.thread_id,
            "run_id": run.run_id,
            "turn_id": turn_id,
            "item_id": "blocked-write-item",
            "decision": "accept",
            "client_request_id": "client-blocked-decision",
        }
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(broker.decide_approval, **kwargs)
            assert entered.wait(1)

            started_at = time.monotonic()
            assert broker.pending_interactions(thread.thread_id) == ()
            assert time.monotonic() - started_at < 0.25
            with pytest.raises(RuntimeBrokerError) as retrying:
                broker.decide_approval(**kwargs)
            _assert_broker_error(retrying, "interaction_outcome_unknown")

            broker.cancel_run(thread.thread_id, run_id=run.run_id)
            release.set()
            with pytest.raises(RuntimeBrokerError) as cancelled_write:
                future.result(timeout=1)
            _assert_broker_error(cancelled_write, "interaction_outcome_unknown")
        assert len(client.responses) == 1
        checkpoint = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert (
            checkpoint["interactions"][pending["interaction_id"]]["status"]
            == "outcome_unknown"
        )
    finally:
        release.set()
        broker.close()


@pytest.mark.parametrize("response_kind", ["approval", "question"])
def test_thread_delete_waits_for_inflight_interaction_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response_kind: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    entered = Event()
    release = Event()
    original_respond = client.respond
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Resolve without orphaning events",
            client_request_id=f"delete-{response_kind}-active",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        if response_kind == "approval":
            workspace = str(storage.resolve_workspace_path(thread.workspace_path))
            assert (
                client.emit_request(
                    "item/commandExecution/requestApproval",
                    {
                        "command": "Get-ChildItem",
                        "commandActions": [
                            {
                                "type": "listFiles",
                                "command": "Get-ChildItem",
                                "path": workspace,
                            }
                        ],
                        "cwd": workspace,
                        "itemId": "delete-approval-item",
                        "startedAtMs": 1_783_936_800_000,
                        "threadId": remote_thread_id,
                        "turnId": turn_id,
                    },
                )
                is DEFERRED_RESPONSE
            )
        else:
            assert (
                client.emit_request(
                    "item/tool/requestUserInput",
                    {
                        "threadId": remote_thread_id,
                        "turnId": turn_id,
                        "itemId": "delete-question-item",
                        "questions": [
                            {
                                "id": "scope",
                                "header": "Scope",
                                "question": "Which scope should be used?",
                                "options": [
                                    {
                                        "label": "Source",
                                        "description": "Use source only.",
                                    }
                                ],
                                "isOther": False,
                                "isSecret": False,
                            }
                        ],
                    },
                )
                is DEFERRED_RESPONSE
            )
        pending = _pending_one(broker, thread.thread_id)
        event_path = storage._event_log_path(thread.thread_id)

        def blocked_respond(
            request: AppServerRequest,
            *,
            result: Any = None,
            error: AppServerResponseError | None = None,
        ) -> None:
            entered.set()
            assert release.wait(2)
            original_respond(request, result=result, error=error)

        monkeypatch.setattr(client, "respond", blocked_respond)

        def respond() -> Any:
            if response_kind == "approval":
                return broker.decide_approval(
                    thread_id=thread.thread_id,
                    interaction_id=pending["interaction_id"],
                    run_id=run.run_id,
                    turn_id=turn_id,
                    item_id="delete-approval-item",
                    decision="accept",
                    client_request_id="delete-approval-response",
                )
            return broker.answer_user_input(
                thread_id=thread.thread_id,
                interaction_id=pending["interaction_id"],
                run_id=run.run_id,
                turn_id=turn_id,
                item_id="delete-question-item",
                answers={"scope": ["Source"]},
                client_request_id="delete-question-response",
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(respond)
            assert entered.wait(1)
            _complete(
                client,
                remote_thread_id=remote_thread_id,
                turn_id=turn_id,
            )
            _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

            with pytest.raises(RuntimeBrokerError) as busy:
                broker.delete_thread(thread.thread_id)
            _assert_broker_error(busy, "runtime_thread_busy")

            release.set()
            with pytest.raises(RuntimeBrokerError) as unknown:
                future.result(timeout=1)
            _assert_broker_error(unknown, "interaction_outcome_unknown")

        broker.delete_thread(thread.thread_id)
        assert not event_path.exists()
        time.sleep(0.05)
        assert not event_path.exists()
    finally:
        release.set()
        broker.close()


def test_failed_interaction_write_is_persisted_as_outcome_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Approval outcome",
            client_request_id="client-response-failure",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "Get-ChildItem",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "Get-ChildItem",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": "failed-write-item",
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)
        calls = 0

        def fail_respond(*_args: object, **_kwargs: object) -> None:
            nonlocal calls
            calls += 1
            raise OSError("injected pipe failure")

        monkeypatch.setattr(client, "respond", fail_respond)
        kwargs = {
            "interaction_id": pending["interaction_id"],
            "thread_id": thread.thread_id,
            "run_id": run.run_id,
            "turn_id": turn_id,
            "item_id": "failed-write-item",
            "decision": "accept",
            "client_request_id": "client-failed-decision",
        }
        with pytest.raises(RuntimeBrokerError) as failed:
            broker.decide_approval(**kwargs)
        _assert_broker_error(failed, "interaction_outcome_unknown")

        with pytest.raises(RuntimeBrokerError) as replay:
            broker.decide_approval(**kwargs)
        _assert_broker_error(replay, "interaction_outcome_unknown")
        assert calls == 1
        checkpoint = json.loads(
            (storage.root / "runtime-state.json").read_text(encoding="utf-8")
        )
        assert (
            checkpoint["interactions"][pending["interaction_id"]]["status"]
            == "outcome_unknown"
        )
    finally:
        broker.close()


@pytest.mark.parametrize(
    "path_field",
    ["header", "question", "option_label", "option_description"],
)
def test_absolute_path_text_is_not_projected_in_user_question(
    tmp_path: Path,
    path_field: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Reject private question text",
            client_request_id=f"absolute-question-{path_field}",
        )
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        workspace = str(storage.resolve_workspace_path(thread.workspace_path))
        question = {
            "id": "scope",
            "header": "Scope",
            "question": "Which scope should be used?",
            "options": [
                {
                    "label": "Source",
                    "description": "Use the source tree.",
                }
            ],
            "isOther": False,
            "isSecret": False,
        }
        private_text = f"Inspect {workspace}/private.txt"
        if path_field == "option_label":
            question["options"][0]["label"] = private_text
        elif path_field == "option_description":
            question["options"][0]["description"] = private_text
        else:
            question[path_field] = private_text

        assert client.emit_request(
            "item/tool/requestUserInput",
            {
                "threadId": remote_thread_id,
                "turnId": turn_id,
                "itemId": "absolute-question-item",
                "questions": [question],
            },
            request_id="provider-absolute-question",
        ) == {"answers": {"scope": {"answers": []}}}
        assert broker.pending_interactions(thread.thread_id) == ()
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert private_text not in serialized
    finally:
        broker.close()


def test_user_question_deferred_answer_is_exact_and_secret_questions_are_rejected(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Ask",
            client_request_id="client-question-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        params = {
            "threadId": remote_thread_id,
            "turnId": turn_id,
            "itemId": "question-item-1",
            "questions": [
                {
                    "id": "scope",
                    "header": "Scope",
                    "question": "Which scope should be used?",
                    "options": [
                        {
                            "label": "Source and docs",
                            "description": "Keep both surfaces aligned.",
                        }
                    ],
                    "isOther": False,
                    "isSecret": False,
                }
            ],
        }
        assert (
            client.emit_request("item/tool/requestUserInput", params)
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        invalid_answers = (
            {"scope": ["Source and docs", "Something else"]},
            {"scope": ["Something else"]},
        )
        for index, answers in enumerate(invalid_answers):
            with pytest.raises(ValueError):
                broker.answer_user_input(
                    thread_id=thread.thread_id,
                    interaction_id=pending["interaction_id"],
                    run_id=run.run_id,
                    turn_id=turn_id,
                    item_id="question-item-1",
                    answers=answers,
                    client_request_id=f"client-invalid-answer-{index}",
                )
        assert client.responses == []

        result = broker.answer_user_input(
            thread_id=thread.thread_id,
            interaction_id=pending["interaction_id"],
            run_id=run.run_id,
            turn_id=turn_id,
            item_id="question-item-1",
            answers={"scope": ["Source and docs"]},
            client_request_id="client-answer-1",
        )

        assert result.status == "answered"
        assert client.responses[-1][1] == {
            "answers": {"scope": {"answers": ["Source and docs"]}}
        }

        secret_params = deepcopy(params)
        secret_params["itemId"] = "secret-item-1"
        secret_params["questions"][0]["isSecret"] = True
        secret_params["questions"][0]["question"] = (
            "Paste reusable-secret for private@example.test"
        )
        assert client.emit_request(
            "item/tool/requestUserInput",
            secret_params,
            request_id="provider-secret-question",
        ) == {"answers": {"scope": {"answers": []}}}
        assert broker.pending_interactions(thread_id=thread.thread_id) == ()
        assert len(client.responses) == 1
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert "reusable-secret" not in serialized
        assert "private@example.test" not in serialized
    finally:
        broker.close()


def test_provider_resolved_notification_expires_token_and_blocks_late_decision(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Resolve",
            client_request_id="client-resolved-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        params = {
            "command": "python -m pytest -q",
            "commandActions": [
                {
                    "type": "listFiles",
                    "command": "python -m pytest -q",
                    "path": str(storage.resolve_workspace_path(thread.workspace_path)),
                }
            ],
            "cwd": str(storage.resolve_workspace_path(thread.workspace_path)),
            "itemId": "command-resolved-1",
            "startedAtMs": 1_783_936_800_000,
            "threadId": remote_thread_id,
            "turnId": turn_id,
        }
        assert (
            client.emit_request(
                "item/commandExecution/requestApproval",
                params,
                request_id="provider-resolved-1",
            )
            is DEFERRED_RESPONSE
        )
        pending = _pending_one(broker, thread.thread_id)

        client.emit_notification(
            "serverRequest/resolved",
            {"requestId": "provider-resolved-1", "threadId": remote_thread_id},
        )

        assert broker.pending_interactions(thread_id=thread.thread_id) == ()
        assert client.discarded == [("provider-resolved-1", 1)]
        with pytest.raises(RuntimeBrokerError) as late:
            broker.decide_approval(
                thread_id=thread.thread_id,
                interaction_id=pending["interaction_id"],
                run_id=run.run_id,
                turn_id=turn_id,
                item_id="command-resolved-1",
                decision="accept",
                client_request_id="client-too-late",
            )
        _assert_broker_error(late, "interaction_stale")
        assert client.responses == []
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("permissions", "reason"),
    [
        ({"network": {"enabled": True}}, "forbidden_network"),
        (
            {"fileSystem": {"read": ["/data/codex-home/auth.json"]}},
            "outside_workspace",
        ),
        (
            {"network": {"enabled": True}},
            "private_host",
        ),
    ],
)
def test_permission_escalations_are_deferred_then_automatically_denied(
    tmp_path: Path,
    permissions: dict[str, Any],
    reason: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Escalate",
            client_request_id=f"client-{reason}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        request_reason = (
            "Connect to homeassistant.local"
            if reason == "private_host"
            else "Provider supplied reusable-secret private@example.test"
        )
        params = {
            "cwd": str(storage.resolve_workspace_path(thread.workspace_path)),
            "itemId": f"permission-{reason}",
            "permissions": permissions,
            "reason": request_reason,
            "startedAtMs": 1_783_936_800_000,
            "threadId": remote_thread_id,
            "turnId": turn_id,
        }
        assert client.emit_request(
            "item/permissions/requestApproval",
            params,
            request_id=f"provider-{reason}",
        ) == {"permissions": {}, "scope": "turn"}

        assert broker.pending_interactions(thread_id=thread.thread_id) == ()
        assert client.responses == []
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert "reusable-secret" not in serialized
        assert "private@example.test" not in serialized
        assert "/data/codex-home" not in serialized
    finally:
        broker.close()


def test_mismatched_provider_response_is_not_rebound_or_fallen_back(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Mismatch",
            client_request_id="client-mismatch",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        params = {
            "command": "echo reusable-secret private@example.test",
            "itemId": "mismatched-item",
            "startedAtMs": 1_783_936_800_000,
            "threadId": remote_thread_id,
            "turnId": f"wrong-{turn_id}",
        }

        assert client.emit_request(
            "item/commandExecution/requestApproval",
            params,
            request_id="provider-mismatch",
        ) == {"decision": "decline"}

        assert broker.pending_interactions(thread_id=thread.thread_id) == ()
        assert client.responses == []
        assert len(_requests(client, "turn/start")) == 1
        serialized = json.dumps(
            [
                event.model_dump(mode="json")
                for event in storage.list_thread_events(thread.thread_id)
            ]
        )
        assert "reusable-secret" not in serialized
        assert "private@example.test" not in serialized
    finally:
        broker.close()


def test_close_invalidates_pending_and_queued_work_without_late_response(
    tmp_path: Path,
) -> None:
    storage, first_thread = _storage_and_thread(tmp_path)
    second_thread = _new_thread(storage, tmp_path, name="CloseQueued")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    broker.submit_prompt(
        first_thread.thread_id,
        "Active",
        client_request_id="client-close-active",
    )
    _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
    broker.submit_prompt(
        second_thread.thread_id,
        "Queued",
        client_request_id="client-close-queued",
    )
    _run_id, remote_thread_id, turn_id = _active_ids(storage, first_thread.thread_id)
    assert (
        client.emit_request(
            "item/commandExecution/requestApproval",
            {
                "command": "python -m pytest -q",
                "commandActions": [
                    {
                        "type": "listFiles",
                        "command": "python -m pytest -q",
                        "path": str(
                            storage.resolve_workspace_path(first_thread.workspace_path)
                        ),
                    }
                ],
                "itemId": "close-item",
                "startedAtMs": 1_783_936_800_000,
                "threadId": remote_thread_id,
                "turnId": turn_id,
            },
            request_id="provider-close",
        )
        is DEFERRED_RESPONSE
    )

    broker.close()

    assert broker.pending_interactions(thread_id=first_thread.thread_id) == ()
    assert client.discarded == [("provider-close", 1)]
    assert client.responses == []
    assert broker.runtime_snapshot().active_turns == 0
    assert broker.runtime_snapshot().queued_prompts == 0
    with pytest.raises(RuntimeBrokerError) as closed:
        broker.submit_prompt(
            first_thread.thread_id,
            "After close",
            client_request_id="client-after-close",
        )
    _assert_broker_error(closed, "runtime_closed")


def test_cold_restart_interrupts_active_and_queued_runs_then_accepts_fresh_work(
    tmp_path: Path,
) -> None:
    storage, active_thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="RestartQueued")
    original_client = ValidatorBackedAppServer()
    original_broker = _broker(storage, original_client)

    active = original_broker.submit_prompt(
        active_thread.thread_id,
        "Persist active work",
        client_request_id="restart-active-request",
    )
    _wait_until(lambda: len(_requests(original_client, "turn/start")) == 1)
    queued = original_broker.submit_prompt(
        queued_thread.thread_id,
        "Persist queued work",
        client_request_id="restart-queued-request",
    )
    assert queued.status == "queued"
    assert original_broker.runtime_snapshot().active_turns == 1
    assert original_broker.runtime_snapshot().queued_prompts == 1

    _restore_durable_runtime_checkpoint_after_stopping(original_broker, storage)

    recovered_client = ValidatorBackedAppServer()
    recovered_broker = _broker(storage, recovered_client)
    try:
        assert recovered_broker.runtime_snapshot().active_turns == 0
        assert recovered_broker.runtime_snapshot().queued_prompts == 0
        assert recovered_client.requests == []

        active_replay = recovered_broker.submit_prompt(
            active_thread.thread_id,
            "Persist active work",
            client_request_id="restart-active-request",
        )
        queued_replay = recovered_broker.submit_prompt(
            queued_thread.thread_id,
            "Persist queued work",
            client_request_id="restart-queued-request",
        )
        assert active_replay.run_id == active.run_id
        assert active_replay.status == "interrupted"
        assert queued_replay.run_id == queued.run_id
        assert queued_replay.status == "interrupted"
        assert recovered_client.requests == []

        assert storage.load_thread(active_thread.thread_id).active_run_id is None
        assert storage.load_thread(queued_thread.thread_id).active_run_id is None

        fresh = recovered_broker.submit_prompt(
            active_thread.thread_id,
            "Fresh work after restart",
            client_request_id="restart-fresh-request",
        )
        _wait_until(lambda: len(_requests(recovered_client, "turn/start")) == 1)
        run_id, remote_thread_id, turn_id = _active_ids(
            storage, active_thread.thread_id
        )
        assert run_id == fresh.run_id
        assert recovered_broker.runtime_snapshot().active_turns == 1
        assert recovered_broker.runtime_snapshot().queued_prompts == 0

        _complete(
            recovered_client,
            remote_thread_id=remote_thread_id,
            turn_id=turn_id,
        )
        _wait_until(lambda: recovered_broker.runtime_snapshot().active_turns == 0)
    finally:
        recovered_broker.close()


def test_cold_restart_expires_pending_and_quarantines_responding_interaction(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    original_client = ValidatorBackedAppServer()
    original_broker = _broker(storage, original_client)

    run = original_broker.submit_prompt(
        thread.thread_id,
        "Persist provider interactions",
        client_request_id="restart-interaction-run",
    )
    _wait_until(lambda: len(_requests(original_client, "turn/start")) == 1)
    _run_id, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
    workspace = str(storage.resolve_workspace_path(thread.workspace_path))

    for item_id, request_id in (
        ("restart-pending-item", "provider-restart-pending"),
        ("restart-responding-item", "provider-restart-responding"),
    ):
        assert (
            original_client.emit_request(
                "item/commandExecution/requestApproval",
                {
                    "command": "python -m pytest -q",
                    "commandActions": [
                        {
                            "type": "listFiles",
                            "command": "python -m pytest -q",
                            "path": workspace,
                        }
                    ],
                    "cwd": workspace,
                    "itemId": item_id,
                    "startedAtMs": 1_783_936_800_000,
                    "threadId": remote_thread_id,
                    "turnId": turn_id,
                },
                request_id=request_id,
            )
            is DEFERRED_RESPONSE
        )

    interactions_by_item = {
        interaction.item_id: interaction
        for interaction in original_broker.pending_interactions(thread.thread_id)
    }
    pending = interactions_by_item["restart-pending-item"]
    responding = interactions_by_item["restart-responding-item"]
    with original_broker._lock:
        responding_state = original_broker._state.interactions[
            responding.interaction_id
        ]
        responding_state.status = "responding"
        responding_state.response_client_request_id = "restart-response-request"
        responding_state.response_fingerprint = runtime_fingerprint(
            ["decision", "accept"]
        )
        original_broker._persist_locked()

    _restore_durable_runtime_checkpoint_after_stopping(original_broker, storage)

    recovered_client = ValidatorBackedAppServer()
    recovered_broker = _broker(storage, recovered_client)
    try:
        assert recovered_broker.pending_interactions(thread.thread_id) == ()
        assert recovered_broker.runtime_snapshot().active_turns == 0
        assert recovered_broker.runtime_snapshot().queued_prompts == 0

        recovered_pending = recovered_broker._state.interactions[pending.interaction_id]
        recovered_responding = recovered_broker._state.interactions[
            responding.interaction_id
        ]
        assert recovered_pending.status == "expired"
        assert recovered_pending.display is None
        assert recovered_responding.status == "outcome_unknown"
        assert recovered_responding.display is None
        assert (
            recovered_responding.response_client_request_id
            == "restart-response-request"
        )

        assert recovered_client.requests == []
        assert recovered_client.responses == []
        assert recovered_client.discarded == []

        replay = recovered_broker.submit_prompt(
            thread.thread_id,
            "Persist provider interactions",
            client_request_id="restart-interaction-run",
        )
        assert replay.run_id == run.run_id
        assert replay.status == "interrupted"

        fresh = recovered_broker.submit_prompt(
            thread.thread_id,
            "Continue after interaction recovery",
            client_request_id="restart-interaction-fresh",
        )
        _wait_until(lambda: len(_requests(recovered_client, "turn/start")) == 1)
        assert fresh.run_id == storage.load_thread(thread.thread_id).active_run_id
    finally:
        recovered_broker.close()


def test_corrupt_runtime_checkpoint_is_quarantined_and_thread_is_repaired(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    record = storage.load_thread(thread.thread_id)
    record.status = "running"
    record.active_run_id = "run_lost_checkpoint"
    record.active_turn_id = "turn_lost_checkpoint"
    storage.save_thread(record)
    checkpoint = storage.root / "runtime-state.json"
    checkpoint.write_text('{"schema_version":1,"runs":', encoding="utf-8")

    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        quarantined = tuple(storage.root.glob("runtime-state.corrupt.*.json"))
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8").endswith('"runs":')
        repaired = storage.load_thread(thread.thread_id)
        assert repaired.status == "error"
        assert repaired.active_run_id is None
        assert repaired.active_turn_id is None
        assert repaired.last_error == (
            "Codex runtime ownership was reset after an invalid checkpoint."
        )
        recovered_event = next(
            event
            for event in storage.event_store.replay(
                after_cursor=0,
                scopes=("thread",),
                thread_ids=(thread.thread_id,),
            ).events
            if event.event_type == "runtime.state_recovered"
        )
        repaired_state = json.loads(
            storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
        )
        assert recovered_event.operation_id is not None
        assert repaired_state["_bridge_operation"]["operation_id"] == (
            recovered_event.operation_id
        )

        fresh = broker.submit_prompt(
            thread.thread_id,
            "Continue safely",
            client_request_id="after-corrupt-checkpoint",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        assert fresh.status == "starting"
    finally:
        broker.close()
