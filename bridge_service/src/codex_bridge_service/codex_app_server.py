from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any, BinaryIO, Final, Literal

from .codex_app_server_contract import (
    AppServerProtocolContract,
    AppServerProtocolValidator,
    ProtocolContractError,
    load_bundled_protocol_contract,
)
from .codex_process import (
    codex_command_prefix,
    codex_subprocess_environment,
    resolve_codex_home,
)

JsonValue = Any
RequestId = str | int

_MISSING: Final = object()
DEFERRED_RESPONSE: Final = object()
_CALLBACK_STOP: Final = object()
_REMOTE_OVERLOAD_CODE = -32001
_APP_SERVER_VERSION_PATTERN = re.compile(
    r"\A[^/\r\n]{1,128}/([0-9]+\.[0-9]+\.[0-9]+)(?:[\s(;]|$)",
    re.ASCII,
)
_METHOD_NOT_FOUND_CODE = -32601
_INTERNAL_ERROR_CODE = -32603
_MAX_REQUEST_ID_TEXT_BYTES = 256
_MAX_METHOD_BYTES = 512
_RETIRED_ID_LIMIT = 2048
_STDERR_READ_BYTES = 16 * 1024
_DEFAULT_MAX_MESSAGE_BYTES = 8 * 1024 * 1024
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
_MODEL_PROVIDER_CAPABILITIES_TIMEOUT_SECONDS = 5.0
_DEFAULT_PROTOCOL_CONTRACT = load_bundled_protocol_contract()
_DEFAULT_PROTOCOL_VALIDATOR = AppServerProtocolValidator(_DEFAULT_PROTOCOL_CONTRACT)


class CodexAppServerError(RuntimeError):
    """Base class whose public text never includes untrusted server content."""


class AppServerUnavailableError(CodexAppServerError):
    def __init__(self) -> None:
        super().__init__("The Codex app server is unavailable.")


class AppServerTimeoutError(CodexAppServerError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__("The Codex app server request timed out.")


class AppServerOverloadedError(CodexAppServerError):
    def __init__(self) -> None:
        super().__init__("The Codex app server client is busy; retry later.")


class AppServerProtocolError(CodexAppServerError):
    def __init__(self) -> None:
        super().__init__("The Codex app server protocol stream is invalid.")


class AppServerRemoteError(CodexAppServerError):
    def __init__(self, *, method: str, code: int) -> None:
        self.method = method
        self.code = code
        self.retryable = code == _REMOTE_OVERLOAD_CODE
        super().__init__("The Codex app server rejected the request.")


class AppServerStaleGenerationError(CodexAppServerError):
    def __init__(self) -> None:
        super().__init__("The Codex app server request is no longer active.")


@dataclass(frozen=True, slots=True)
class AppServerNotification:
    method: str
    params: JsonValue
    generation: int


@dataclass(frozen=True, slots=True)
class AppServerRequest:
    request_id: RequestId
    method: str
    params: JsonValue
    generation: int


@dataclass(frozen=True, slots=True)
class AppServerResponseError:
    code: int
    message: str
    data: JsonValue = _MISSING

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            raise ValueError("response error code must be an integer")
        _validate_safe_response_message(self.message)


@dataclass(frozen=True, slots=True)
class ModelProviderCapabilities:
    """Validated provider capability flags for one app-server generation."""

    generation: int
    image_generation: bool
    web_search: bool
    namespace_tools: bool


@dataclass(slots=True)
class _PendingRequest:
    method: str
    future: Future[JsonValue]


class _DuplicateJsonKey(ValueError):
    pass


class _BoundedCallbackDispatcher:
    def __init__(self, *, workers: int, queued_callbacks: int) -> None:
        if type(workers) is not int or workers <= 0:
            raise ValueError("callback workers must be positive")
        if type(queued_callbacks) is not int or queued_callbacks <= 0:
            raise ValueError("callback queue size must be positive")
        self._queue: queue.Queue[object] = queue.Queue(maxsize=queued_callbacks)
        self._workers_count = workers
        self._threads: list[threading.Thread] = []
        self._started = False
        self._closed = threading.Event()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self._workers_count):
            worker = threading.Thread(
                target=self._run,
                name=f"CodexAppServerCallback-{index + 1}",
                daemon=True,
            )
            self._threads.append(worker)
            worker.start()

    def submit(self, callback: Callable[[], None]) -> bool:
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(callback)
            return True
        except queue.Full:
            return False

    def close(self, *, join_timeout_seconds: float) -> None:
        if not self._started or self._closed.is_set():
            return
        self._closed.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
        self._queue.put_nowait(_CALLBACK_STOP)
        deadline = monotonic() + max(join_timeout_seconds, 0)
        current = threading.current_thread()
        for worker in self._threads:
            if worker is current:
                continue
            worker.join(timeout=max(0, deadline - monotonic()))

    def _run(self) -> None:
        while True:
            callback = self._queue.get()
            try:
                if callback is _CALLBACK_STOP:
                    try:
                        self._queue.put_nowait(_CALLBACK_STOP)
                    except queue.Full:
                        pass
                    return
                assert callable(callback)
                callback()
            except BaseException:
                # Callback failures are converted to safe protocol responses by the
                # wrapper submitted by CodexAppServerClient.
                pass
            finally:
                self._queue.task_done()


