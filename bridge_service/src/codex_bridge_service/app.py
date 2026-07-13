import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .account import AppServerAccountProbe, CodexAccountProbe
from .auth_coordinator import CodexAuthCoordinator
from .build_info import BuildInfo
from .codex_app_server import CodexAppServerClient
from .codex_auth import CodexAuthManager
from .diagnostics import BridgeDiagnosticsProbe
from .event_store import (
    DurableOperationTooLargeError,
    EventDraft,
    EventPayloadTooLargeError,
    EventStoreCapacityError,
)
from .http_limits import AttachmentIngressMiddleware
from .limits import AppServerLimitsProbe, CodexLimitsProbe
from .model_catalog import CodexModelCatalogProbe
from .models import CodexAuthStatusRecord, RuntimeProfile
from .resource_limits import (
    QuotaExceededError,
    ResourceLimitError,
    ResourceLimits,
)
from .routes import (
    approvals,
    artifacts,
    attachments,
    codex_auth,
    events,
    health,
    projects,
    prompts,
    runtime_events,
    status,
    threads,
)
from .runner import BridgeRunner
from .runtime_broker import RuntimeBroker, RuntimeBrokerError
from .runtime_gate import (
    RuntimeGate,
    RuntimeGateClosedError,
    RuntimeGateError,
    RuntimeMutationConflictError,
    RuntimeQueueFullError,
)
from .storage import BridgeStorage


class _AppServerLifecycle(Protocol):
    def start(self) -> None: ...

    def close(self) -> None: ...


class _AuthCoordinatorLifecycle(Protocol):
    def start(self) -> object: ...

    def close(self) -> None: ...


_AUTH_STATE_FILENAME = "auth-state.json"
_OUTBOX_MARKER_FIELD = "_bridge_operation"


