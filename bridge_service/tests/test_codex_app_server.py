from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import importlib
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event
import time
from types import ModuleType
from typing import Any, Callable

import pytest


FAKE_APP_SERVER = Path(__file__).with_name("fakes") / "fake_app_server.py"


class FakeAppServer:
    def __init__(self, root: Path) -> None:
        self.codex_home = root / "codex-home"
        self.sidecars = self.codex_home / ".fake-app-server"
        self.sidecars.mkdir(parents=True)
        self.command = str(FAKE_APP_SERVER)

    def configure(self, generation: int = 1, **scenario: Any) -> None:
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
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

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


def _client(module: ModuleType, fake_server: FakeAppServer, **overrides: Any) -> Any:
    options: dict[str, object] = {
        "codex_command": fake_server.command,
        "codex_home": fake_server.codex_home,
        "client_name": "ha_codex_bridge",
        "client_title": "HA Codex Bridge",
        "client_version": "0.6.0",
        "initialize_timeout_seconds": 1.0,
        "request_timeout_seconds": 1.0,
        "max_message_bytes": 16 * 1024,
        "max_pending_requests": 8,
        "callback_workers": 2,
        "max_callback_queue": 8,
        "restart_base_delay_seconds": 0.03,
        "restart_max_delay_seconds": 0.1,
        "restart_stable_seconds": 0.2,
        "shutdown_grace_seconds": 0.05,
    }
    options.update(overrides)
    return module.CodexAppServerClient(**options)


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


def _wait_for_client_message(
    fake_server: FakeAppServer,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    generation: int = 1,
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []

    def locate() -> bool:
        found[:] = [
            message for message in fake_server.client_messages(generation) if predicate(message)
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
            "version": "0.6.0",
        }
        assert fake_server.process()["argv"] == ["app-server", "--stdio"]
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
    assert client.process_id is None


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
            second = executor.submit(client.request, "echo/reverse", {"value": "second"})
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


def test_bounded_callback_queue_drops_excess_notifications_without_blocking_reader(
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
        assert client.request("emit/flood") == {"echo": None}
        time.sleep(0.05)
        assert len(calls) == 1
        release.set()
        _wait_until(lambda: len(calls) == 2)
        assert len(calls) == 2
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
                    message.get("method") == "hold" for message in fake_server.client_messages()
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
    fake_server.configure(
        responses={"slow": {"mode": "delay", "delay_seconds": 0.15}}
    )
    client = _client(module, fake_server, max_pending_requests=1)
    client.start()

    try:
        with pytest.raises(module.AppServerTimeoutError):
            client.request("slow", {"request": "old"}, timeout_seconds=0.03)
        time.sleep(0.2)
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
                "error": {"code": -32042, "message": "request rejected", "data": {"retry": False}},
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


def test_initialize_timeout_is_typed_and_close_stops_retries(fake_server: FakeAppServer) -> None:
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
        assert client.request("ping", {"generation": 2}) == {
            "echo": {"generation": 2}
        }
    finally:
        client.close()


def test_stderr_is_drained_and_diagnostics_are_redacted(fake_server: FakeAppServer) -> None:
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
        _wait_until(lambda: len(diagnostics) >= 3)
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
            {"kind": "request", "id": "old-approval", "method": "approval/request", "params": {}}
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


def test_close_during_restart_backoff_prevents_another_process(fake_server: FakeAppServer) -> None:
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
def test_close_terminates_the_entire_posix_process_group(fake_server: FakeAppServer) -> None:
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
