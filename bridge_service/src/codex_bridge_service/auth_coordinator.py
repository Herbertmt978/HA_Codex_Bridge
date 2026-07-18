from __future__ import annotations

from collections import deque
from collections.abc import Callable
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Any, Protocol

from .account import account_owner_marker, account_unverified_marker
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
        initial_status: CodexAuthStatusRecord | None = None,
        state_listener_fatal: bool = False,
        runtime_gate: RuntimeGate | None = None,
        account_owner_secret: str | None = None,
        account_binding_listener: Callable[[str], None] | None = None,
        account_read_timeout_seconds: float = 5.0,
        active_login_poll_interval_seconds: float = 2.0,
    ) -> None:
        if (
            isinstance(account_read_timeout_seconds, bool)
            or not isinstance(account_read_timeout_seconds, (int, float))
            or not isfinite(account_read_timeout_seconds)
            or account_read_timeout_seconds <= 0
        ):
            raise ValueError("account read timeout must be positive")
        if (
            isinstance(active_login_poll_interval_seconds, bool)
            or not isinstance(active_login_poll_interval_seconds, (int, float))
            or not isfinite(active_login_poll_interval_seconds)
            or active_login_poll_interval_seconds < 0
        ):
            raise ValueError("active login poll interval must be non-negative")
        if (account_owner_secret is None) != (account_binding_listener is None):
            raise ValueError(
                "account owner secret and binding listener must be configured together"
            )
        if account_owner_secret is not None and not account_owner_secret:
            raise ValueError("account owner secret is invalid")
        self._client = client
        self._account_read_timeout_seconds = float(account_read_timeout_seconds)
        self._active_login_poll_interval_seconds = float(
            active_login_poll_interval_seconds
        )
        self._state_listener = state_listener
        self._state_listener_fatal = state_listener_fatal
        self._runtime_gate = runtime_gate
        self._account_owner_secret = account_owner_secret
        self._account_binding_listener = account_binding_listener
        self._lock = Lock()
        self._status = (
            initial_status.model_copy(deep=True)
            if initial_status is not None
            else CodexAuthStatusRecord(
                message=MESSAGE_UNKNOWN,
                updated_at=now(),
            )
        )
        self._revision = self._status.revision
        self._operation_sequence = 0
        self._operation: tuple[int, str] | None = None
        self._account_update_revision = 0
        self._operation_account_update_revision = 0
        self._active_login_id: str | None = None
        self._active_login_generation: int | None = None
        self._active_login_polling = False
        self._active_login_last_polled_at: float | None = None
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
            self._acquire_runtime_auth_locked()
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
        while True:
            login_poll: tuple[str, int, int] | None = None
            blocked: CodexAuthStatusRecord | None = None
            stale_login_lease: RuntimeLease | None = None
            restart_after_stale_login = False
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
                    poll_due = (
                        self._operation is None
                        and not self._active_login_polling
                        and self._active_login_id is not None
                        and self._active_login_generation == generation
                        and (
                            self._active_login_last_polled_at is None
                            or monotonic() - self._active_login_last_polled_at
                            >= self._active_login_poll_interval_seconds
                        )
                    )
                    stale_login_generation = (
                        self._active_login_generation is not None
                        and self._active_login_generation != generation
                    )
                    needs_reconcile = self._operation is None and (
                        self._status.state == "unavailable"
                        or self._observed_generation != generation
                        or stale_login_generation
                    )
                    if not needs_reconcile and not poll_due:
                        return self._copy_status_locked()
                    if poll_due:
                        assert self._active_login_id is not None
                        self._active_login_polling = True
                        self._active_login_last_polled_at = monotonic()
                        login_poll = (
                            self._active_login_id,
                            generation,
                            self._account_update_revision,
                        )
                        operation = None
                        checking = None
                    elif stale_login_generation:
                        # The old App-server generation can no longer complete
                        # this device code. Clear its correlation before
                        # releasing the lease so every late callback is inert,
                        # then restart admission and acquire a fresh lease.
                        self._cancel_requested = False
                        self._clear_active_login_locked()
                        stale_login_lease = self._take_runtime_auth_lease_locked()
                        restart_after_stale_login = True
                        operation = None
                        checking = None
                    else:
                        try:
                            self._acquire_runtime_auth_locked()
                        except AuthOperationConflictError:
                            operation = None
                            checking = None
                            blocked_projection = self._unverified_account_status()
                            if self._status.model_dump(
                                exclude={"revision", "updated_at"}
                            ) == blocked_projection:
                                blocked = self._copy_status_locked()
                            else:
                                blocked = self._set_status_locked(**blocked_projection)
                        else:
                            operation = self._begin_operation_locked(
                                "status_reconcile"
                            )
                            self._clear_active_login_locked()
                            checking = self._set_status_locked(
                                state="checking",
                                busy=True,
                                auth_required=True,
                                message=MESSAGE_CHECKING,
                                **cleared_device_fields(),
                            )
            if restart_after_stale_login:
                if stale_login_lease is not None:
                    stale_login_lease.release()
                continue
            break
        if blocked is not None:
            self._notify(blocked)
            return blocked
        if retry_start:
            return self.start()
        if checking is not None:
            self._notify(checking)
        try:
            generation, response = self._read_account()
        except Exception:
            if login_poll is not None:
                return self._finish_active_login_poll_failure(login_poll)
            assert operation is not None
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        if login_poll is not None:
            return self._finish_active_login_poll(login_poll, generation, response)
        assert operation is not None
        return self._finish_account_read(operation, generation, response)

    def start_device_login(
        self,
        *,
        force_logout: bool = False,
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
                self._active_login_polling = False
                self._active_login_last_polled_at = monotonic()
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
            self._acquire_runtime_auth_locked()
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
            # Notifications are hints rather than an authoritative identity
            # source, but an update observed while account/read is in flight
            # makes that read's snapshot stale. Record the hint before checking
            # operation ownership so the in-flight result cannot publish ready.
            self._account_update_revision += 1
            if self._operation is not None or self._active_login_id is not None:
                return
            try:
                self._acquire_runtime_auth_locked()
            except AuthOperationConflictError:
                # A notification is only a hint. If a turn currently owns the
                # runtime, fail closed and let the next status reconciliation
                # perform the authoritative read after that turn settles.
                self._observed_generation = None
                published = self._set_status_locked(
                    state="unavailable",
                    busy=False,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_UNAVAILABLE,
                    **cleared_device_fields(),
                )
                operation = None
            else:
                operation = self._begin_operation_locked("account_updated")
                published = self._set_status_locked(
                    state="checking",
                    busy=True,
                    auth_required=True,
                    auth_mode=None,
                    plan_type=None,
                    message=MESSAGE_CHECKING,
                    **cleared_device_fields(),
                )
        if published is not None:
            self._notify(published)
        if operation is None:
            return
        try:
            generation, response = self._read_account()
        except Exception:
            self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
            return
        self._finish_account_read(operation, generation, response)

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
        response = self._client.request(
            "account/read",
            _ACCOUNT_READ_PARAMS,
            timeout_seconds=self._account_read_timeout_seconds,
        )
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
        binding_marker, owner_verified = self._binding_observation(
            response,
            normalized,
        )
        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            generation_is_current = generation == self._client.generation
            observation_is_current = (
                self._operation_account_update_revision
                == self._account_update_revision
            )
        if not observation_is_current:
            return self._finish_with_failure(
                operation,
                state="unavailable",
                message=MESSAGE_UNAVAILABLE,
            )
        if generation_is_current and binding_marker is not None:
            try:
                self._bind_account_owner(binding_marker)
            except Exception:
                return self._finish_with_failure(
                    operation,
                    state="unavailable",
                    message=MESSAGE_UNAVAILABLE,
                )
        if not owner_verified:
            normalized = self._unverified_account_status()
        with self._lock:
            if not self._operation_matches_locked(operation):
                return self._copy_status_locked()
            observation_is_current = (
                self._operation_account_update_revision
                == self._account_update_revision
            )
            if generation != self._client.generation or not observation_is_current:
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

    def _finish_active_login_poll(
        self,
        login_poll: tuple[str, int, int],
        generation: int,
        response: Any,
    ) -> CodexAuthStatusRecord:
        try:
            normalized = account_status(response)
        except (TypeError, ValueError):
            return self._finish_active_login_poll_failure(login_poll)
        binding_marker, owner_verified = self._binding_observation(
            response,
            normalized,
        )
        login_id, login_generation, account_update_revision = login_poll
        with self._lock:
            if (
                self._closed
                or not self._active_login_polling
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
            ):
                return self._copy_status_locked()
            if (
                self._operation is not None
                or generation != self._client.generation
                or generation != login_generation
            ):
                self._active_login_polling = False
                return self._copy_status_locked()
            if account_update_revision != self._account_update_revision:
                observation_is_current = False
            else:
                observation_is_current = True
        if not observation_is_current:
            return self._finish_active_login_poll_invalidated(login_poll)
        with self._lock:
            if (
                self._closed
                or not self._active_login_polling
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
            ):
                return self._copy_status_locked()
            self._observed_generation = generation
            if normalized["state"] == "logged_out":
                self._active_login_polling = False
                return self._copy_status_locked()
        if binding_marker is not None:
            try:
                self._bind_account_owner(binding_marker)
            except Exception:
                return self._finish_active_login_binding_failure(login_poll)
        if not owner_verified:
            normalized = self._unverified_account_status()
        with self._lock:
            if (
                self._closed
                or not self._active_login_polling
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
                or self._operation is not None
                or generation != self._client.generation
                or generation != login_generation
            ):
                return self._copy_status_locked()
            if account_update_revision != self._account_update_revision:
                observation_is_current = False
            else:
                observation_is_current = True
        if not observation_is_current:
            return self._finish_active_login_poll_invalidated(login_poll)
        with self._lock:
            if (
                self._closed
                or not self._active_login_polling
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
                or self._operation is not None
                or generation != self._client.generation
                or generation != login_generation
                or account_update_revision != self._account_update_revision
            ):
                return self._copy_status_locked()
            self._cancel_requested = False
            self._clear_active_login_locked()
            published = self._set_status_locked(**normalized)
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        return published.model_copy(deep=True)

    def _finish_active_login_binding_failure(
        self,
        login_poll: tuple[str, int, int],
    ) -> CodexAuthStatusRecord:
        login_id, login_generation, _account_update_revision = login_poll
        with self._lock:
            if (
                self._closed
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
            ):
                return self._copy_status_locked()
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
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        return published.model_copy(deep=True)

    def _finish_active_login_poll_invalidated(
        self,
        login_poll: tuple[str, int, int],
    ) -> CodexAuthStatusRecord:
        login_id, login_generation, _account_update_revision = login_poll
        with self._lock:
            if (
                self._closed
                or not self._active_login_polling
                or self._active_login_id != login_id
                or self._active_login_generation != login_generation
            ):
                return self._copy_status_locked()
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
            runtime_lease = self._take_runtime_auth_lease_locked()
        if runtime_lease is not None:
            runtime_lease.release()
        self._notify(published)
        return published.model_copy(deep=True)

    def _finish_active_login_poll_failure(
        self,
        login_poll: tuple[str, int, int],
    ) -> CodexAuthStatusRecord:
        login_id, login_generation, _account_update_revision = login_poll
        with self._lock:
            if (
                self._active_login_id == login_id
                and self._active_login_generation == login_generation
            ):
                self._active_login_polling = False
            return self._copy_status_locked()

    def _binding_observation(
        self,
        response: Any,
        normalized: dict[str, Any],
    ) -> tuple[str | None, bool]:
        if self._account_owner_secret is None or self._account_binding_listener is None:
            return None, True
        if normalized.get("state") == "ok":
            marker = account_owner_marker(response, self._account_owner_secret)
            if marker is not None:
                return marker, True
            return account_unverified_marker(self._account_owner_secret), False
        return account_unverified_marker(self._account_owner_secret), True

    @staticmethod
    def _unverified_account_status() -> dict[str, Any]:
        return {
            "state": "unavailable",
            "busy": False,
            "auth_required": True,
            "auth_mode": None,
            "plan_type": None,
            "message": MESSAGE_UNAVAILABLE,
            **cleared_device_fields(),
        }

    def _bind_account_owner(self, marker: str) -> None:
        listener = self._account_binding_listener
        if listener is not None:
            listener(marker)

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
        self._operation_account_update_revision = self._account_update_revision
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
        self._active_login_polling = False
        self._active_login_last_polled_at = None

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
                if not self._state_listener_fatal:
                    continue
                with self._lock:
                    self._notification_queue.clear()
                    self._notifying = False
                raise