class CodexAppServerClient:
    """Bounded, generation-aware owner of one Codex app-server subprocess."""

    def __init__(
        self,
        *,
        codex_command: str = "codex",
        codex_home: Path | str | None = None,
        client_name: str = "ha_codex_bridge",
        client_title: str = "Home Assistant Codex Bridge",
        client_version: str = "0.7.1",
        initialize_timeout_seconds: float = 10.0,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
        max_pending_requests: int = 64,
        callback_workers: int = 4,
        max_callback_queue: int = 64,
        restart_base_delay_seconds: float = 0.25,
        restart_max_delay_seconds: float = 30.0,
        restart_stable_seconds: float = 60.0,
        shutdown_grace_seconds: float = 5.0,
        stderr_diagnostic_sink: Callable[[str], None] | None = None,
        enable_mcp: bool = False,
        protocol_contract: AppServerProtocolContract
        | None = _DEFAULT_PROTOCOL_CONTRACT,
    ) -> None:
        self.codex_command = codex_command
        self.codex_home = resolve_codex_home(codex_home, codex_command)
        self.client_name = _validate_client_info(client_name, "client name")
        self.client_title = _validate_client_info(client_title, "client title")
        self.client_version = _validate_client_info(client_version, "client version")
        self.initialize_timeout_seconds = _positive_number(
            initialize_timeout_seconds,
            "initialize timeout",
        )
        self.request_timeout_seconds = _positive_number(
            request_timeout_seconds,
            "request timeout",
        )
        if type(max_message_bytes) is not int or max_message_bytes < 256:
            raise ValueError("message byte limit must be at least 256")
        if type(max_pending_requests) is not int or max_pending_requests <= 0:
            raise ValueError("pending request limit must be positive")
        self.max_message_bytes = max_message_bytes
        self.max_pending_requests = max_pending_requests
        self.restart_base_delay_seconds = _positive_number(
            restart_base_delay_seconds,
            "restart base delay",
        )
        self.restart_max_delay_seconds = _positive_number(
            restart_max_delay_seconds,
            "restart maximum delay",
        )
        if self.restart_max_delay_seconds < self.restart_base_delay_seconds:
            raise ValueError("restart maximum delay must cover the base delay")
        self.restart_stable_seconds = _positive_number(
            restart_stable_seconds,
            "restart stable interval",
        )
        self.shutdown_grace_seconds = _positive_number(
            shutdown_grace_seconds,
            "shutdown grace",
        )
        self._stderr_diagnostic_sink = stderr_diagnostic_sink
        if type(enable_mcp) is not bool:
            raise ValueError("MCP enabled state must be a boolean")
        self.enable_mcp = enable_mcp
        self.protocol_contract = protocol_contract
        self._protocol_validator = (
            None
            if protocol_contract is None
            else (
                _DEFAULT_PROTOCOL_VALIDATOR
                if protocol_contract is _DEFAULT_PROTOCOL_CONTRACT
                else AppServerProtocolValidator(protocol_contract)
            )
        )

        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closing = threading.Event()
        self._startup_complete = threading.Event()
        self._ready = threading.Event()
        # MCP must never be loaded from a persisted configuration during the
        # bootstrap generation.  The manager explicitly activates it only
        # after it has replaced that persisted configuration with its validated
        # projection.
        self._mcp_startup_state: Literal["masked", "activating", "active"] = "masked"
        self._mcp_activation_generation: int | None = None
        self._mcp_activation_complete = threading.Event()
        self._closed = False
        self._ever_ready = False
        self._startup_error: CodexAppServerError | None = None
        self._generation = 0
        self._server_version: str | None = None
        self._dispatch_generation: int | None = None
        self._next_request_id = 1
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._supervisor_thread: threading.Thread | None = None
        self._generation_failures: dict[int, CodexAppServerError] = {}
        self._pending: dict[tuple[int, RequestId], _PendingRequest] = {}
        self._retired_ids: deque[tuple[int, RequestId]] = deque()
        self._retired_id_set: set[tuple[int, RequestId]] = set()
        self._server_requests: set[tuple[int, RequestId]] = set()
        self._notification_handlers: dict[
            str,
            Callable[[AppServerNotification], None],
        ] = {}
        self._request_handlers: dict[
            str,
            Callable[[AppServerRequest], JsonValue],
        ] = {}
        self._stderr_bytes: dict[int, int] = {}
        self._stderr_chunks: dict[int, int] = {}
        self._stderr_reported: set[int] = set()
        self._dispatcher = _BoundedCallbackDispatcher(
            workers=callback_workers,
            queued_callbacks=max_callback_queue,
        )

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def server_version(self) -> str | None:
        with self._state_lock:
            return self._server_version if self._ready.is_set() else None

    @property
    def process_id(self) -> int | None:
        with self._state_lock:
            process = self._process
            return (
                process.pid if process is not None and process.poll() is None else None
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise AppServerUnavailableError()
            if self._ready.is_set():
                return
            if self._supervisor_thread is None:
                self._validate_start_paths()
                self._dispatcher.start()
                supervisor = threading.Thread(
                    target=self._supervise,
                    name="CodexAppServerSupervisor",
                    daemon=True,
                )
                self._supervisor_thread = supervisor
                supervisor.start()

        wait_seconds = self.initialize_timeout_seconds + self.shutdown_grace_seconds + 1
        if not self._startup_complete.wait(timeout=wait_seconds):
            self.close()
            raise AppServerTimeoutError("initialize")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise error

    def request(
        self,
        method: str,
        params: JsonValue = None,
        *,
        timeout_seconds: float | None = None,
    ) -> JsonValue:
        method = _validate_method(method)
        self._require_contract_method("clientRequests", method)
        with self._state_lock:
            if not self._ready.is_set() or self._process is None:
                raise AppServerUnavailableError()
            generation = self._generation
        return self._request_for_generation(
            generation,
            method,
            params,
            timeout_seconds=(
                self.request_timeout_seconds
                if timeout_seconds is None
                else _positive_number(timeout_seconds, "request timeout")
            ),
            require_ready=True,
        )

    def read_model_provider_capabilities(self) -> ModelProviderCapabilities:
        """Read bounded, generation-correlated native provider capabilities."""

        method = "modelProvider/capabilities/read"
        self._require_contract_method("clientRequests", method)
        with self._state_lock:
            if not self._ready.is_set() or self._process is None:
                raise AppServerUnavailableError()
            generation = self._generation
        result = self._request_for_generation(
            generation,
            method,
            {},
            timeout_seconds=min(
                self.request_timeout_seconds,
                _MODEL_PROVIDER_CAPABILITIES_TIMEOUT_SECONDS,
            ),
            require_ready=True,
        )
        if not isinstance(result, dict):
            raise AppServerProtocolError()
        values = (
            result.get("imageGeneration"),
            result.get("webSearch"),
            result.get("namespaceTools"),
        )
        if any(type(value) is not bool for value in values):
            raise AppServerProtocolError()
        image_generation, web_search, namespace_tools = values
        assert isinstance(image_generation, bool)
        assert isinstance(web_search, bool)
        assert isinstance(namespace_tools, bool)
        return ModelProviderCapabilities(
            generation=generation,
            image_generation=image_generation,
            web_search=web_search,
            namespace_tools=namespace_tools,
        )

    def register_notification_handler(
        self,
        method: str,
        handler: Callable[[AppServerNotification], None],
    ) -> None:
        method = _validate_method(method)
        self._require_contract_method("serverNotifications", method)
        if not callable(handler):
            raise TypeError("notification handler must be callable")
        with self._state_lock:
            self._notification_handlers[method] = handler

    def register_request_handler(
        self,
        method: str,
        handler: Callable[[AppServerRequest], JsonValue],
    ) -> None:
        method = _validate_method(method)
        self._require_contract_method("serverRequests", method)
        if not callable(handler):
            raise TypeError("request handler must be callable")
        with self._state_lock:
            self._request_handlers[method] = handler

    def respond(
        self,
        request: AppServerRequest,
        *,
        result: JsonValue = _MISSING,
        error: AppServerResponseError | None = None,
    ) -> None:
        if not isinstance(request, AppServerRequest):
            raise TypeError("response requires an app-server request")
        if (result is _MISSING) == (error is None):
            raise ValueError("provide exactly one response result or error")
        key = (request.generation, request.request_id)
        with self._state_lock:
            if (
                request.generation != self._generation
                or key not in self._server_requests
            ):
                raise AppServerStaleGenerationError()
            self._server_requests.remove(key)
        if error is None:
            message = {"id": request.request_id, "result": result}
        else:
            error_payload: dict[str, JsonValue] = {
                "code": error.code,
                "message": error.message,
            }
            if error.data is not _MISSING:
                error_payload["data"] = error.data
            message = {"id": request.request_id, "error": error_payload}
        try:
            validator = self._protocol_validator
            if validator is not None:
                if error is None:
                    validator.validate_server_response(request.method, result=result)
                else:
                    validator.validate_server_response(
                        request.method,
                        error_message={
                            "id": request.request_id,
                            "error": error_payload,
                        },
                        is_error=True,
                    )
            self._write_message(request.generation, message)
        except ProtocolContractError:
            self._mark_generation_failed(
                request.generation,
                AppServerProtocolError(),
            )
            raise AppServerProtocolError() from None
        except AppServerProtocolError:
            self._mark_generation_failed(
                request.generation,
                AppServerProtocolError(),
            )
            raise
        except BaseException:
            # The response token is single-use even if the generation disappears
            # during the write. Replaying it into a restart would be unsafe.
            raise

    def discard_server_request(
        self,
        request_id: RequestId,
        expected_generation: int,
    ) -> bool:
        """Invalidate one retained server-request token if its generation matches."""
        request_id = _validate_request_id(request_id)
        expected_generation = _validate_expected_generation(expected_generation)
        key = (expected_generation, request_id)
        with self._state_lock:
            if (
                self._closed
                or self._closing.is_set()
                or expected_generation != self._generation
                or key not in self._server_requests
            ):
                return False
            self._server_requests.remove(key)
            return True

    def abort_generation(self, expected_generation: int) -> bool:
        """Fail and restart only the currently matching app-server generation."""
        expected_generation = _validate_expected_generation(expected_generation)
        error = AppServerUnavailableError()
        with self._state_lock:
            process = self._process
            if (
                self._closed
                or self._closing.is_set()
                or expected_generation != self._generation
                or expected_generation in self._generation_failures
                or process is None
                or process.poll() is not None
            ):
                return False
            self._generation_failures[expected_generation] = error
            self._ready.clear()
            if self._dispatch_generation == expected_generation:
                self._dispatch_generation = None
            self._server_requests = {
                key for key in self._server_requests if key[0] != expected_generation
            }
        self._fail_generation_pending(expected_generation, type(error))
        _close_stdin(process)
        _terminate_process_group(process)
        stopper = threading.Thread(
            target=_force_stop_aborted_process,
            args=(process, self.shutdown_grace_seconds),
            name=f"CodexAppServerAbort-{expected_generation}",
            daemon=True,
        )
        stopper.start()
        return True

    def activate_validated_mcp_config(self) -> None:
        """Restart into the validated MCP configuration after a safe bootstrap.

        The first generation is always launched with an empty session override.
        Only the MCP manager may call this after it atomically replaces and
        reloads the persisted MCP configuration.  A failed activation restores
        the empty override before another generation can be launched.
        """

        if not self.enable_mcp:
            raise AppServerUnavailableError()
        with self._lifecycle_lock:
            with self._state_lock:
                if self._mcp_startup_state == "active":
                    return
                process = self._process
                generation = self._generation
                if (
                    self._mcp_startup_state != "masked"
                    or not self._ready.is_set()
                    or process is None
                    or process.poll() is not None
                ):
                    raise AppServerUnavailableError()
                self._mcp_startup_state = "activating"
                self._mcp_activation_generation = generation + 1
                self._mcp_activation_complete.clear()
            if not self.abort_generation(generation):
                self._restore_mcp_bootstrap(generation + 1)
                raise AppServerUnavailableError()

        wait_seconds = (
            self.initialize_timeout_seconds
            + self.shutdown_grace_seconds
            + self.restart_max_delay_seconds
            + 1
        )
        if not self._mcp_activation_complete.wait(timeout=wait_seconds):
            with self._state_lock:
                generation = self._generation
            # Cleanup can be delayed while the old bootstrap generation is
            # still current.  Restore by activation state, rather than that
            # generation number, before any later spawn can inspect it.
            self._restore_mcp_bootstrap()
            self.abort_generation(generation)
            raise AppServerUnavailableError()
        with self._state_lock:
            if self._mcp_startup_state != "active" or not self._ready.is_set():
                raise AppServerUnavailableError()

    def _activate_mcp_generation(self, generation: int) -> None:
        with self._state_lock:
            if (
                self._mcp_startup_state == "activating"
                and self._mcp_activation_generation == generation
            ):
                self._mcp_startup_state = "active"
                self._mcp_activation_generation = None
                self._mcp_activation_complete.set()

    def _restore_mcp_bootstrap(self, generation: int | None = None) -> None:
        with self._state_lock:
            if self._mcp_startup_state == "activating" and (
                generation is None or self._mcp_activation_generation == generation
            ):
                self._mcp_startup_state = "masked"
                self._mcp_activation_generation = None
                self._mcp_activation_complete.set()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._closing.set()
            self._ready.clear()
            with self._state_lock:
                self._mcp_startup_state = "masked"
                self._mcp_activation_generation = None
                self._mcp_activation_complete.set()
                process = self._process
                supervisor = self._supervisor_thread
            if process is not None:
                _close_stdin(process)

        if supervisor is not None and supervisor is not threading.current_thread():
            eof_grace = min(0.25, self.shutdown_grace_seconds)
            supervisor.join(timeout=eof_grace)
            if supervisor.is_alive():
                with self._state_lock:
                    process = self._process
                if process is not None:
                    _terminate_process_group(process)
                supervisor.join(
                    timeout=max(0.01, self.shutdown_grace_seconds - eof_grace)
                )
            if supervisor.is_alive():
                with self._state_lock:
                    process = self._process
                if process is not None:
                    _kill_process_group(process)
                supervisor.join(timeout=2)
        self._fail_all_pending(AppServerUnavailableError)
        self._dispatcher.close(join_timeout_seconds=self.shutdown_grace_seconds)

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate_start_paths(self) -> None:
        try:
            if not self.codex_home.is_absolute() or not self.codex_home.is_dir():
                raise AppServerUnavailableError()
        except OSError:
            raise AppServerUnavailableError() from None

    def _require_contract_method(
        self,
        direction: Literal[
            "clientRequests",
            "clientNotifications",
            "serverRequests",
            "serverNotifications",
        ],
        method: str,
    ) -> None:
        contract = self.protocol_contract
        if contract is None:
            return
        try:
            contract.require(direction, method)
            if direction == "clientRequests":
                contract.response_type("client", method)
        except ProtocolContractError:
            raise AppServerProtocolError() from None

    def _supervise(self) -> None:
        restart_attempt = 0
        while not self._closing.is_set():
            ready_at: float | None = None
            generation = 0
            process: subprocess.Popen[bytes] | None = None
            try:
                generation, process = self._spawn_generation()
                self._require_contract_method("clientRequests", "initialize")
                result = self._request_for_generation(
                    generation,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": self.client_name,
                            "title": self.client_title,
                            "version": self.client_version,
                        },
                        "capabilities": {
                            "experimentalApi": False,
                            "requestAttestation": False,
                        },
                    },
                    timeout_seconds=self.initialize_timeout_seconds,
                    require_ready=False,
                )
                server_version = self._validate_initialize_response(result)
                with self._state_lock:
                    if (
                        self._generation != generation
                        or self._process is not process
                        or self._closing.is_set()
                    ):
                        raise AppServerUnavailableError()
                    self._server_version = server_version
                    # Callbacks may arrive as soon as the server reads the
                    # initialized notification. Keep public requests closed until
                    # that synchronized write has completed.
                    self._dispatch_generation = generation
                self._require_contract_method("clientNotifications", "initialized")
                initialized_message = {"method": "initialized", "params": {}}
                validator = self._protocol_validator
                if validator is not None:
                    try:
                        validator.validate_client_notification(initialized_message)
                    except ProtocolContractError:
                        raise AppServerProtocolError() from None
                self._write_message(generation, initialized_message)
                with self._state_lock:
                    if (
                        self._generation != generation
                        or self._process is not process
                        or self._closing.is_set()
                    ):
                        raise AppServerUnavailableError()
                    self._ready.set()
                    self._ever_ready = True
                    ready_at = monotonic()
                    reader = self._reader_thread
                self._activate_mcp_generation(generation)
                self._startup_complete.set()
                if reader is not None:
                    reader.join()
                with self._state_lock:
                    failure = self._generation_failures.pop(
                        generation,
                        AppServerUnavailableError(),
                    )
                if not self._closing.is_set():
                    self._fail_generation_pending(generation, type(failure))
            except CodexAppServerError as error:
                self._restore_mcp_bootstrap(generation)
                if not self._ever_ready:
                    self._startup_error = error
                    self._startup_complete.set()
                    return
            except BaseException:
                self._restore_mcp_bootstrap(generation)
                if not self._ever_ready:
                    self._startup_error = AppServerUnavailableError()
                    self._startup_complete.set()
                    return
            finally:
                self._ready.clear()
                if generation:
                    self._fail_generation_pending(
                        generation,
                        AppServerUnavailableError,
                    )
                    self._discard_generation_server_requests(generation)
                if process is not None:
                    self._stop_process(process)
                self._join_generation_threads()
                with self._state_lock:
                    if self._process is process:
                        self._process = None
                        self._reader_thread = None
                        self._stderr_thread = None
                        self._server_version = None
                    if self._dispatch_generation == generation:
                        self._dispatch_generation = None
                    self._generation_failures.pop(generation, None)
                    self._stderr_bytes.pop(generation, None)
                    self._stderr_chunks.pop(generation, None)
                    self._stderr_reported.discard(generation)

            if self._closing.is_set():
                return
            lifetime = 0.0 if ready_at is None else monotonic() - ready_at
            if ready_at is not None and lifetime >= self.restart_stable_seconds:
                restart_attempt = 0
            restart_attempt += 1
            delay = _capped_restart_delay(
                base_seconds=self.restart_base_delay_seconds,
                maximum_seconds=self.restart_max_delay_seconds,
                attempt=restart_attempt,
            )
            self._closing.wait(delay)

    def _spawn_generation(self) -> tuple[int, subprocess.Popen[bytes]]:
        command = [*codex_command_prefix(self.codex_command)]
        with self._state_lock:
            mcp_masked = self._mcp_startup_state == "masked"
        if mcp_masked:
            # This command-line layer is applied before Codex reads persisted
            # MCP configuration.  It remains in place unless the manager's
            # sanitized bootstrap completes and explicitly activates MCP.
            command.extend(("-c", "mcp_servers={}"))
        command.extend(("app-server", "--stdio"))
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": codex_subprocess_environment(self.codex_home),
            "bufsize": 0,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        try:
            process = subprocess.Popen(command, **kwargs)
        except (OSError, ValueError):
            raise AppServerUnavailableError() from None
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._stop_process(process)
            raise AppServerUnavailableError()
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._process = process
            self._stderr_bytes[generation] = 0
            self._stderr_chunks[generation] = 0
            reader = threading.Thread(
                target=self._read_protocol,
                args=(generation, process, process.stdout),
                name=f"CodexAppServerReader-{generation}",
                daemon=True,
            )
            stderr_reader = threading.Thread(
                target=self._drain_stderr,
                args=(generation, process.stderr),
                name=f"CodexAppServerStderr-{generation}",
                daemon=True,
            )
            self._reader_thread = reader
            self._stderr_thread = stderr_reader
        stderr_reader.start()
        reader.start()
        return generation, process

    def _request_for_generation(
        self,
        generation: int,
        method: str,
        params: JsonValue,
        *,
        timeout_seconds: float,
        require_ready: bool,
    ) -> JsonValue:
        with self._state_lock:
            if (
                self._generation != generation
                or self._process is None
                or self._process.poll() is not None
                or (require_ready and not self._ready.is_set())
            ):
                raise AppServerUnavailableError()
            if len(self._pending) >= self.max_pending_requests:
                raise AppServerOverloadedError()
            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _PendingRequest(method=method, future=Future())
            key = (generation, request_id)
            self._pending[key] = pending
        message = {"method": method, "id": request_id, "params": params}
        try:
            _preflight_outbound_json(message, self.max_message_bytes - 1)
            validator = self._protocol_validator
            if validator is not None:
                validator.validate_client_request(message)
            self._write_message(generation, message)
        except ProtocolContractError:
            with self._state_lock:
                self._pending.pop(key, None)
            raise AppServerProtocolError() from None
        except BaseException:
            with self._state_lock:
                self._pending.pop(key, None)
            raise
        try:
            return pending.future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            with self._state_lock:
                removed = self._pending.pop(key, None)
                if removed is not None:
                    self._retire_id(key)
            if removed is None and pending.future.done():
                return pending.future.result()
            raise AppServerTimeoutError(method) from None

    def _write_message(self, generation: int, message: Mapping[str, JsonValue]) -> None:
        # JSONL framing consumes one byte. Reserve it before serialization so a
        # body at the nominal limit cannot force an over-limit allocation.
        _preflight_outbound_json(message, self.max_message_bytes - 1)
        try:
            encoded = (
                json.dumps(
                    message,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise AppServerProtocolError() from None
        if len(encoded) > self.max_message_bytes:
            raise AppServerProtocolError()

        with self._write_lock:
            with self._state_lock:
                process = self._process
                if (
                    generation != self._generation
                    or process is None
                    or process.poll() is not None
                    or process.stdin is None
                ):
                    raise AppServerUnavailableError()
                stream = process.stdin
            try:
                written = stream.write(encoded)
                stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._mark_generation_failed(
                    generation,
                    AppServerUnavailableError(),
                )
                raise AppServerUnavailableError() from None
            if written != len(encoded):
                self._mark_generation_failed(
                    generation,
                    AppServerProtocolError(),
                )
                raise AppServerProtocolError()

    def _read_protocol(
        self,
        generation: int,
        process: subprocess.Popen[bytes],
        stream: BinaryIO,
    ) -> None:
        try:
            while not self._closing.is_set():
                line = stream.readline(self.max_message_bytes + 1)
                if not line:
                    self._mark_generation_failed(
                        generation,
                        AppServerUnavailableError(),
                    )
                    return
                if len(line) > self.max_message_bytes or not line.endswith(b"\n"):
                    raise AppServerProtocolError()
                message = _decode_message(line)
                self._route_message(generation, message)
        except CodexAppServerError as error:
            self._mark_generation_failed(generation, error)
        except (OSError, ValueError, RecursionError, UnicodeError):
            self._mark_generation_failed(generation, AppServerProtocolError())
        finally:
            if process.poll() is None and not self._closing.is_set():
                _close_stdin(process)

    def _route_message(self, generation: int, message: dict[str, JsonValue]) -> None:
        has_method = "method" in message
        has_id = "id" in message
        has_result = "result" in message
        has_error = "error" in message
        if has_method:
            if has_result or has_error:
                raise AppServerProtocolError()
            method = _validate_method(message["method"])
            params = message.get("params")
            if has_id:
                self._require_contract_method("serverRequests", method)
                validator = self._protocol_validator
                if validator is not None:
                    try:
                        validator.validate_server_request(message)
                    except ProtocolContractError:
                        raise AppServerProtocolError() from None
                request_id = _validate_request_id(message["id"])
                self._route_server_request(
                    AppServerRequest(
                        request_id=request_id,
                        method=method,
                        params=params,
                        generation=generation,
                    )
                )
            else:
                self._require_contract_method("serverNotifications", method)
                validator = self._protocol_validator
                if validator is not None:
                    try:
                        validator.validate_server_notification(message)
                    except ProtocolContractError:
                        raise AppServerProtocolError() from None
                self._route_notification(
                    AppServerNotification(
                        method=method,
                        params=params,
                        generation=generation,
                    )
                )
            return
        if not has_id or has_result == has_error:
            raise AppServerProtocolError()
        request_id = _validate_request_id(message["id"])
        self._route_response(
            generation,
            request_id,
            result=message.get("result", _MISSING),
            error=message.get("error", _MISSING),
        )

    def _route_response(
        self,
        generation: int,
        request_id: RequestId,
        *,
        result: JsonValue,
        error: JsonValue,
    ) -> None:
        key = (generation, request_id)
        with self._state_lock:
            pending = self._pending.pop(key, None)
            if pending is None:
                if key in self._retired_id_set:
                    self._retired_id_set.remove(key)
                    return
                raise AppServerProtocolError()
        if error is not _MISSING:
            if not isinstance(error, dict):
                pending.future.set_exception(AppServerProtocolError())
                raise AppServerProtocolError()
            code = error.get("code")
            message = error.get("message")
            if (
                isinstance(code, bool)
                or not isinstance(code, int)
                or not isinstance(message, str)
            ):
                pending.future.set_exception(AppServerProtocolError())
                raise AppServerProtocolError()
            validator = self._protocol_validator
            if validator is not None:
                try:
                    validator.validate_client_response(
                        pending.method,
                        error_message={"id": request_id, "error": error},
                        is_error=True,
                    )
                except ProtocolContractError:
                    pending.future.set_exception(AppServerProtocolError())
                    raise AppServerProtocolError() from None
            pending.future.set_exception(
                AppServerRemoteError(method=pending.method, code=code)
            )
            return
        validator = self._protocol_validator
        if validator is not None:
            try:
                validator.validate_client_response(pending.method, result=result)
            except ProtocolContractError:
                pending.future.set_exception(AppServerProtocolError())
                raise AppServerProtocolError() from None
        pending.future.set_result(result)

    def _route_notification(self, notification: AppServerNotification) -> None:
        with self._state_lock:
            handler = self._notification_handlers.get(notification.method)
        if handler is None:
            return

        def invoke() -> None:
            with self._state_lock:
                if (
                    notification.generation != self._generation
                    or self._dispatch_generation != notification.generation
                ):
                    return
            try:
                handler(notification)
            except BaseException:
                # A handler may own durable auth/runtime publication. Silently
                # dropping its failure would leave the in-memory projection
                # ahead of replayable state, so retire this generation and let
                # the normal restart/reconciliation path restore consistency.
                self._mark_generation_failed(
                    notification.generation,
                    AppServerProtocolError(),
                )
                return

        if not self._dispatcher.submit(invoke):
            self._mark_generation_failed(
                notification.generation,
                AppServerOverloadedError(),
            )
            raise AppServerOverloadedError()

    def _route_server_request(self, request: AppServerRequest) -> None:
        key = (request.generation, request.request_id)
        with self._state_lock:
            if key in self._server_requests:
                raise AppServerProtocolError()
            if len(self._server_requests) >= self.max_pending_requests:
                handler = None
                overloaded = True
            else:
                self._server_requests.add(key)
                handler = self._request_handlers.get(request.method)
                overloaded = False
        if overloaded:
            self._write_direct_error(
                request,
                code=_REMOTE_OVERLOAD_CODE,
                message="Client overloaded; retry later.",
            )
            return
        if handler is None:
            self.respond(
                request,
                error=AppServerResponseError(
                    code=_METHOD_NOT_FOUND_CODE,
                    message="Method not found.",
                ),
            )
            return

        def invoke() -> None:
            with self._state_lock:
                if (
                    key not in self._server_requests
                    or self._dispatch_generation != request.generation
                ):
                    return
            try:
                result = handler(request)
                if result is DEFERRED_RESPONSE:
                    return
                self.respond(request, result=result)
            except AppServerStaleGenerationError:
                return
            except BaseException:
                try:
                    self.respond(
                        request,
                        error=AppServerResponseError(
                            code=_INTERNAL_ERROR_CODE,
                            message="Internal client error.",
                        ),
                    )
                except AppServerStaleGenerationError:
                    return

        if not self._dispatcher.submit(invoke):
            self.respond(
                request,
                error=AppServerResponseError(
                    code=_REMOTE_OVERLOAD_CODE,
                    message="Client overloaded; retry later.",
                ),
            )

    def _write_direct_error(
        self,
        request: AppServerRequest,
        *,
        code: int,
        message: str,
    ) -> None:
        response = {
            "id": request.request_id,
            "error": {"code": code, "message": message},
        }
        validator = self._protocol_validator
        if validator is not None:
            try:
                validator.validate_server_response(
                    request.method,
                    error_message=response,
                    is_error=True,
                )
            except ProtocolContractError:
                raise AppServerProtocolError() from None
        self._write_message(request.generation, response)

    def _mark_generation_failed(
        self,
        generation: int,
        error: CodexAppServerError,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return
            self._generation_failures.setdefault(generation, error)
            self._ready.clear()
            if self._dispatch_generation == generation:
                self._dispatch_generation = None
            process = self._process
        self._fail_generation_pending(generation, type(error))
        if process is not None and process.poll() is None:
            _close_stdin(process)

    def _fail_generation_pending(
        self,
        generation: int,
        error_type: type[CodexAppServerError],
    ) -> None:
        futures: list[Future[JsonValue]] = []
        with self._state_lock:
            keys = [key for key in self._pending if key[0] == generation]
            for key in keys:
                pending = self._pending.pop(key)
                self._retire_id(key)
                futures.append(pending.future)
        for future in futures:
            if not future.done():
                try:
                    error = error_type()
                except TypeError:
                    error = AppServerUnavailableError()
                future.set_exception(error)

    def _fail_all_pending(
        self,
        error_factory: Callable[[], CodexAppServerError],
    ) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(error_factory())

    def _retire_id(self, key: tuple[int, RequestId]) -> None:
        self._retired_ids.append(key)
        self._retired_id_set.add(key)
        while len(self._retired_ids) > _RETIRED_ID_LIMIT:
            retired = self._retired_ids.popleft()
            self._retired_id_set.discard(retired)

    def _discard_generation_server_requests(self, generation: int) -> None:
        with self._state_lock:
            self._server_requests = {
                key for key in self._server_requests if key[0] != generation
            }

    def _validate_initialize_response(self, result: JsonValue) -> str:
        if not isinstance(result, dict):
            raise AppServerProtocolError()
        codex_home = result.get("codexHome")
        platform_family = result.get("platformFamily")
        platform_os = result.get("platformOs")
        user_agent = result.get("userAgent")
        if not all(
            isinstance(value, str) and value
            for value in (codex_home, platform_family, platform_os, user_agent)
        ):
            raise AppServerProtocolError()
        assert isinstance(user_agent, str)
        version_match = _APP_SERVER_VERSION_PATTERN.match(user_agent)
        if version_match is None:
            raise AppServerProtocolError()
        server_version = version_match.group(1)
        contract = self.protocol_contract
        if contract is not None:
            expected_version = contract.codex_version.removeprefix("codex-cli ")
            if server_version != expected_version:
                raise AppServerProtocolError()
        assert isinstance(codex_home, str)
        try:
            reported_home = Path(codex_home)
            if not reported_home.is_absolute() or reported_home.resolve(
                strict=False
            ) != self.codex_home.resolve(strict=False):
                raise AppServerProtocolError()
        except OSError:
            raise AppServerProtocolError() from None
        return server_version

    def _drain_stderr(self, generation: int, stream: BinaryIO) -> None:
        while True:
            try:
                content = stream.read(_STDERR_READ_BYTES)
            except (OSError, ValueError):
                return
            if not content:
                return
            with self._state_lock:
                self._stderr_bytes[generation] = self._stderr_bytes.get(
                    generation, 0
                ) + len(content)
                self._stderr_chunks[generation] = (
                    self._stderr_chunks.get(generation, 0) + 1
                )
                diagnostic = (
                    "[redacted] Codex app-server stderr output observed "
                    f"({self._stderr_bytes[generation]} bytes across "
                    f"{self._stderr_chunks[generation]} chunks; generation "
                    f"{generation})."
                )
                should_report = generation not in self._stderr_reported
                self._stderr_reported.add(generation)
            sink = self._stderr_diagnostic_sink
            if sink is not None and should_report:
                self._dispatcher.submit(lambda: _call_diagnostic_sink(sink, diagnostic))

    def _stop_process(self, process: subprocess.Popen[bytes]) -> None:
        _close_stdin(process)
        eof_grace = min(0.25, self.shutdown_grace_seconds)
        if process.poll() is None:
            try:
                process.wait(timeout=eof_grace)
            except subprocess.TimeoutExpired:
                pass
        _terminate_process_group(process)
        remaining_grace = max(0.01, self.shutdown_grace_seconds - eof_grace)
        if process.poll() is None:
            try:
                process.wait(timeout=remaining_grace)
            except subprocess.TimeoutExpired:
                pass
        elif os.name == "posix":
            threading.Event().wait(min(remaining_grace, 0.05))
        _kill_process_group(process)
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        _close_process_streams(process)

    def _join_generation_threads(self) -> None:
        with self._state_lock:
            threads = (self._reader_thread, self._stderr_thread)
        current = threading.current_thread()
        deadline = monotonic() + self.shutdown_grace_seconds
        for thread in threads:
            if thread is None or thread is current:
                continue
            thread.join(timeout=max(0, deadline - monotonic()))


def _decode_message(line: bytes) -> dict[str, JsonValue]:
    try:
        decoded = line.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        RecursionError,
        ValueError,
    ):
        raise AppServerProtocolError() from None
    if not isinstance(value, dict):
        raise AppServerProtocolError()
    return value


def _preflight_outbound_json(value: object, limit_bytes: int) -> None:
    budget = 0
    nodes = 0
    node_limit = max(128, min(100_000, limit_bytes // 2 + 16))
    active_containers: set[int] = set()
    stack: list[tuple[bool, object, int]] = [(False, value, 0)]
    while stack:
        leaving, current, depth = stack.pop()
        if leaving:
            active_containers.discard(id(current))
            continue
        nodes += 1
        if nodes > node_limit or depth > 128:
            raise AppServerProtocolError()
        if current is None:
            budget += 4
        elif isinstance(current, bool):
            budget += 4 if current else 5
        elif isinstance(current, str):
            budget += _json_string_bytes(current, limit_bytes)
        elif isinstance(current, int):
            budget += max(1, current.bit_length()) + 2
        elif isinstance(current, float):
            if not isfinite(current):
                raise AppServerProtocolError()
            budget += 32
        elif isinstance(current, dict):
            container_id = id(current)
            if container_id in active_containers:
                raise AppServerProtocolError()
            active_containers.add(container_id)
            budget += 2 + max(0, len(current) - 1) + len(current)
            if budget > limit_bytes or len(current) > node_limit - nodes:
                raise AppServerProtocolError()
            stack.append((True, current, depth))
            for key in reversed(current):
                if not isinstance(key, str):
                    raise AppServerProtocolError()
                budget += _json_string_bytes(key, limit_bytes)
                if budget > limit_bytes:
                    raise AppServerProtocolError()
                stack.append((False, current[key], depth + 1))
        elif isinstance(current, (list, tuple)):
            container_id = id(current)
            if container_id in active_containers:
                raise AppServerProtocolError()
            active_containers.add(container_id)
            budget += 2 + max(0, len(current) - 1)
            if budget > limit_bytes or len(current) > node_limit - nodes:
                raise AppServerProtocolError()
            stack.append((True, current, depth))
            for item in reversed(current):
                stack.append((False, item, depth + 1))
        else:
            raise AppServerProtocolError()
        if budget > limit_bytes:
            raise AppServerProtocolError()


def _json_string_bytes(value: str, limit_bytes: int) -> int:
    if len(value) > limit_bytes:
        raise AppServerProtocolError()
    size = 2
    for character in value:
        codepoint = ord(character)
        if codepoint in {0x22, 0x5C}:
            size += 2
        elif codepoint < 0x20:
            size += 6
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif 0xD800 <= codepoint <= 0xDFFF:
            raise AppServerProtocolError()
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > limit_bytes:
            raise AppServerProtocolError()
    return size


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey()
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> JsonValue:
    raise ValueError("non-standard JSON constant")


def _validate_request_id(value: object) -> RequestId:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AppServerProtocolError()
    if isinstance(value, str):
        if not value or len(value.encode("utf-8")) > _MAX_REQUEST_ID_TEXT_BYTES:
            raise AppServerProtocolError()
    elif not -(1 << 63) <= value < (1 << 63):
        raise AppServerProtocolError()
    return value


def _validate_expected_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("expected generation must be a positive integer")
    return value


def _validate_method(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_METHOD_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppServerProtocolError()
    return value


def _validate_client_info(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must be bounded printable text")
    return value


def _validate_safe_response_message(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("response error message must be bounded printable text")


def _positive_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value < float("inf")
    ):
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)


def _capped_restart_delay(
    *,
    base_seconds: float,
    maximum_seconds: float,
    attempt: int,
) -> float:
    if attempt <= 1 or base_seconds >= maximum_seconds:
        return min(base_seconds, maximum_seconds)
    delay = base_seconds
    doublings = attempt - 1
    for _step in range(min(doublings, 4096)):
        if delay >= maximum_seconds / 2:
            return maximum_seconds
        delay *= 2
    if doublings > 4096:
        return maximum_seconds
    return min(delay, maximum_seconds)


def _close_stdin(process: subprocess.Popen[bytes]) -> None:
    stream = process.stdin
    if stream is None:
        return
    try:
        stream.close()
    except (OSError, ValueError):
        pass


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _call_diagnostic_sink(sink: Callable[[str], None], diagnostic: str) -> None:
    try:
        sink(diagnostic)
    except BaseException:
        pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        _send_posix_group_signal(process.pid, signal.SIGTERM)
    elif process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        _send_posix_group_signal(process.pid, kill_signal)
    elif process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _force_stop_aborted_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
) -> None:
    grace_seconds = max(0.01, grace_seconds)
    if os.name == "posix":
        deadline = monotonic() + grace_seconds
        if process.poll() is None:
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass
        remaining = deadline - monotonic()
        if remaining > 0:
            threading.Event().wait(remaining)
        # The process-group leader can exit while a descendant that inherited
        # its streams ignores SIGTERM. Always target the original group after
        # the grace period instead of using leader liveness as a proxy.
        _kill_process_group(process)
        return
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)


def _send_posix_group_signal(process_group_id: int, sig: signal.Signals) -> None:
    kill_process_group = getattr(os, "killpg", None)
    if not callable(kill_process_group):
        return
    try:
        kill_process_group(process_group_id, sig)
    except OSError:
        pass
