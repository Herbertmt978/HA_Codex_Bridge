"""Private JSONL client for the fixed App-owned browser worker.

The browser worker is intentionally not a web service.  The Bridge speaks to
one local executable over its inherited stdin/stdout pipes, and only after the
root-owned startup proof says that Chromium and the egress boundary are live.
This module deliberately knows nothing about CDP, WebDriver, or arbitrary
browser commands; those implementation details never cross the Bridge/model
boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import stat
import subprocess
from threading import RLock
from typing import Callable, Protocol

from .browser_contract import BrowserContractError, parse_browser_action


BROWSER_WORKER_PATH = Path("/usr/local/libexec/codex-bridge/browser_worker.py")
BROWSER_WORKER_ATTESTATION_PATH = Path(
    "/run/codex-bridge/browser-worker-attestation.json"
)
BROWSER_WORKER_PROTOCOL = "browser-worker-v1"
CHROMIUM_VERSION = "150.0.7871.124"
MAX_REQUEST_BYTES = 64 * 1024
# An 8 MiB PDF may expand to just under 11.2 MiB in base64 plus its JSON shell.
MAX_RESPONSE_BYTES = 12 * 1024 * 1024


class BrowserWorkerClientError(RuntimeError):
    """The local browser helper did not satisfy its private IPC contract."""


class _WorkerProcess(Protocol):
    stdin: object
    stdout: object

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[..., _WorkerProcess]
ProofVerifier = Callable[[Path], bool]


def _strict_json(payload: bytes) -> object:
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise BrowserWorkerClientError("browser worker response is invalid")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise BrowserWorkerClientError("browser worker response is invalid")
            document[key] = value
        return document

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserWorkerClientError("browser worker response is invalid") from exc
    if not isinstance(value, dict):
        raise BrowserWorkerClientError("browser worker response is invalid")
    return value


def browser_worker_attestation_ready(
    path: Path = BROWSER_WORKER_ATTESTATION_PATH,
) -> bool:
    """Accept only the root-created, boot-local browser-worker proof.

    The proof is intentionally separate from the Codex sandbox attestation:
    Chromium has its own sandbox and connection-time egress conditions.  It is
    not created by this client, and a missing or malformed proof is simply not
    ready rather than an invitation to launch an unproven browser.
    """

    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or mode & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > 4096
        ):
            return False
        payload = os.read(descriptor, metadata.st_size + 1)
        if len(payload) != metadata.st_size or os.read(descriptor, 1):
            return False
        document = _strict_json(payload)
    except (BrowserWorkerClientError, OSError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return document == {
        "schema_version": 1,
        "worker_protocol": BROWSER_WORKER_PROTOCOL,
        "chromium_version": CHROMIUM_VERSION,
        "chromium_sandbox": "ready",
        "egress_boundary": "ready",
    }


class BrowserWorkerClient:
    """One private worker process implementing :class:`BrowserWorker`.

    It is deliberately synchronous because the runtime broker serializes the
    active turn.  Each line is bounded, write-only stdin and read-only stdout;
    there is no TCP, Unix-domain socket, CDP endpoint, or browser-visible
    listener owned by this client.
    """

    def __init__(
        self,
        *,
        worker_path: Path = BROWSER_WORKER_PATH,
        attestation_path: Path = BROWSER_WORKER_ATTESTATION_PATH,
        proof_verifier: ProofVerifier = browser_worker_attestation_ready,
        process_factory: ProcessFactory = subprocess.Popen,
        response_timeout_seconds: float = 35.0,
    ) -> None:
        if not 1.0 <= response_timeout_seconds <= 40.0:
            raise ValueError("browser worker response timeout is invalid")
        self._worker_path = worker_path
        self._attestation_path = attestation_path
        self._proof_verifier = proof_verifier
        self._process_factory = process_factory
        self._response_timeout_seconds = float(response_timeout_seconds)
        self._process: _WorkerProcess | None = None
        self._closed = False
        self._lock = RLock()

    def ready(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            return self._proof_verifier(self._attestation_path) is True
        except BaseException:
            return False

    def execute(self, action: object, *, session_id: str) -> object:
        """Send one validated high-level action through the private pipe."""

        if not isinstance(session_id, str) or not session_id.startswith("brs_"):
            raise BrowserWorkerClientError("browser session is invalid")
        try:
            parsed_action = parse_browser_action(action)
        except BrowserContractError as exc:
            raise BrowserWorkerClientError("browser action is invalid") from exc
        if not self.ready():
            raise BrowserWorkerClientError("browser worker is not proven ready")
        payload = {
            "session_id": session_id,
            "action": parsed_action.model_dump(mode="json"),
        }
        return self._request(payload)

    def close_session(self, session_id: str) -> None:
        """Destroy the worker's matching ephemeral profile, if it exists.

        The private worker handles one Chromium session at a time.  It cannot
        process a JSONL ``close_session`` line while an action is blocked, so
        cancellation deliberately terminates its process group instead.  The
        next authorised session gets a fresh worker and fresh profile.
        """

        if not isinstance(session_id, str) or not session_id.startswith("brs_"):
            return
        self._stop_process()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._stop_process()

    def _request(self, document: dict[str, object]) -> object:
        encoded = _encode_request(document)
        with self._lock:
            if self._closed:
                raise BrowserWorkerClientError("browser worker is closed")
            process = self._start_locked()
            stdin = getattr(process, "stdin", None)
            stdout = getattr(process, "stdout", None)
            if stdin is None or stdout is None:
                self._stop_process_locked()
                raise BrowserWorkerClientError("browser worker pipes are unavailable")
        try:
            # Do not hold the client lock while the worker is running.  A
            # terminal runtime transition must be able to stop this exact
            # process and interrupt the pending pipe read.
            stdin.write(encoded)
            stdin.flush()
            line = _readline_with_timeout(stdout, self._response_timeout_seconds)
            return _strict_json(line)
        except (BrowserWorkerClientError, OSError, ValueError, TypeError):
            self._stop_process(process)
            raise BrowserWorkerClientError("browser worker request failed") from None

    def _start_locked(self) -> _WorkerProcess:
        current = self._process
        if current is not None and current.poll() is None:
            return current
        if not self.ready():
            raise BrowserWorkerClientError("browser worker is not proven ready")
        environment = {
            "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/tmp/codex-bridge-browser",
            "TMPDIR": "/tmp/codex-bridge-browser",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        try:
            process = self._process_factory(
                [str(self._worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                bufsize=0,
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BrowserWorkerClientError("browser worker could not start") from exc
        self._process = process
        return process

    def _stop_process(self, expected: _WorkerProcess | None = None) -> None:
        with self._lock:
            if expected is not None and self._process is not expected:
                return
            process, self._process = self._process, None
        self._terminate_process(process)

    def _stop_process_locked(self) -> None:
        process, self._process = self._process, None
        self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: _WorkerProcess | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError, TimeoutError):
                return


def _encode_request(document: dict[str, object]) -> bytes:
    try:
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise BrowserWorkerClientError("browser worker request is invalid") from exc
    if not payload or len(payload) > MAX_REQUEST_BYTES:
        raise BrowserWorkerClientError("browser worker request is invalid")
    return payload + b"\n"


def _readline_with_timeout(stream: object, timeout_seconds: float) -> bytes:
    """Read one bounded line without allowing a wedged worker to block a turn."""

    fileno = getattr(stream, "fileno", None)
    readline = getattr(stream, "readline", None)
    if not callable(fileno) or not callable(readline):
        raise BrowserWorkerClientError("browser worker pipes are unavailable")
    try:
        ready, _, _ = select.select([fileno()], [], [], timeout_seconds)
    except (OSError, ValueError) as exc:
        raise BrowserWorkerClientError("browser worker pipes are unavailable") from exc
    if not ready:
        raise BrowserWorkerClientError("browser worker timed out")
    value = readline(MAX_RESPONSE_BYTES + 2)
    if not isinstance(value, bytes) or not value.endswith(b"\n") or len(value) > MAX_RESPONSE_BYTES + 1:
        raise BrowserWorkerClientError("browser worker response is invalid")
    return value[:-1]
