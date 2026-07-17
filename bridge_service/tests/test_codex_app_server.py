from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import importlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from threading import Event
import time
import tomllib
from types import ModuleType
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_broker import RuntimeBroker
from codex_bridge_service.runtime_gate import RuntimeGate
from codex_bridge_service.storage import BridgeStorage

FAKE_APP_SERVER = Path(__file__).with_name("fakes") / "fake_app_server.py"
ROOT = Path(__file__).resolve().parents[2]


def _canonical_versions() -> tuple[str, str]:
    lock = json.loads(
        (ROOT / "codex_bridge_app/codex-release.json").read_text(encoding="utf-8")
    )
    project = tomllib.loads(
        (ROOT / "bridge_service/pyproject.toml").read_text(encoding="utf-8")
    )
    return lock["release"]["version"], project["project"]["version"]


CODEX_VERSION, BRIDGE_VERSION = _canonical_versions()


class FakeAppServer:
    def __init__(self, root: Path) -> None:
        self.codex_home = root / "codex-home"
        self.sidecars = self.codex_home / ".fake-app-server"
        self.sidecars.mkdir(parents=True)
        local_command = root / "fake_app_server.py"
        shutil.copy2(FAKE_APP_SERVER, local_command)
        self.command = str(local_command)

    def configure(self, generation: int = 1, **scenario: Any) -> None:
        if "initialize_result" not in scenario:
            scenario["initialize_result"] = {
                "codexHome": str(self.codex_home.resolve()),
                "platformFamily": "windows" if os.name == "nt" else "unix",
                "platformOs": "windows" if os.name == "nt" else "linux",
                "userAgent": f"Codex Desktop/{CODEX_VERSION} (test; x86_64)",
            }
        self._write_json(self.sidecars / f"scenario-{generation}.json", scenario)

    def release(self, generation: int, *control_keys: str) -> None:
        self._write_json(
            self.sidecars / f"control-{generation}.json",
            {"release": list(control_keys)},
        )

    def transcript(self, generation: int = 1) -> list[dict[str, Any]]:
        path = self.sidecars / f"transcript-{generation}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]

    def client_messages(self, generation: int = 1) -> list[dict[str, Any]]:
        return [
            entry["message"]
            for entry in self.transcript(generation)
            if entry["direction"] == "client" and isinstance(entry["message"], dict)
        ]

    def process(self, generation: int = 1) -> dict[str, Any]:
        return json.loads(
            (self.sidecars / f"process-{generation}.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)


@pytest.fixture
def fake_server(tmp_path: Path) -> FakeAppServer:
    return FakeAppServer(tmp_path)


def _load_module() -> ModuleType:
    try:
        return importlib.import_module("codex_bridge_service.codex_app_server")
    except ImportError as exc:
        pytest.fail(f"Codex app-server client module is missing: {exc}")
        raise AssertionError from exc


def _client(module: ModuleType, fake_server: FakeAppServer, **overrides: Any) -> Any:
    options: dict[str, object] = {
        "codex_command": fake_server.command,
        "codex_home": fake_server.codex_home,
        "client_name": "ha_codex_bridge",
        "client_title": "HA Codex Bridge",
        "client_version": BRIDGE_VERSION,
        "initialize_timeout_seconds": 10.0,
        "request_timeout_seconds": 2.0,
        "max_message_bytes": 16 * 1024,
        "max_pending_requests": 8,
        "callback_workers": 2,
        "max_callback_queue": 8,
        "restart_base_delay_seconds": 0.03,
        "restart_max_delay_seconds": 0.1,
        "restart_stable_seconds": 0.2,
        "shutdown_grace_seconds": 0.05,
        "protocol_contract": None,
    }
    use_default_message_limit = overrides.pop("_use_default_message_limit", False)
    use_default_request_timeout = overrides.pop("_use_default_request_timeout", False)
    options.update(overrides)
    if use_default_message_limit:
        options.pop("max_message_bytes")
    if use_default_request_timeout:
        options.pop("request_timeout_seconds")
    return module.CodexAppServerClient(**options)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    message: str = "condition was not met",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def _wait_for_client_message(
    fake_server: FakeAppServer,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    generation: int = 1,
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []

    def locate() -> bool:
        found[:] = [
            message
            for message in fake_server.client_messages(generation)
            if predicate(message)
        ]
        return bool(found)

    _wait_until(locate, message="expected client message was not written")
    return found[-1]


def test_start_performs_initialize_then_initialized_with_sanitized_environment(
    fake_server: FakeAppServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    fake_server.configure()
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.invalid")
    client = _client(module, fake_server)

    try:
        client.start()

        assert client.ready is True
        assert client.generation == 1
        assert client.process_id == fake_server.process()["pid"]
        assert client.server_version == CODEX_VERSION
        _wait_for_client_message(
            fake_server,
            lambda message: message.get("method") == "initialized",
        )
        messages = fake_server.client_messages()
        assert [message.get("method") for message in messages[:2]] == [
            "initialize",
            "initialized",
        ]
        assert "id" in messages[0]
        assert "id" not in messages[1]
        assert messages[0]["params"]["clientInfo"] == {
            "name": "ha_codex_bridge",
            "title": "HA Codex Bridge",
            "version": BRIDGE_VERSION,
        }
        assert messages[0]["params"]["capabilities"] == {
            "experimentalApi": False,
            "requestAttestation": False,
        }
        assert fake_server.process()["argv"] == [
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        ]
        environment_keys = fake_server.process()["environmentKeys"]
        assert "CODEX_HOME" in environment_keys
        assert "HOME" in environment_keys
        assert "CODEX_BRIDGE_AUTH_TOKEN" not in environment_keys
        assert "OPENAI_API_KEY" not in environment_keys
        assert "SUPERVISOR_TOKEN" not in environment_keys
        assert "HTTPS_PROXY" not in environment_keys
    finally:
        client.close()
    assert client.ready is False
    assert client.server_version is None
    assert client.process_id is None
    _wait_until(
        lambda: any(
            entry.get("direction") == "server-control"
            and entry.get("message") == {"event": "stdin-eof"}
            for entry in fake_server.transcript()
        ),
        message="app-server did not receive a graceful stdin EOF",
    )


def test_experimental_api_is_opt_in_for_a_proven_client_owned_tool(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure()
    client = _client(module, fake_server, enable_experimental_api=True)

    try:
        client.start()
        initialize = fake_server.client_messages()[0]
        assert initialize["params"]["capabilities"] == {
            "experimentalApi": True,
            "requestAttestation": False,
        }
    finally:
        client.close()


def test_experimental_api_flag_requires_an_exact_boolean(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="experimental API enabled state"):
        _client(module, fake_server, enable_experimental_api=1)


def test_disabled_mcp_adds_a_generation_scoped_empty_config_override(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure()
    client = _client(module, fake_server, enable_mcp=False)

    try:
        client.start()
        assert fake_server.process()["argv"] == [
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        ]
    finally:
        client.close()


def test_enabled_mcp_bootstraps_masked_then_activates_a_clean_generation(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure()
    fake_server.configure(2)
    client = _client(module, fake_server, enable_mcp=True)

    try:
        client.start()
        assert fake_server.process()["argv"] == [
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        ]

        client.activate_validated_mcp_config()

        assert client.ready is True
        assert client.generation == 2
        assert fake_server.process(2)["argv"] == ["app-server", "--stdio"]
    finally:
        client.close()


def test_failed_mcp_activation_restores_the_empty_config_override(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure()
    fake_server.configure(2, startup="stall_initialize")
    fake_server.configure(3)
    client = _client(
        module,
        fake_server,
        enable_mcp=True,
        initialize_timeout_seconds=2.0,
    )

    try:
        client.start()
        # Keep the deliberate activation stall short without making a cold
        # Windows subprocess launch share the same brittle deadline.
        client.initialize_timeout_seconds = 0.2
        with pytest.raises(module.AppServerUnavailableError):
            client.activate_validated_mcp_config()

        _wait_until(
            lambda: (fake_server.sidecars / "process-3.json").exists(),
            message="masked recovery generation was not started",
        )
        assert fake_server.process(3)["argv"] == [
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        ]
    finally:
        client.close()


def test_activation_timeout_before_restart_keeps_the_next_generation_masked(
    fake_server: FakeAppServer,
) -> None:
    class _TimedOutEvent:
        def clear(self) -> None:
            pass

        def set(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> bool:
            del timeout
            return False

    module = _load_module()
    fake_server.configure()
    fake_server.configure(2)
    client = _client(module, fake_server, enable_mcp=True)
    original_abort = client.abort_generation

    try:
        client.start()
        client._mcp_activation_complete = _TimedOutEvent()
        client.abort_generation = lambda _generation: True

        with pytest.raises(module.AppServerUnavailableError):
            client.activate_validated_mcp_config()

        assert not (fake_server.sidecars / "process-2.json").exists()
        client.abort_generation = original_abort
        assert client.abort_generation(client.generation) is True
        _wait_until(
            lambda: (fake_server.sidecars / "process-2.json").exists(),
            message="recovery generation was not started",
        )
        assert fake_server.process(2)["argv"] == [
            "-c",
            "mcp_servers={}",
            "app-server",
            "--stdio",
        ]
    finally:
        client.abort_generation = original_abort
        client.close()


def test_application_requests_remain_closed_until_initialized_is_written(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure()
    client = _client(module, fake_server)
    initialized_write_entered = Event()
    release_initialized_write = Event()
    original_write = client._write_message

    def gated_write(generation: int, message: dict[str, Any]) -> None:
        if message.get("method") == "initialized":
            initialized_write_entered.set()
            assert release_initialized_write.wait(10)
        original_write(generation, message)

    client._write_message = gated_write
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            startup = executor.submit(client.start)
            assert initialized_write_entered.wait(10)
            assert client.ready is False
            with pytest.raises(module.AppServerUnavailableError):
                client.request("must-wait")
            assert not any(
                message.get("method") == "must-wait"
                for message in fake_server.client_messages()
            )
            release_initialized_write.set()
            startup.result(timeout=12)
        assert client.request("ping") == {"echo": None}
    finally:
        release_initialized_write.set()
        client.close()


def test_concurrent_requests_are_correlated_when_responses_arrive_in_reverse_order(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(responses={"echo/reverse": {"mode": "reverse_pair"}})
    client = _client(module, fake_server)
    client.start()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(client.request, "echo/reverse", {"value": "first"})
            second = executor.submit(
                client.request, "echo/reverse", {"value": "second"}
            )
            results = [first.result(timeout=2), second.result(timeout=2)]

        assert results == [
            {"echo": {"value": "first"}},
            {"echo": {"value": "second"}},
        ]
        requests = [
            message
            for message in fake_server.client_messages()
            if message.get("method") == "echo/reverse"
        ]
        assert requests[0]["id"] != requests[1]["id"]
    finally:
        client.close()


def test_notification_handler_is_immutable_and_never_blocks_response_reader(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {"kind": "notification", "method": "turn/progress", "params": {"step": 1}}
        ]
    )
    client = _client(module, fake_server, callback_workers=1, max_callback_queue=1)
    entered = Event()
    release = Event()
    notifications: list[Any] = []

    def handle(notification: Any) -> None:
        notifications.append(notification)
        entered.set()
        release.wait(2)

    client.register_notification_handler("turn/progress", handle)
    client.start()
    try:
        assert entered.wait(1)
        assert client.request("ping", {"sequence": 1}) == {"echo": {"sequence": 1}}
        notification = notifications[0]
        assert notification.method == "turn/progress"
        assert notification.params == {"step": 1}
        assert notification.generation == 1
        with pytest.raises(FrozenInstanceError):
            notification.method = "mutated"
    finally:
        release.set()
        client.close()


def test_notification_handler_failure_restarts_generation_for_reconciliation(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {"kind": "notification", "method": "account/updated", "params": {}}
        ]
    )
    client = _client(module, fake_server, callback_workers=1)

    def fail_durable_projection(_notification: Any) -> None:
        raise RuntimeError("private durable auth failure")

    client.register_notification_handler("account/updated", fail_durable_projection)
    client.start()
    try:
        _wait_until(lambda: client.ready and client.generation == 2)
        assert client.request("ping", {"generation": 2}) == {"echo": {"generation": 2}}
    finally:
        client.close()


def test_rapid_agent_message_deltas_do_not_overflow_the_callback_queue(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    initial = "0000 "
    deltas = [f"{index:04d} " for index in range(1, 1_001)]
    fake_server.configure(
        on_initialized=[
            {
                "kind": "notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "message-1",
                    "delta": initial,
                },
            }
        ],
        responses={
            "emit/long-response": {
                "mode": "notifications_then_echo",
                "notifications": [
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "itemId": "message-1",
                            "delta": delta,
                        },
                    }
                    for delta in deltas
                ]
                + [
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {
                                "id": "message-1",
                                "type": "agentMessage",
                                "text": initial + "".join(deltas),
                            },
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "turn": {
                                "id": "turn-1",
                                "status": "completed",
                                "items": [],
                            },
                        },
                    },
                ],
            }
        },
    )
    client = _client(module, fake_server, callback_workers=1, max_callback_queue=1)
    entered = Event()
    release = Event()
    received: list[tuple[str, str]] = []

    def handle(notification: Any) -> None:
        received.append(("delta", notification.params["delta"]))
        if notification.params["delta"] == initial:
            entered.set()
            release.wait(2)

    def handle_terminal(_notification: Any) -> None:
        received.append(("terminal", ""))

    client.register_notification_handler("item/agentMessage/delta", handle)
    client.register_notification_handler("item/completed", handle_terminal)
    client.register_notification_handler("turn/completed", handle_terminal)
    client.start()
    try:
        assert entered.wait(1)
        assert client.request("emit/long-response") == {"echo": None}
        assert client.generation == 1
        release.set()
        _wait_until(
            lambda: sum(kind == "terminal" for kind, _value in received) == 2,
            message="completion notifications did not follow the assistant text",
        )
        assert "".join(value for kind, value in received if kind == "delta") == (
            initial + "".join(deltas)
        )
        assert [kind for kind, _value in received][-2:] == ["terminal", "terminal"]
        assert client.ready is True
        assert client.generation == 1
    finally:
        release.set()
        client.close()


def test_real_runtime_broker_preserves_a_rapid_five_thousand_word_response(
    fake_server: FakeAppServer,
    tmp_path: Path,
) -> None:
    module = _load_module()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    remote_thread_id = "codex-thread-long-response"
    remote_turn_id = "codex-turn-long-response"
    model = "gpt-5.6-codex"
    words = [f"word-{index:04d}" for index in range(5_000)]
    response = " ".join(words)
    deltas = []
    for offset in range(0, len(words), 13):
        chunk = " ".join(words[offset : offset + 13])
        deltas.append(chunk if offset == 0 else f" {chunk}")
    thread = {
        "id": remote_thread_id,
        "preview": "",
        "ephemeral": False,
        "modelProvider": "openai",
        "createdAt": 1_783_936_800,
        "updatedAt": 1_783_936_800,
        "status": {"type": "idle"},
        "cwd": str(workspace),
        "cliVersion": CODEX_VERSION,
        "source": "appServer",
        "turns": [],
        "sessionId": "session-long-response",
    }
    fake_server.configure(
        responses={
            "thread/start": {
                "result": {
                    "thread": thread,
                    "model": model,
                    "modelProvider": "openai",
                    "cwd": str(workspace),
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sandbox": {
                        "type": "workspaceWrite",
                        "networkAccess": False,
                        "writableRoots": [
                            str(workspace / name)
                            for name in (
                                ".agents",
                                ".codex",
                                ".cursor",
                                ".git",
                                ".vscode",
                            )
                        ],
                        "excludeSlashTmp": True,
                        "excludeTmpdirEnvVar": True,
                    },
                    "activePermissionProfile": {"id": "ha_bridge", "extends": None},
                    "instructionSources": [],
                }
            },
            "turn/start": {
                "result": {
                    "turn": {
                        "id": remote_turn_id,
                        "items": [],
                        "status": "inProgress",
                    }
                }
            },
            "emit/long-response": {
                "mode": "notifications_then_echo",
                "notifications": [
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": remote_thread_id,
                            "turnId": remote_turn_id,
                            "itemId": "agent-message-long-response",
                            "delta": delta,
                        },
                    }
                    for delta in deltas
                ]
                + [
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": remote_thread_id,
                            "turnId": remote_turn_id,
                            "item": {
                                "id": "agent-message-long-response",
                                "type": "agentMessage",
                                "text": response,
                            },
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": remote_thread_id,
                            "turn": {
                                "id": remote_turn_id,
                                "items": [],
                                "status": "completed",
                            },
                        },
                    },
                ],
            },
        }
    )
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Long response",
        root_path=str(workspace),
        default_model=model,
        default_thinking_level="high",
    )
    local_thread = storage.create_thread(
        title="Long response",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    client = _client(
        module,
        fake_server,
        callback_workers=1,
        max_callback_queue=64,
        _use_default_message_limit=True,
        request_timeout_seconds=30.0,
    )
    client.start()
    broker = RuntimeBroker(
        storage=storage,
        app_server=client,
        runtime_gate=RuntimeGate(limits=ResourceLimits()),
        queue_wait_timeout_seconds=5.0,
        watchdog_interval_seconds=0.01,
        turn_timeout_seconds=30.0,
        cancel_grace_seconds=0.05,
        interaction_timeout_seconds=5.0,
    )
    broker.start()
    try:
        broker.submit_prompt(
            local_thread.thread_id,
            "Write a five thousand word response",
            client_request_id="long-response-transport",
        )

        def run_started() -> bool:
            record = storage.load_thread(local_thread.thread_id)
            return record.active_run_id is not None and any(
                event.event_type == "run.started"
                and event.payload.get("run_id") == record.active_run_id
                for event in storage.list_thread_events(local_thread.thread_id)
            )

        _wait_until(
            run_started,
            timeout=10.0,
            message="long-response run did not start",
        )
        assert client.request("emit/long-response", timeout_seconds=30.0) == {
            "echo": None
        }
        _wait_until(
            lambda: storage.load_thread(local_thread.thread_id).status == "idle",
            timeout=10.0,
            message="long-response run did not complete",
        )

        events = storage.list_thread_events(local_thread.thread_id)
        streamed = "".join(
            event.payload["text"]
            for event in events
            if event.event_type == "message.delta"
        )
        completed = [
            event for event in events if event.event_type == "message.completed"
        ]
        terminals = [
            event.event_type
            for event in events
            if event.event_type
            in {"run.completed", "run.failed", "run.interrupted", "run.cancelled"}
        ]
        assert streamed == response
        assert len(streamed.split()) == 5_000
        assert len(completed) == 1
        assert completed[0].payload["text"] == response
        assert terminals == ["run.completed"]
        assert client.ready is True
        assert client.generation == 1
    finally:
        broker.close()
        client.close()


def test_bounded_callback_queue_fails_generation_instead_of_dropping_notifications(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {"kind": "notification", "method": "item/delta", "params": {"index": 0}}
        ],
        responses={
            "emit/flood": {
                "mode": "notifications_then_echo",
                "notifications": [
                    {"method": "item/delta", "params": {"index": index}}
                    for index in range(1, 8)
                ],
            }
        },
    )
    client = _client(module, fake_server, callback_workers=1, max_callback_queue=1)
    entered = Event()
    release = Event()
    calls: list[Any] = []

    def handle(notification: Any) -> None:
        calls.append(notification)
        entered.set()
        release.wait(2)

    client.register_notification_handler("item/delta", handle)
    client.start()
    try:
        assert entered.wait(1)
        with pytest.raises(
            (module.AppServerOverloadedError, module.AppServerUnavailableError)
        ):
            client.request("emit/flood")
        assert len(calls) == 1
        release.set()
        _wait_until(lambda: client.ready and client.generation == 2)
        assert client.request("ping", {"generation": 2}) == {"echo": {"generation": 2}}
        assert len(calls) == 1
    finally:
        release.set()
        client.close()


