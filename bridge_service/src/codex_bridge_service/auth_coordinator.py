from __future__ import annotations

from collections import deque
from collections.abc import Callable
from threading import Lock
from typing import Any, Protocol

from .codex_app_server import AppServerNotification
from .models import CodexAuthStatusRecord
from .runtime_gate import (
    RuntimeGate,
    RuntimeGateClosedError,
    RuntimeLease,
    RuntimeMutationConflictError,
)
from .auth_state import (
    MESSAGE_CHECKING,
    MESSAGE_CLOSED,
    MESSAGE_LOGIN_CANCELING,
    MESSAGE_LOGIN_COMPLETING,
    MESSAGE_LOGIN_FAILED,
    MESSAGE_LOGIN_RUNNING,
    MESSAGE_LOGIN_STARTING,
    MESSAGE_LOGOUT_FAILED,
    MESSAGE_LOGOUT_RUNNING,
    MESSAGE_UNAVAILABLE,
    MESSAGE_UNKNOWN,
    account_status,
    cleared_device_fields,
    now,
    parse_device_login,
    updated_account_status,
)

_ACCOUNT_READ_PARAMS = {"refreshToken": False}
_DEVICE_LOGIN_PARAMS = {"type": "chatgptDeviceCode"}
_SHUTDOWN_CANCEL_TIMEOUT_SECONDS = 2.0


class AuthOperationConflictError(RuntimeError):
    """Raised when another authentication mutation already owns the coordinator."""

    def __init__(self) -> None:
        super().__init__("another authentication operation is already in progress")


class AuthCoordinatorClosedError(RuntimeError):
    """Raised when a mutation is attempted after coordinator shutdown."""

    def __init__(self) -> None:
        super().__init__("the authentication coordinator is closed")


class _AuthAppServerClient(Protocol):
    @property
    def generation(self) -> int: ...

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any: ...

    def register_notification_handler(
        self,
        method: str,
        handler: Callable[[AppServerNotification], None],
    ) -> None: ...