def _load_durable_auth_status(path: Path) -> CodexAuthStatusRecord | None:
    """Load the safe auth projection without trusting outbox metadata."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("The durable authentication state is invalid.") from None
    if not isinstance(raw, dict):
        raise ValueError("The durable authentication state is invalid.")

    payload = dict(raw)
    marker = payload.pop(_OUTBOX_MARKER_FIELD, None)
    try:
        status = CodexAuthStatusRecord.model_validate(payload)
    except ValidationError:
        raise ValueError("The durable authentication state is invalid.") from None
    if marker is None:
        return status
    if not isinstance(marker, dict) or set(marker) != {"operation_id", "revision"}:
        raise ValueError("The durable authentication state marker is invalid.")
    operation_id = marker.get("operation_id")
    marker_revision = marker.get("revision")
    if (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 256
        or type(marker_revision) is not int
        or marker_revision < 1
        or marker_revision != status.revision
    ):
        raise ValueError("The durable authentication state marker is invalid.")
    return status


def create_app(
    root_path: Path | str,
    auth_token: str,
    limits_probe: CodexLimitsProbe | None = None,
    account_probe: CodexAccountProbe | None = None,
    diagnostics_probe: BridgeDiagnosticsProbe | None = None,
    model_catalog_probe: CodexModelCatalogProbe | None = None,
    codex_command: str = "codex",
    codex_home: Path | str | None = None,
    run_idle_timeout_seconds: float | None = 1800.0,
    auth_manager: CodexAuthManager | None = None,
    runner_factory=None,
    initialize_special_projects: bool = False,
    build_info: BuildInfo | None = None,
    runtime_profile: RuntimeProfile | str = RuntimeProfile.EXTERNAL_LEGACY,
    workspace_root: Path | str | None = None,
    resource_limits: ResourceLimits | None = None,
    app_server_factory: Callable[[], _AppServerLifecycle] | None = None,
    auth_coordinator_factory: (
        Callable[[Any], _AuthCoordinatorLifecycle] | None
    ) = None,
) -> FastAPI:
    resolved_runtime_profile = RuntimeProfile(runtime_profile)
    resolved_build_info = (
        build_info if build_info is not None else BuildInfo.from_environment()
    )
    resolved_resource_limits = (
        resource_limits or ResourceLimits()
        if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        else resource_limits
    )
    if (
        resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        and resolved_resource_limits is not None
        and resolved_resource_limits.max_active_turns != 1
    ):
        raise ValueError("The Home Assistant runtime requires exactly one active turn.")
    resolved_runtime_gate = (
        RuntimeGate(limits=resolved_resource_limits)
        if resolved_resource_limits is not None
        and resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        else None
    )
    resolved_app_server: _AppServerLifecycle | None = None
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        resolved_app_server = (
            app_server_factory()
            if app_server_factory is not None
            else CodexAppServerClient(
                codex_command=codex_command,
                codex_home=codex_home,
                client_version=(
                    resolved_build_info.app_version
                    or resolved_build_info.bridge_version
                    or "0.6.0"
                ),
                # RuntimeBroker state transitions are ordered protocol events.
                # One bounded callback worker preserves app-server FIFO order.
                callback_workers=1,
            )
        )
    resolved_auth_coordinator: _AuthCoordinatorLifecycle | None = None
    resolved_runner: Any = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if resolved_app_server is None:
            try:
                yield
            finally:
                await asyncio.to_thread(storage.event_store.close)
            return
        try:
            await asyncio.to_thread(resolved_app_server.start)
            if resolved_auth_coordinator is not None:
                await asyncio.to_thread(resolved_auth_coordinator.start)
            runner_start = getattr(resolved_runner, "start", None)
            if callable(runner_start):
                await asyncio.to_thread(runner_start)
            yield
        finally:
            try:
                try:
                    runner_close = getattr(resolved_runner, "close", None)
                    if callable(runner_close):
                        await asyncio.to_thread(runner_close)
                finally:
                    try:
                        if resolved_auth_coordinator is not None:
                            await asyncio.to_thread(resolved_auth_coordinator.close)
                    finally:
                        try:
                            if resolved_runtime_gate is not None:
                                await asyncio.to_thread(resolved_runtime_gate.close)
                        finally:
                            await asyncio.to_thread(resolved_app_server.close)
            finally:
                await asyncio.to_thread(storage.event_store.close)

    app = FastAPI(title="Codex Bridge", lifespan=lifespan)

    @app.exception_handler(EventStoreCapacityError)
    async def event_store_capacity_handler(
        _request, _error: EventStoreCapacityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=507,
            content={
                "detail": {
                    "code": "event_store_capacity_exhausted",
                    "resource": "event_store",
                    "retryable": True,
                }
            },
        )

    @app.exception_handler(DurableOperationTooLargeError)
    async def durable_operation_size_handler(
        _request, _error: DurableOperationTooLargeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "durable_operation_too_large",
                    "resource": "durable_operation",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(EventPayloadTooLargeError)
    async def event_payload_size_handler(
        _request, _error: EventPayloadTooLargeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "event_payload_too_large",
                    "resource": "event_payload",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(ResourceLimitError)
    async def resource_limit_handler(
        _request, error: ResourceLimitError
    ) -> JSONResponse:
        status_code = 413 if isinstance(error, QuotaExceededError) else 409
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": {
                    "code": error.code,
                    "resource": error.resource,
                    "retryable": status_code == 409,
                }
            },
        )

    @app.exception_handler(RuntimeBrokerError)
    async def runtime_broker_handler(
        _request, error: RuntimeBrokerError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.public_detail()},
        )

    @app.exception_handler(RuntimeGateError)
    async def runtime_gate_handler(_request, error: RuntimeGateError) -> JSONResponse:
        if isinstance(error, RuntimeQueueFullError):
            status_code = 429
            code = "runtime_queue_full"
        elif isinstance(error, RuntimeMutationConflictError):
            status_code = 409
            code = "runtime_mutation_conflict"
        elif isinstance(error, RuntimeGateClosedError):
            status_code = 503
            code = "runtime_closed"
        else:
            status_code = 409
            code = "runtime_conflict"
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": {
                    "code": code,
                    "retryable": status_code in {429, 503},
                }
            },
        )

    resolved_model_catalog_probe = model_catalog_probe or CodexModelCatalogProbe(
        codex_command=codex_command,
        codex_home=codex_home,
    )

    def special_project_defaults() -> tuple[str, str, bool]:
        catalog = resolved_model_catalog_probe.probe()
        return catalog.default_model, catalog.default_thinking_level, catalog.stale

    resolved_limits_probe = (
        AppServerLimitsProbe(cast(Any, resolved_app_server))
        if resolved_app_server is not None
        else limits_probe
    )
    resolved_account_probe = (
        AppServerAccountProbe(cast(Any, resolved_app_server))
        if resolved_app_server is not None
        else account_probe
    )
    storage = BridgeStorage(
        root_path=root_path,
        limits_probe=resolved_limits_probe,
        special_project_defaults_provider=special_project_defaults,
        runtime_profile=resolved_runtime_profile,
        workspace_root=workspace_root,
        resource_limits=resolved_resource_limits,
    )
    if resolved_app_server is not None:
        if auth_coordinator_factory is not None:
            resolved_auth_coordinator = auth_coordinator_factory(resolved_app_server)
        else:
            initial_auth_status = _load_durable_auth_status(
                storage.root / _AUTH_STATE_FILENAME
            )

            def persist_auth_status(status: CodexAuthStatusRecord) -> None:
                payload = status.model_dump(mode="json")
                storage.durable_outbox.commit_json(
                    operation_id=f"auth-status:{status.revision}",
                    relative_path=_AUTH_STATE_FILENAME,
                    state_revision=status.revision,
                    state_payload=payload,
                    event=EventDraft(
                        scope="auth",
                        event_type="auth.status_changed",
                        payload=payload,
                    ),
                )

            resolved_auth_coordinator = CodexAuthCoordinator(
                cast(Any, resolved_app_server),
                state_listener=persist_auth_status,
                initial_status=initial_auth_status,
                state_listener_fatal=True,
                runtime_gate=resolved_runtime_gate,
            )
    if storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        limits = storage.resource_limits
        assert limits is not None
        app.add_middleware(
            AttachmentIngressMiddleware,
            expected_token=auth_token,
            max_body_bytes=(
                limits.max_upload_file_bytes + limits.max_upload_request_overhead_bytes
            ),
        )
    if initialize_special_projects:
        if storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            storage.defer_special_project_migration()
        else:
            initial_catalog = resolved_model_catalog_probe.probe()
            if initial_catalog.stale:
                storage.defer_special_project_migration()
            else:
                storage.initialize_special_projects(
                    default_model=initial_catalog.default_model,
                    default_thinking_level=initial_catalog.default_thinking_level,
                    defaults_provisional=False,
                )
    app.state.storage = storage
    app.state.event_store = storage.event_store
    app.state.auth_token = auth_token
    app.state.build_info = resolved_build_info
    app.state.codex_app_server = resolved_app_server
    app.state.runtime_gate = resolved_runtime_gate
    app.state.auth_coordinator = resolved_auth_coordinator
    app.state.account_probe = resolved_account_probe
    app.state.diagnostics_probe = diagnostics_probe or BridgeDiagnosticsProbe(
        storage=storage,
        build_info=resolved_build_info,
        codex_command=codex_command,
        codex_home=codex_home,
    )
    app.state.model_catalog_probe = resolved_model_catalog_probe
    app.state.auth_manager = (
        None
        if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        else auth_manager
        or CodexAuthManager(
            codex_command=codex_command,
            codex_home=codex_home,
        )
    )
    resolved_runner = (
        runner_factory(storage)
        if runner_factory is not None
        else RuntimeBroker(
            storage,
            cast(Any, resolved_app_server),
            cast(RuntimeGate, resolved_runtime_gate),
            resource_limits=resolved_resource_limits,
        )
        if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        else BridgeRunner(
            storage,
            codex_command=codex_command,
            codex_home=codex_home,
            idle_timeout_seconds=run_idle_timeout_seconds,
        )
    )
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT and isinstance(
        resolved_runner, BridgeRunner
    ):
        raise ValueError(
            "BridgeRunner is an external-legacy adapter and cannot own the "
            "Home Assistant runtime."
        )
    app.state.runner = resolved_runner
    app.include_router(artifacts.router)
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        app.include_router(approvals.router)
    app.include_router(attachments.router)
    app.include_router(codex_auth.router)
    app.include_router(events.router)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(prompts.router)
    app.include_router(runtime_events.router)
    app.include_router(status.router)
    app.include_router(threads.router)
    return app