def test_server_request_handler_can_respond_on_the_same_generation(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {
                "kind": "request",
                "id": "approval-7",
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "ls"},
            }
        ]
    )
    client = _client(module, fake_server)
    received: list[Any] = []

    def approve(request: Any) -> None:
        received.append(request)
        client.respond(request, result={"decision": "accept"})

    client.register_request_handler("item/commandExecution/requestApproval", approve)
    client.start()
    try:
        response = _wait_for_client_message(
            fake_server,
            lambda message: message.get("id") == "approval-7" and "result" in message,
        )
        assert response == {"id": "approval-7", "result": {"decision": "accept"}}
        assert received[0].request_id == "approval-7"
        assert received[0].method == "item/commandExecution/requestApproval"
        assert received[0].params == {"command": "ls"}
        assert received[0].generation == client.generation == 1
        with pytest.raises(FrozenInstanceError):
            received[0].generation = 2
    finally:
        client.close()


def test_discard_server_request_atomically_invalidates_the_response_token(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {
                "kind": "request",
                "id": "resolved-approval",
                "method": "approval/request",
                "params": {},
            }
        ]
    )
    client = _client(module, fake_server)
    received: list[Any] = []
    entered = Event()
    release = Event()

    def defer(request: Any) -> object:
        received.append(request)
        entered.set()
        release.wait(2)
        return module.DEFERRED_RESPONSE

    client.register_request_handler("approval/request", defer)
    client.start()
    try:
        assert entered.wait(1)
        request = received[0]
        assert (
            client.discard_server_request(
                request.request_id,
                request.generation + 1,
            )
            is False
        )
        assert (
            client.discard_server_request(
                request.request_id,
                request.generation,
            )
            is True
        )
        assert (
            client.discard_server_request(
                request.request_id,
                request.generation,
            )
            is False
        )

        release.set()
        with pytest.raises(module.AppServerStaleGenerationError):
            client.respond(request, result={"decision": "accept"})
        time.sleep(0.05)
        assert not any(
            message.get("id") == "resolved-approval"
            for message in fake_server.client_messages()
        )
        assert client.ready is True
    finally:
        release.set()
        client.close()