class CodexAuthCoordinator:
    """Own the safe public projection of Codex app-server authentication state."""

    def __init__(
        self,
        client: _AuthAppServerClient,
        *,
        state_listener: Callable[[CodexAuthStatusRecord], None] | None = None,
        runtime_gate: RuntimeGate | None = None,
    ) -> None:
        self._client = client
        self._state_listener = state_listener
        self._runtime_gate = runtime_gate
        self._lock = Lock()
        self._status = CodexAuthStatusRecord(
            message=MESSAGE_UNKNOWN,
            updated_at=now(),
        )
        self._revision = 0
        self._operation_sequence = 0
        self._operation: tuple[int, str] | None = None
        self._active_login_id: str | None = None
        self._active_login_generation: int | None = None
        self._cancel_requested = False
        self._started = False
        self._handlers_registered = False
        self._observed_generation: int | None = None
        self._closed = False
        self._runtime_auth_lease: RuntimeLease | None = None
        self._notification_queue: deque[CodexAuthStatusRecord] = deque()
        self._notifying = False

    def start(self) -> CodexAuthStatusRecord:
        """Register auth notifications and reconcile the persisted Codex account."""

        with self._lock:
            self._require_open_locked()
            if (
                self._started
                and self._status.state != "unavailable"
                and self._observed_generation == self._client.generation
            ):
                return self._copy_status_locked()
            if self._operation is not None:
                raise AuthOperationConflictError()
            register_handlers = not self._handlers_registered
            operation = self._begin_operation_locked("start")

        try:
            if register_handlers:
                self._client.register_notification_handler(
                    "account/login/completed",
                    self._handle_login_completed,
                )
                self._client.register_notification_handler(
                    "account/updated",
                    self._handle_account_updated,
                )
                with self._lock:
                    self._handlers_registered = True
            generation, response = self._read_account()
        except Exception:
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        with self._lock:
            self._started = True
        return self._finish_account_read(operation, generation, response)

    def status(
        self,
        *,
        last_error: str | None = None,
    ) -> CodexAuthStatusRecord:
        """Return a detached public snapshot; legacy error text is never projected."""

        del last_error
        with self._lock:
            if self._closed:
                return self._copy_status_locked()
            if not self._started:
                if self._operation is not None:
                    return self._copy_status_locked()
                retry_start = True
                operation = None
                checking = None
            else:
                retry_start = False
                generation = self._client.generation
                needs_reconcile = self._operation is None and (
                    self._status.state == "unavailable"
                    or self._observed_generation != generation
                    or (
                        self._active_login_generation is not None
                        and self._active_login_generation != generation
                    )
                )
                if not needs_reconcile:
                    return self._copy_status_locked()
                operation = self._begin_operation_locked("status_reconcile")
                self._clear_active_login_locked()
                checking = self._set_status_locked(
                    state="checking",
                    busy=True,
                    message=MESSAGE_CHECKING,
                    **cleared_device_fields(),
                )
        if retry_start:
            return self.start()
        assert operation is not None and checking is not None
        self._notify(checking)
        try:
            generation, response = self._read_account()
        except Exception:
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        return self._finish_account_read(operation, generation, response)

    def start_device_login(
        self,
        *,
        force_logout: bool = True,
    ) -> CodexAuthStatusRecord:
        """Start ChatGPT device authorization without implicitly removing credentials."""

        del force_logout
        with self._lock:
            self._require_open_locked()
            self._require_idle_mutation_locked()
            if self._active_login_id is not None:
                raise AuthOperationConflictError()
            if self._status.state in {"ok", "unsupported"}:
                raise AuthOperationConflictError()
            self._acquire_runtime_auth_locked()
            operation = self._begin_operation_locked("login_start")
            self._cancel_requested = False
            request_generation = self._client.generation
            starting = self._set_status_locked(
                state="login_starting",
                busy=True,
                auth_required=True,
                auth_mode=None,
                plan_type=None,
                message=MESSAGE_LOGIN_STARTING,
                **cleared_device_fields(),
            )
        self._notify(starting)

        try:
            response = self._client.request("account/login/start", _DEVICE_LOGIN_PARAMS)
            login_id, user_code, verification_uri = parse_device_login(response)
        except Exception:
            return self._finish_with_failure(
                operation,
                state="login_failed",
                message=MESSAGE_LOGIN_FAILED,
            )

        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            generation_is_current = request_generation == self._client.generation
            should_cancel = self._cancel_requested or self._closed
            if not generation_is_current:
                self._operation = None
                self._cancel_requested = False
                runtime_lease = self._take_runtime_auth_lease_locked()
                failed = self._set_status_locked(
                    state="login_failed",
                    busy=False,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_LOGIN_FAILED,
                    **cleared_device_fields(),
                )
                next_action = "failed"
            elif should_cancel:
                self._active_login_id = login_id
                self._active_login_generation = request_generation
                self._operation = (operation[0], "cancel")
                next_action = "cancel"
                failed = None
                runtime_lease = None
            else:
                self._active_login_id = login_id
                self._active_login_generation = request_generation
                self._operation = None
                running = self._set_status_locked(
                    state="login_running",
                    busy=True,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_LOGIN_RUNNING,
                    verification_uri=verification_uri,
                    login_url=verification_uri,
                    user_code=user_code,
                    output_tail=[],
                )
                next_action = "running"
                failed = None
                runtime_lease = None

        if next_action == "failed":
            assert failed is not None
            if runtime_lease is not None:
                runtime_lease.release()
            self._notify(failed)
            return failed.model_copy(deep=True)
        if next_action == "running":
            self._notify(running)
            return running.model_copy(deep=True)
        return self._cancel_known_login(operation, login_id, request_generation)

    def cancel_login(self) -> CodexAuthStatusRecord:
        """Cancel the active login, including one whose start reply is in flight."""

        with self._lock:
            self._require_open_locked()
            if self._operation is not None:
                if self._operation[1] != "login_start":
                    raise AuthOperationConflictError()
                self._cancel_requested = True
                canceling = self._set_status_locked(
                    state="login_canceling",
                    busy=True,
                    auth_required=True,
                    message=MESSAGE_LOGIN_CANCELING,
                    **cleared_device_fields(),
                )
                deferred = True
                operation = self._operation
                login_id = None
                generation = None
            else:
                if (
                    self._active_login_id is None
                    or self._active_login_generation is None
                ):
                    return self._copy_status_locked()
                operation = self._begin_operation_locked("cancel")
                login_id = self._active_login_id
                generation = self._active_login_generation
                canceling = self._set_status_locked(
                    state="login_canceling",
                    busy=True,
                    auth_required=True,
                    message=MESSAGE_LOGIN_CANCELING,
                    **cleared_device_fields(),
                )
                deferred = False
        self._notify(canceling)
        if deferred:
            return canceling.model_copy(deep=True)
        assert login_id is not None and generation is not None
        return self._cancel_known_login(operation, login_id, generation)

    def logout(self) -> CodexAuthStatusRecord:
        """Explicitly remove Codex credentials and verify the resulting account state."""

        with self._lock:
            self._require_open_locked()
            self._require_idle_mutation_locked()
            if self._active_login_id is not None:
                raise AuthOperationConflictError()
            self._acquire_runtime_auth_locked()
            operation = self._begin_operation_locked("logout")
            logging_out = self._set_status_locked(
                state="logout_running",
                busy=True,
                message=MESSAGE_LOGOUT_RUNNING,
                **cleared_device_fields(),
            )
        self._notify(logging_out)

        try:
            self._client.request("account/logout", None)
        except Exception:
            pass
        try:
            generation, account = self._read_account()
        except Exception:
            return self._finish_logout_unknown(operation)

        return self._finish_account_read(
            operation,
            generation,
            account,
            message_override=(
                None
                if account_status(account)["state"] == "logged_out"
                else MESSAGE_LOGOUT_FAILED
            ),
        )

    def reconcile_after_restart(self) -> CodexAuthStatusRecord:
        """Discard generation-bound login correlation and reread persisted auth."""

        with self._lock:
            self._require_open_locked()
            self._require_idle_mutation_locked()
            operation = self._begin_operation_locked("reconcile")
            self._clear_active_login_locked()
        try:
            generation, response = self._read_account()
        except Exception:
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        return self._finish_account_read(operation, generation, response)

    def close(self) -> None:
        """Stop publishing auth state and best-effort cancel an issued device code."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            login_id = self._active_login_id
            login_generation = self._active_login_generation
            if self._operation is not None and self._operation[1] == "login_start":
                self._cancel_requested = True
                defer_cleanup = True
            else:
                defer_cleanup = False
                self._operation = None
                self._clear_active_login_locked()

        if not defer_cleanup and login_id is not None and login_generation is not None:
            if login_generation == self._client.generation:
                try:
                    self._client.request(
                        "account/login/cancel",
                        {"loginId": login_id},
                        timeout_seconds=_SHUTDOWN_CANCEL_TIMEOUT_SECONDS,
                    )
                except Exception:
                    pass

        with self._lock:
            if defer_cleanup:
                self._clear_active_login_locked()
            closed = self._set_status_locked(
                state="closed",
                busy=False,
                auth_required=True,
                auth_mode=None,
                plan_type=None,
                message=MESSAGE_CLOSED,
                **cleared_device_fields(),
            )
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(closed)

    def _handle_login_completed(self, notification: AppServerNotification) -> None:
        params = notification.params
        if not isinstance(params, dict):
            return
        login_id = params.get("loginId")
        success = params.get("success")
        if not isinstance(login_id, str) or not isinstance(success, bool):
            return

        with self._lock:
            if (
                self._closed
                or self._operation is not None
                or notification.generation != self._active_login_generation
                or login_id != self._active_login_id
            ):
                return
            operation = self._begin_operation_locked("login_complete")
            self._clear_active_login_locked()
            if not success:
                self._operation = None
                runtime_lease = self._take_runtime_auth_lease_locked()
                failed = self._set_status_locked(
                    state="login_failed",
                    busy=False,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_LOGIN_FAILED,
                    **cleared_device_fields(),
                )
                completing = None
            else:
                completing = self._set_status_locked(
                    state="login_completing",
                    busy=True,
                    auth_required=True,
                    message=MESSAGE_LOGIN_COMPLETING,
                    **cleared_device_fields(),
                )
                failed = None
                runtime_lease = None

        if failed is not None:
            if runtime_lease is not None:
                runtime_lease.release()
            self._notify(failed)
            return
        assert completing is not None
        self._notify(completing)
        try:
            generation, response = self._read_account()
        except Exception:
            self._finish_with_failure(
                operation,
                state="login_failed",
                message=MESSAGE_LOGIN_FAILED,
            )
            return
        self._finish_account_read(operation, generation, response)

    def _handle_account_updated(self, notification: AppServerNotification) -> None:
        params = notification.params
        if not isinstance(params, dict):
            return
        with self._lock:
            if self._closed or notification.generation != self._client.generation:
                return
            if self._operation is not None or self._active_login_id is not None:
                return
            normalized = updated_account_status(params, self._status)
            published = self._set_status_locked(**normalized)
        self._notify(published)

    def _cancel_known_login(
        self,
        operation: tuple[int, str],
        login_id: str,
        generation: int,
    ) -> CodexAuthStatusRecord:
        if generation == self._client.generation:
            try:
                self._client.request(
                    "account/login/cancel",
                    {"loginId": login_id},
                )
            except Exception:
                pass
        try:
            response_generation, response = self._read_account()
        except Exception:
            with self._lock:
                if self._closed:
                    return self._copy_status_locked()
            return self._finish_with_failure(
                operation,
                state="login_failed",
                message=MESSAGE_LOGIN_FAILED,
            )

        with self._lock:
            if self._closed:
                self._operation = None
                self._clear_active_login_locked()
                return self._copy_status_locked()
        return self._finish_account_read(
            operation,
            response_generation,
            response,
        )

    def _read_account(self) -> tuple[int, Any]:
        generation = self._client.generation
        response = self._client.request("account/read", _ACCOUNT_READ_PARAMS)
        if generation != self._client.generation:
            raise RuntimeError("app-server generation changed during account read")
        return generation, response

    def _finish_account_read(
        self,
        operation: tuple[int, str],
        generation: int,
        response: Any,
        *,
        message_override: str | None = None,
    ) -> CodexAuthStatusRecord:
        try:
            normalized = account_status(response)
        except (TypeError, ValueError):
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            if generation != self._client.generation:
                self._operation = None
                self._observed_generation = None
                self._cancel_requested = False
                self._clear_active_login_locked()
                published = self._set_status_locked(
                    state="unavailable",
                    busy=False,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_UNAVAILABLE,
                    **cleared_device_fields(),
                )
                generation_changed = True
            else:
                generation_changed = False
                self._operation = None
                self._observed_generation = generation
                self._cancel_requested = False
                self._clear_active_login_locked()
                if message_override is not None:
                    normalized["message"] = message_override
                published = self._set_status_locked(**normalized)
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        if generation_changed:
            return published.model_copy(deep=True)
        return published.model_copy(deep=True)

    def _finish_with_failure(
        self,
        operation: tuple[int, str],
        *,
        state: str,
        message: str,
    ) -> CodexAuthStatusRecord:
        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            if self._closed:
                self._operation = None
                self._cancel_requested = False
                self._clear_active_login_locked()
                return self._copy_status_locked()
            self._operation = None
            self._observed_generation = None
            self._cancel_requested = False
            self._clear_active_login_locked()
            published = self._set_status_locked(
                state=state,
                busy=False,
                auth_required=True,
                auth_mode=None,
                plan_type=None,
                message=message,
                **cleared_device_fields(),
            )
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        return published.model_copy(deep=True)

    def _finish_logout_unknown(
        self,
        operation: tuple[int, str],
    ) -> CodexAuthStatusRecord:
        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            if self._status.auth_mode == "chatgpt":
                state = "ok"
                auth_required = False
            elif self._status.auth_mode is not None:
                state = "unsupported"
                auth_required = True
            else:
                state = "logout_failed"
                auth_required = True
            self._operation = None
            self._observed_generation = None
            published = self._set_status_locked(
                state=state,
                busy=False,
                auth_required=auth_required,
                message=MESSAGE_LOGOUT_FAILED,
                **cleared_device_fields(),
            )
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        return published.model_copy(deep=True)

    def _begin_operation_locked(self, kind: str) -> tuple[int, str]:
        self._operation_sequence += 1
        operation = (self._operation_sequence, kind)
        self._operation = operation
        return operation

    def _operation_matches_locked(self, operation: tuple[int, str]) -> bool:
        return self._operation is not None and self._operation[0] == operation[0]

    def _require_idle_mutation_locked(self) -> None:
        if self._operation is not None:
            raise AuthOperationConflictError()

    def _require_open_locked(self) -> None:
        if self._closed:
            raise AuthCoordinatorClosedError()

    def _acquire_runtime_auth_locked(self) -> None:
        if self._runtime_gate is None:
            return
        if self._runtime_auth_lease is not None:
            raise AuthOperationConflictError()
        try:
            self._runtime_auth_lease = self._runtime_gate.acquire_auth_mutation()
        except (RuntimeGateClosedError, RuntimeMutationConflictError):
            raise AuthOperationConflictError() from None

    def _take_runtime_auth_lease_locked(self) -> RuntimeLease | None:
        lease = self._runtime_auth_lease
        self._runtime_auth_lease = None
        return lease

    def _clear_active_login_locked(self) -> None:
        self._active_login_id = None
        self._active_login_generation = None

    def _set_status_locked(self, **updates: Any) -> CodexAuthStatusRecord:
        self._revision += 1
        updates["revision"] = self._revision
        updates["updated_at"] = now()
        self._status = self._status.model_copy(update=updates, deep=True)
        published = self._copy_status_locked()
        if self._state_listener is not None:
            self._notification_queue.append(published.model_copy(deep=True))
        return published

    def _copy_status_locked(self) -> CodexAuthStatusRecord:
        return self._status.model_copy(deep=True)

    def _notify(self, status: CodexAuthStatusRecord) -> None:
        del status
        listener = self._state_listener
        if listener is None:
            return
        with self._lock:
            if self._notifying:
                return
            self._notifying = True
        while True:
            with self._lock:
                if not self._notification_queue:
                    self._notifying = False
                    return
                pending = self._notification_queue.popleft()
            try:
                listener(pending)
            except Exception:
                pass
