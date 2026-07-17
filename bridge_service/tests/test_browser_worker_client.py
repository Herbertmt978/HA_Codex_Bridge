"""Contracts for the private App-owned browser-worker process boundary."""

from __future__ import annotations

import io
import json
from pathlib import Path
from threading import Event, Thread
import time

import pytest

from codex_bridge_service import browser_worker_client as client_module
from codex_bridge_service.browser_worker_client import (
    BROWSER_WORKER_PROTOCOL,
    BrowserWorkerClient,
    BrowserWorkerClientError,
)


class _Process:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = object()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def test_unproven_worker_never_starts_even_for_a_valid_open() -> None:
    started = False

    def factory(*_args, **_kwargs):
        nonlocal started
        started = True
        return _Process()

    worker = BrowserWorkerClient(
        proof_verifier=lambda _path: False,
        process_factory=factory,
    )

    assert worker.ready() is False
    with pytest.raises(BrowserWorkerClientError):
        worker.execute(
            {"action": "open", "url": "https://example.com"},
            session_id="brs_0123456789abcdef",
        )
    assert started is False


def test_worker_uses_only_private_jsonl_pipes_and_scrubbed_environment(monkeypatch) -> None:
    process = _Process()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    responses = iter(
        [
            b'{"status":"ok","session_id":"brs_0123456789abcdef","page":{"url":"https://example.com/","title":"Example","text":""}}\n',
        ]
    )

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(client_module, "_readline_with_timeout", lambda *_args: next(responses))
    worker = BrowserWorkerClient(
        proof_verifier=lambda _path: True,
        process_factory=factory,
        worker_path=Path("/fixed/browser_worker.py"),
    )

    response = worker.execute(
        {"action": "open", "url": "https://example.com"},
        session_id="brs_0123456789abcdef",
    )
    worker.close_session("brs_0123456789abcdef")

    assert response["status"] == "ok"
    assert calls and calls[0][0] == ([str(Path("/fixed/browser_worker.py"))],)
    kwargs = calls[0][1]
    assert kwargs["stdin"] is client_module.subprocess.PIPE
    assert kwargs["stdout"] is client_module.subprocess.PIPE
    assert kwargs["stderr"] is client_module.subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert kwargs["start_new_session"] is True
    assert kwargs["env"] == {
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp/codex-bridge-browser",
        "TMPDIR": "/tmp/codex-bridge-browser",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    requests = process.stdin.getvalue().splitlines()
    assert len(requests) == 1
    assert json.loads(requests[0]) == {
        "action": {"action": "open", "timeout_ms": 15000, "url": "https://example.com/", "wait_until": "domcontentloaded"},
        "session_id": "brs_0123456789abcdef",
    }
    # A close interrupts the one private worker process rather than waiting
    # behind a potentially stuck JSONL action.
    assert process.terminated is True


def test_private_worker_rejects_invalid_action_before_spawning() -> None:
    worker = BrowserWorkerClient(
        proof_verifier=lambda _path: True,
        process_factory=lambda *_args, **_kwargs: pytest.fail("must not spawn"),
    )

    with pytest.raises(BrowserWorkerClientError):
        worker.execute(
            {"action": "evaluate", "script": "1 + 1"},
            session_id="brs_0123456789abcdef",
        )


def test_worker_failure_or_malformed_response_kills_the_helper(monkeypatch) -> None:
    process = _Process()
    monkeypatch.setattr(client_module, "_readline_with_timeout", lambda *_args: b"not-json\n")
    worker = BrowserWorkerClient(
        proof_verifier=lambda _path: True,
        process_factory=lambda *_args, **_kwargs: process,
    )

    with pytest.raises(BrowserWorkerClientError):
        worker.execute(
            {"action": "open", "url": "https://example.com"},
            session_id="brs_0123456789abcdef",
        )
    assert process.terminated is True


def test_close_session_interrupts_a_pending_pipe_read(monkeypatch) -> None:
    process = _Process()
    entered = Event()

    def blocked_read(*_args: object) -> bytes:
        entered.set()
        deadline = time.monotonic() + 2
        while not process.terminated and time.monotonic() < deadline:
            time.sleep(0.01)
        raise client_module.BrowserWorkerClientError("worker stopped")

    monkeypatch.setattr(client_module, "_readline_with_timeout", blocked_read)
    worker = BrowserWorkerClient(
        proof_verifier=lambda _path: True,
        process_factory=lambda *_args, **_kwargs: process,
    )
    failure: list[BaseException] = []
    request = Thread(
        target=lambda: _capture_failure(
            failure,
            lambda: worker.execute(
                {"action": "open", "url": "https://example.com"},
                session_id="brs_0123456789abcdef",
            ),
        ),
        daemon=True,
    )
    request.start()
    assert entered.wait(1)

    worker.close_session("brs_0123456789abcdef")

    request.join(timeout=2)
    assert not request.is_alive()
    assert process.terminated is True
    assert len(failure) == 1
    assert isinstance(failure[0], BrowserWorkerClientError)


def _capture_failure(failures: list[BaseException], callback) -> None:
    try:
        callback()
    except BaseException as exc:  # pragma: no branch - test-only capture
        failures.append(exc)


def test_attestation_shape_is_exact_and_not_created_by_the_client(tmp_path: Path, monkeypatch) -> None:
    proof = tmp_path / "browser-worker-attestation.json"
    proof.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "worker_protocol": BROWSER_WORKER_PROTOCOL,
                "chromium_version": "150.0.7871.124",
                "chromium_sandbox": "ready",
                "egress_boundary": "ready",
            }
        ),
        encoding="utf-8",
    )

    # A user-owned test file is not a valid root-side proof.  This assertion is
    # portable because production verification also rejects symlinks/modes.
    assert client_module.browser_worker_attestation_ready(proof) is False
    assert proof.read_text(encoding="utf-8")