def test_abort_generation_fails_waiters_discards_tokens_and_restarts_only_match(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        1,
        on_initialized=[
            {
                "kind": "request",
                "id": "aborted-approval",
                "method": "approval/request",
                "params": {},
            }
        ],
        responses={"hold": {"mode": "hold", "control_key": "never"}},
    )
    fake_server.configure(2)
    client = _client(module, fake_server)
    retained: list[Any] = []
    approval_received = Event()

    def defer(request: Any) -> object:
        retained.append(request)
        approval_received.set()
        return module.DEFERRED_RESPONSE

    client.register_request_handler("approval/request", defer)
    client.start()
    try:
        assert approval_received.wait(1)
        first_generation = client.generation
        first_process_id = client.process_id
        assert first_process_id is not None
        assert client.abort_generation(first_generation + 1) is False
        assert client.generation == first_generation
        assert client.process_id == first_process_id

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.request, "hold", {"prompt": "bounded"})
            _wait_for_client_message(
                fake_server,
                lambda message: message.get("method") == "hold",
            )

            assert client.abort_generation(first_generation) is True
            assert client.abort_generation(first_generation) is False
            with pytest.raises(module.AppServerUnavailableError):
                pending.result(timeout=2)

        with pytest.raises(module.AppServerStaleGenerationError):
            client.respond(retained[0], result={"decision": "accept"})
        _wait_until(lambda: client.ready and client.generation == 2)
        second_process_id = client.process_id
        assert second_process_id is not None
        assert second_process_id == fake_server.process(2)["pid"]
        assert client.abort_generation(first_generation) is False
        assert client.ready is True
        assert client.process_id == second_process_id
        assert client.request("ping", {"generation": 2}) == {"echo": {"generation": 2}}
    finally:
        client.close()

    assert client.abort_generation(2) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_abort_generation_force_kills_a_sigterm_resistant_process_group(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(1, spawn_child=True, ignore_sigterm=True)
    fake_server.configure(2)
    client = _client(module, fake_server, shutdown_grace_seconds=0.05)
    parent_pid: int | None = None
    child_pid: int | None = None

    try:
        client.start()
        parent_pid = client.process_id
        child_pid = fake_server.process(1)["childPid"]
        assert parent_pid is not None
        assert _pid_is_running(parent_pid)
        assert _pid_is_running(child_pid)

        assert client.abort_generation(client.generation) is True

        _wait_until(
            lambda: not _pid_is_running(parent_pid) and not _pid_is_running(child_pid),
            timeout=1,
            message="abort left a process in the Codex app-server process group",
        )
        _wait_until(lambda: client.ready and client.generation == 2)
    finally:
        client.close()
        if parent_pid is not None and (
            _pid_is_running(parent_pid)
            or (child_pid is not None and _pid_is_running(child_pid))
        ):
            try:
                os.killpg(parent_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_abort_generation_kills_child_after_sigterm_exits_group_leader() -> None:
    module = _load_module()
    parent_code = (
        "import signal, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        '"import os, signal, time; '
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'print(os.getpid(), flush=True); time.sleep(120)"]); '
        "time.sleep(120)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid: int | None = None

    try:
        assert process.stdout is not None
        child_pid = int(process.stdout.readline().decode("ascii").strip())
        assert _pid_is_running(process.pid)
        assert _pid_is_running(child_pid)

        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
        assert not _pid_is_running(process.pid)
        assert _pid_is_running(child_pid)

        module._force_stop_aborted_process(process, 0.05)

        _wait_until(
            lambda: not _pid_is_running(child_pid),
            timeout=1,
            message="abort left a child behind after the group leader exited",
        )
    finally:
        if _pid_is_running(process.pid) or (
            child_pid is not None and _pid_is_running(child_pid)
        ):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.stdout is not None:
            process.stdout.close()


def test_missing_and_failed_server_request_handlers_return_safe_errors(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        on_initialized=[
            {"kind": "request", "id": 40, "method": "unknown/request", "params": {}},
            {"kind": "request", "id": 41, "method": "broken/request", "params": {}},
        ]
    )
    client = _client(module, fake_server)

    def fail_safely(_request: Any) -> None:
        raise RuntimeError("handler leaked reusable-secret and person@example.com")

    client.register_request_handler("broken/request", fail_safely)
    client.start()
    try:
        missing = _wait_for_client_message(
            fake_server,
            lambda message: message.get("id") == 40 and "error" in message,
        )
        failed = _wait_for_client_message(
            fake_server,
            lambda message: message.get("id") == 41 and "error" in message,
        )
        assert missing["error"]["code"] == -32601
        assert failed["error"]["code"] == -32603
        serialized = json.dumps([missing, failed])
        assert "reusable-secret" not in serialized
        assert "person@example.com" not in serialized
    finally:
        client.close()


def test_model_provider_capability_probe_is_typed_and_uses_empty_params(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        responses={
            "modelProvider/capabilities/read": {
                "result": {
                    "imageGeneration": True,
                    "namespaceTools": False,
                    "webSearch": True,
                }
            }
        }
    )
    client = _client(module, fake_server)
    client.start()

    try:
        capabilities = client.read_model_provider_capabilities()

        assert capabilities.generation == 1
        assert capabilities.image_generation is True
        assert capabilities.namespace_tools is False
        assert capabilities.web_search is True
        request = _wait_for_client_message(
            fake_server,
            lambda message: message.get("method") == "modelProvider/capabilities/read",
        )
        assert request["params"] == {}
    finally:
        client.close()


def test_model_provider_capability_probe_rejects_non_boolean_flags(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        responses={
            "modelProvider/capabilities/read": {
                "result": {
                    "imageGeneration": 1,
                    "namespaceTools": False,
                    "webSearch": True,
                }
            }
        }
    )
    client = _client(module, fake_server)
    client.start()

    try:
        with pytest.raises(module.AppServerProtocolError):
            client.read_model_provider_capabilities()
    finally:
        client.close()


def test_pending_request_capacity_is_reserved_before_writing(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(responses={"hold": {"mode": "hold", "control_key": "held"}})
    client = _client(module, fake_server, max_pending_requests=1)
    client.start()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(client.request, "hold", {"request": 1})
            _wait_until(
                lambda: any(
                    message.get("method") == "hold"
                    for message in fake_server.client_messages()
                )
            )
            with pytest.raises(module.AppServerOverloadedError):
                client.request("must-not-be-written", {"request": 2})
            assert not any(
                message.get("method") == "must-not-be-written"
                for message in fake_server.client_messages()
            )
            fake_server.release(1, "held")
            assert first.result(timeout=2) == {"echo": {"request": 1}}
    finally:
        client.close()


def test_request_timeout_releases_capacity_and_late_response_is_ignored(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(responses={"slow": {"mode": "hold", "control_key": "slow"}})
    client = _client(module, fake_server, max_pending_requests=1)
    client.start()

    try:
        with pytest.raises(module.AppServerTimeoutError):
            client.request("slow", {"request": "old"}, timeout_seconds=0.03)
        slow_request = _wait_for_client_message(
            fake_server,
            lambda message: message.get("method") == "slow",
        )
        fake_server.release(1, "slow")
        _wait_until(
            lambda: any(
                entry.get("direction") == "server"
                and entry.get("message", {}).get("id") == slow_request["id"]
                for entry in fake_server.transcript()
            ),
            message="fake server did not emit the deliberately late response",
        )
        assert client.ready is True
        assert client.request("ping", {"request": "new"}) == {
            "echo": {"request": "new"}
        }
    finally:
        client.close()


def test_remote_error_is_mapped_to_typed_exception(fake_server: FakeAppServer) -> None:
    module = _load_module()
    fake_server.configure(
        responses={
            "remote/fail": {
                "mode": "error",
                "error": {
                    "code": -32042,
                    "message": "request rejected",
                    "data": {"retry": False},
                },
            }
        }
    )
    client = _client(module, fake_server)
    client.start()
    try:
        with pytest.raises(module.AppServerRemoteError) as error:
            client.request("remote/fail")
        assert error.value.code == -32042
    finally:
        client.close()


def test_initialize_timeout_is_typed_and_close_stops_retries(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(startup="stall_initialize")
    client = _client(
        module,
        fake_server,
        initialize_timeout_seconds=0.05,
        restart_base_delay_seconds=0.5,
        restart_max_delay_seconds=0.5,
    )
    try:
        with pytest.raises(module.AppServerTimeoutError):
            client.start()
    finally:
        client.close()
    time.sleep(0.1)
    assert not (fake_server.sidecars / "generation-2.claim").exists()


def test_restart_backoff_is_positive_capped_and_cannot_overflow(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()

    assert (
        module._capped_restart_delay(
            base_seconds=0.25,
            maximum_seconds=30.0,
            attempt=1025,
        )
        == 30.0
    )
    with pytest.raises(ValueError, match="restart base delay"):
        _client(module, fake_server, restart_base_delay_seconds=0)


def test_outbound_limit_preflight_rejects_before_json_serialization(
    fake_server: FakeAppServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    client = _client(module, fake_server, max_message_bytes=256)

    def serialization_must_not_run(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("oversized payload reached json.dumps")

    monkeypatch.setattr(module.json, "dumps", serialization_must_not_run)
    with pytest.raises(module.AppServerProtocolError):
        client._write_message(1, {"payload": "x" * 1024})
    exact_body = "x" * (client.max_message_bytes - len(b'{"payload":""}'))
    with pytest.raises(module.AppServerProtocolError):
        client._write_message(1, {"payload": exact_body})


def test_default_plugin_catalog_bounds_accept_large_response(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    description = "x" * 4_000_000
    fake_server.configure(
        responses={
            "plugin/list": {
                "result": {
                    "marketplaces": [
                        {
                            "name": "official",
                            "plugins": [
                                {
                                    "id": "large-plugin",
                                    "name": "Large Plugin",
                                    "description": description,
                                }
                            ],
                        }
                    ],
                    "marketplaceLoadErrors": [],
                    "featuredPluginIds": ["large-plugin"],
                }
            }
        }
    )
    client = _client(
        module,
        fake_server,
        _use_default_message_limit=True,
        _use_default_request_timeout=True,
    )
    assert client.max_message_bytes == module._DEFAULT_MAX_MESSAGE_BYTES
    assert client.request_timeout_seconds == 30.0

    client.start()
    try:
        result = client.request("plugin/list")
        marketplace = result["marketplaces"][0]
        plugin = marketplace["plugins"][0]
        assert marketplace["name"] == "official"
        assert plugin["id"] == "large-plugin"
        assert plugin["name"] == "Large Plugin"
        assert len(plugin["description"]) == len(description)
        assert result["marketplaceLoadErrors"] == []
        assert result["featuredPluginIds"] == ["large-plugin"]
    finally:
        client.close()


def test_default_request_timeout_remains_bounded_for_ordinary_requests(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    client = _client(module, fake_server, _use_default_request_timeout=True)

    assert client.request_timeout_seconds == 30.0


@pytest.mark.parametrize(
    "message",
    [
        {"method": "invented/notification", "params": {}},
        {"method": "invented/request", "id": "unknown-1", "params": {}},
        {"method": "account/updated", "params": {"authMode": "api-key"}},
    ],
)
def test_locked_inbound_method_and_payload_violations_restart_generation(
    fake_server: FakeAppServer,
    message: dict[str, Any],
) -> None:
    module = _load_module()
    action = {
        "kind": "request" if "id" in message else "notification",
        **message,
    }
    fake_server.configure(1, on_initialized=[action])
    fake_server.configure(2)
    client = _client(
        module,
        fake_server,
        protocol_contract=module._DEFAULT_PROTOCOL_CONTRACT,
    )

    client.start()
    try:
        _wait_until(lambda: client.ready and client.generation == 2)
    finally:
        client.close()


def test_locked_client_rejects_a_mismatched_app_server_version(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        initialize_result={
            "codexHome": str(fake_server.codex_home.resolve()),
            "platformFamily": "windows" if os.name == "nt" else "unix",
            "platformOs": "windows" if os.name == "nt" else "linux",
            "userAgent": "Codex Desktop/0.140.0 (test; x86_64)",
        }
    )
    client = _client(
        module,
        fake_server,
        protocol_contract=module._DEFAULT_PROTOCOL_CONTRACT,
    )

    with pytest.raises(module.AppServerProtocolError):
        client.start()


@pytest.mark.parametrize(
    ("mode", "action"),
    [
        ("malformed", {"mode": "malformed", "payload": "{definitely-not-json"}),
        ("oversize", {"mode": "oversize", "size": 4096}),
    ],
)
def test_protocol_violation_fails_pending_request_and_restarts_clean_generation(
    fake_server: FakeAppServer,
    mode: str,
    action: dict[str, object],
) -> None:
    module = _load_module()
    fake_server.configure(1, responses={"break/protocol": action})
    fake_server.configure(2)
    client = _client(module, fake_server, max_message_bytes=512)
    client.start()
    try:
        with pytest.raises(module.AppServerProtocolError):
            client.request("break/protocol", {"mode": mode})
        _wait_until(
            lambda: client.ready and client.generation == 2,
            message="client did not restart after a fatal protocol violation",
        )
        assert client.request("ping", {"generation": 2}) == {"echo": {"generation": 2}}
    finally:
        client.close()


def test_stderr_is_drained_and_diagnostics_are_redacted(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    diagnostics: list[str] = []
    fake_server.configure(
        responses={
            "stderr/flood": {
                "mode": "stderr_then_echo",
                "lines": [
                    "Authorization: Bearer reusable-secret",
                    "account person@example.com failed",
                    "ordinary diagnostic " + ("x" * 256),
                ],
                "repeat": 600,
            }
        }
    )
    client = _client(module, fake_server, stderr_diagnostic_sink=diagnostics.append)
    client.start()
    try:
        assert client.request("stderr/flood", {"ok": True}, timeout_seconds=2) == {
            "echo": {"ok": True}
        }
        _wait_until(lambda: len(diagnostics) == 1)
        emitted = "\n".join(diagnostics)
        assert "reusable-secret" not in emitted
        assert "person@example.com" not in emitted
        assert "[redacted]" in emitted.lower()
    finally:
        client.close()


def test_crash_fails_pending_request_and_old_server_request_cannot_cross_generation(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(
        1,
        on_initialized=[
            {
                "kind": "request",
                "id": "old-approval",
                "method": "approval/request",
                "params": {},
            }
        ],
        responses={"crash": {"mode": "crash"}},
    )
    fake_server.configure(2)
    client = _client(module, fake_server)
    old_request: list[Any] = []
    received = Event()

    def capture(request: Any) -> None:
        old_request.append(request)
        received.set()

    client.register_request_handler("approval/request", capture)
    client.start()
    try:
        assert received.wait(1)
        with pytest.raises(module.AppServerUnavailableError):
            client.request("crash")
        _wait_until(lambda: client.ready and client.generation == 2)
        with pytest.raises(module.AppServerStaleGenerationError):
            client.respond(old_request[0], result={"decision": "accept"})
        assert client.request("ping") == {"echo": None}
    finally:
        client.close()


def test_close_during_restart_backoff_prevents_another_process(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(responses={"crash": {"mode": "crash"}})
    client = _client(
        module,
        fake_server,
        restart_base_delay_seconds=0.5,
        restart_max_delay_seconds=0.5,
    )
    client.start()
    with pytest.raises(module.AppServerUnavailableError):
        client.request("crash")
    client.close()

    time.sleep(0.6)
    assert client.ready is False
    assert client.process_id is None
    assert not (fake_server.sidecars / "generation-2.claim").exists()


def _pid_is_running(pid: int) -> bool:
    if sys.platform.startswith("linux"):
        status = Path(f"/proc/{pid}/status")
        if not status.exists():
            return False
        return "State:\tZ" not in status.read_text(encoding="utf-8")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_close_terminates_the_entire_posix_process_group(
    fake_server: FakeAppServer,
) -> None:
    module = _load_module()
    fake_server.configure(spawn_child=True, ignore_sigterm=True)
    client = _client(module, fake_server, shutdown_grace_seconds=0.05)
    client.start()
    parent_pid = client.process_id
    child_pid = fake_server.process()["childPid"]
    assert parent_pid is not None
    assert _pid_is_running(parent_pid)
    assert _pid_is_running(child_pid)

    client.close()

    _wait_until(
        lambda: not _pid_is_running(parent_pid) and not _pid_is_running(child_pid),
        message="close left a process in the Codex app-server process group",
    )
    with pytest.raises(ProcessLookupError):
        os.kill(parent_pid, signal.SIGTERM)


class _LifecycleClient:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.start_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("synthetic startup failure")

    def close(self) -> None:
        self.close_calls += 1


class _LifecycleComponent:
    def start(self) -> None:
        pass

    def close(self) -> None:
        pass


class _ForbiddenModelProbe:
    def probe(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("HA startup must not launch a legacy model process")


def test_home_assistant_lifespan_owns_one_app_server_client(tmp_path: Path) -> None:
    managed = _LifecycleClient()
    factory_calls = 0
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def factory() -> _LifecycleClient:
        nonlocal factory_calls
        factory_calls += 1
        return managed

    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=factory,
        auth_coordinator_factory=lambda _client: _LifecycleComponent(),
        runner_factory=lambda _storage: _LifecycleComponent(),
        model_catalog_probe=_ForbiddenModelProbe(),
        initialize_special_projects=True,
    )

    assert factory_calls == 1
    assert app.state.codex_app_server is managed
    with TestClient(app):
        assert managed.start_calls == 1
        assert managed.close_calls == 0
    assert managed.close_calls == 1


def test_external_lifespan_never_constructs_an_app_server_client(
    tmp_path: Path,
) -> None:
    def forbidden_factory() -> _LifecycleClient:
        raise AssertionError("external legacy must not construct an app server")

    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        app_server_factory=forbidden_factory,
    )

    assert app.state.codex_app_server is None
    with TestClient(app):
        pass


def test_failed_home_assistant_startup_closes_partial_app_server(
    tmp_path: Path,
) -> None:
    managed = _LifecycleClient(fail_start=True)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=lambda: managed,
        auth_coordinator_factory=lambda _client: _LifecycleComponent(),
        runner_factory=lambda _storage: _LifecycleComponent(),
    )

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        with TestClient(app):
            pass

    assert managed.start_calls == 1
    assert managed.close_calls == 1
