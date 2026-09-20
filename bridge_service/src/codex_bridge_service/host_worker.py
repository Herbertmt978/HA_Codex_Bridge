"""Authenticated execution in the optional, separately privileged HAOS App."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request

from .host_access_contract import HostIdentity, HostInvocation, MAX_OUTPUT_BYTES

MAX_REQUESTS_PER_SESSION = 4096
MAX_ACTIVE_COMMANDS = 2


class HostWorkerError(RuntimeError):
    """A safe, fixed explanation suitable for the private API."""


@dataclass
class _Job:
    run_id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None


def _terminate_group(process: subprocess.Popen) -> None:
    # Only the process group created for this child. Root commands can leave it;
    # the disclosure explicitly does not promise containment of host root.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class HostWorker:
    def __init__(
        self, identity: HostIdentity, *,
        spawn: Callable = subprocess.Popen,
        terminate: Callable = _terminate_group,
        clock: Callable = time.time,
        monotonic: Callable = time.monotonic,
    ) -> None:
        self.identity = identity
        self.session_id = uuid4().hex
        self._spawn = spawn
        self._terminate = terminate
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._requests: set[str] = set()
        self._revoked_runs: set[str] = set()
        self._jobs: dict[str, _Job] = {}
        self._closed = False

    def status(self) -> dict[str, object]:
        return {
            "ready": not self._closed,
            "session_id": self.session_id,
            "identity": self.identity.model_dump(),
        }

    def execute(self, invocation: HostInvocation) -> dict[str, object]:
        with self._lock:
            if self._closed or invocation.run_id in self._revoked_runs:
                raise HostWorkerError("Host access was stopped for this run.")
            if (
                invocation.worker_session != self.session_id
                or invocation.scope_revision != self.identity.scope_revision
            ):
                raise HostWorkerError("The host access environment changed.")
            if not self._clock() < invocation.expires_at <= self._clock() + 30:
                raise HostWorkerError("The host access request expired.")
            if invocation.request_id in self._requests:
                raise HostWorkerError("This host command has already been submitted.")
            if len(self._requests) >= MAX_REQUESTS_PER_SESSION:
                raise HostWorkerError("Restart Host Access before submitting more commands.")
            if len(self._jobs) >= MAX_ACTIVE_COMMANDS:
                raise HostWorkerError("The host command limit has been reached.")
            # Reserve before starting. Never repeat a command after a lost reply.
            self._requests.add(invocation.request_id)
            job = _Job(invocation.run_id)
            self._jobs[invocation.request_id] = job
            try:
                job.process = self._spawn(
                    [sys.executable, "-m", "codex_bridge_service.host_entry", "--execute"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
                )
            except Exception:
                self._jobs.pop(invocation.request_id, None)
                raise HostWorkerError("The host command could not start.") from None
        try:
            return self._collect(job, invocation)
        finally:
            if job.process is not None:
                if job.process.returncode is None:
                    self._terminate(job.process)
                    job.process.wait(timeout=10)
                for stream in (job.process.stdin, job.process.stdout):
                    if stream is not None:
                        stream.close()
            with self._lock:
                self._jobs.pop(invocation.request_id, None)

    def _collect(self, job: _Job, invocation: HostInvocation) -> dict[str, object]:
        process = job.process
        assert process is not None and process.stdin is not None and process.stdout is not None
        # Input is bounded and consumed before the child runs the shell. Select
        # stdin as well as stdout so a stuck entrypoint cannot block cancellation.
        pending = memoryview(invocation.operation.model_dump_json().encode())
        output = bytearray()
        deadline = self._monotonic() + invocation.operation.timeout_seconds
        terminal = "completed"
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if job.cancelled.is_set():
                    terminal = "cancelled"
                    break
                if self._monotonic() >= deadline:
                    terminal = "timed_out"
                    break
                for key, _ in selector.select(0.05):
                    if key.fileobj is process.stdin:
                        try:
                            written = os.write(key.fd, pending)
                            pending = pending[written:]
                        except BrokenPipeError:
                            pending = pending[:0]
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, min(65536, MAX_OUTPUT_BYTES + 1 - len(output)))
                        if not chunk:
                            selector.unregister(process.stdout)
                        else:
                            output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    terminal = "output_limit"
                    break
            # A command can close output then continue running; still enforce
            # its deadline and cancellation rather than blocking in wait().
            while terminal == "completed" and os.waitid(
                os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT,
            ) is None:
                if job.cancelled.wait(0.05):
                    terminal = "cancelled"
                elif self._monotonic() >= deadline:
                    terminal = "timed_out"
        # Kill remaining group members before reaping the leader. A retained
        # zombie prevents PID reuse from ever selecting an unrelated group.
        self._terminate(process)
        return_code = process.wait(timeout=10)
        if terminal == "completed" and return_code != 0:
            terminal = "failed"
        return {
            "status": terminal, "exit_code": return_code,
            "output": bytes(output[:MAX_OUTPUT_BYTES]).decode("utf-8", errors="replace"),
            "truncated": len(output) > MAX_OUTPUT_BYTES,
        }

    def cancel(self, run_id: str) -> None:
        with self._lock:
            if len(self._revoked_runs) >= MAX_REQUESTS_PER_SESSION:
                self._closed = True
            self._revoked_runs.add(run_id)
            for job in self._jobs.values():
                if job.run_id == run_id or self._closed:
                    job.cancelled.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                job.cancelled.set()


def create_host_worker_app(worker: HostWorker, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError("Host Access needs a private credential")

    @asynccontextmanager
    async def lifespan(_app):
        yield
        worker.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def authenticate(authorization: str | None, version: str | None) -> None:
        if version != "1" or not hmac.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Host Access authentication required")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Check before Pydantic can put command values into validation errors.
        try:
            authenticate(request.headers.get("authorization"), request.headers.get("x-codex-host-api"))
        except HTTPException:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Host Access authentication required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({"detail": "Invalid host access request"}, status_code=422)

    @app.get("/status")
    def status():
        return worker.status()

    @app.post("/execute")
    async def execute(request: Request):
        from pydantic import ValidationError
        from starlette.concurrency import run_in_threadpool
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 160 * 1024:
                raise HTTPException(status_code=413, detail="Host request is too large")
        try:
            payload = HostInvocation.model_validate_json(bytes(body))
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid host access request") from None
        try:
            return await run_in_threadpool(worker.execute, payload)
        except HostWorkerError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @app.delete("/runs/{run_id}")
    def cancel(run_id: str, x_codex_host_session: str | None = Header(default=None)):
        if x_codex_host_session != worker.session_id or len(run_id) > 128:
            raise HTTPException(status_code=409, detail="Host Access session changed")
        worker.cancel(run_id)
        return {"stopped": True}

    return app
