import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .account import AppServerAccountProbe, CodexAccountProbe
from .auth_coordinator import CodexAuthCoordinator
from .automations import AutomationError, AutomationStore, AutomationValidationError
from .build_info import BuildInfo
from .capabilities import CapabilitiesManager
from .codex_app_server import CodexAppServerClient, CodexAppServerError
from .codex_auth import CodexAuthManager
from .diagnostics import BridgeDiagnosticsProbe
from .event_store import (
    DurableOperationTooLargeError,
    EventDraft,
    EventPayloadTooLargeError,
    EventStoreCapacityError,
)
from .feature_capabilities import supports_web_search
from .http_limits import AttachmentIngressMiddleware
from .limits import AppServerLimitsProbe, CodexLimitsProbe
from .model_catalog import AppServerModelCatalogProbe, CodexModelCatalogProbe
from .mcp_manager import McpManager, McpManagerError
from .models import CodexAuthStatusRecord, RunMode, RuntimeProfile
from .resource_limits import (
    QuotaExceededError,
    ResourceLimitError,
    ResourceLimits,
)
from .routes import (
    approvals,
    agents,
    artifacts,
    attachments,
    automations,
    capabilities,
    uploads,
    codex_auth,
    events,
    health,
    mcp,
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
from .routes.agents import WorkspaceAgentsManager
from .storage import BridgeStorage, ProjectMutationError


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
    sandbox_ready: bool | None = None,
    model_discovery_timeout_seconds: float = 5.0,
    model_cache_ttl_seconds: float = 600.0,
    enable_mcp: bool = False,
) -> FastAPI:
    if type(enable_mcp) is not bool:
        raise ValueError("MCP enabled state must be a boolean")
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
                    or "0.7.1"
                ),
                # RuntimeBroker state transitions are ordered protocol events.
                # One bounded callback worker preserves app-server FIFO order.
                callback_workers=1,
                enable_mcp=enable_mcp,
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
            try:
                await asyncio.to_thread(resolved_app_server.start)
            except CodexAppServerError:
                # Keep the authenticated diagnostic surface alive for a
                # missing, incompatible, or otherwise unavailable bundled
                # runtime. The App remains fail-closed and requires a restart
                # after the underlying installation is repaired.
                _app.state.runtime_startup_failed = True
                yield
                return
            if resolved_mcp_manager is not None:
                try:
                    await asyncio.to_thread(
                        resolved_mcp_manager.sanitize_startup_servers
                    )
                    if resolved_mcp_manager.enabled:
                        await asyncio.to_thread(
                            resolved_mcp_manager.activate_validated_mcp_config
                        )
                except McpManagerError:
                    # The generation-scoped CLI override remains in place
                    # until the manager has durably sanitized native MCP
                    # configuration and activated a clean generation.
                    _app.state.runtime_startup_failed = True
                    yield
                    return
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

    resolved_model_catalog_probe = (
        model_catalog_probe
        if model_catalog_probe is not None
        else AppServerModelCatalogProbe(
            cast(Any, resolved_app_server),
            codex_command=codex_command,
            codex_home=codex_home,
            timeout_seconds=model_discovery_timeout_seconds,
            cache_ttl_seconds=model_cache_ttl_seconds,
        )
        if resolved_app_server is not None
        else CodexModelCatalogProbe(
            codex_command=codex_command,
            codex_home=codex_home,
        )
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
    resolved_automations: AutomationStore | None = None
    resolved_capabilities_manager: CapabilitiesManager | None = None
    resolved_agents_manager: WorkspaceAgentsManager | None = None
    resolved_mcp_manager: McpManager | None = None
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        assert resolved_app_server is not None
        assert resolved_runtime_gate is not None

        def validate_automation_target(target: Mapping[str, Any]) -> None:
            try:
                if target.get("kind") == "standalone":
                    project = storage.load_project(str(target.get("project_id", "")))
                    if project.archived_at is not None:
                        raise AutomationValidationError(
                            "automation project is archived"
                        )
                    return
                if target.get("kind") == "continue_thread":
                    thread = storage.load_thread(str(target.get("thread_id", "")))
                    if thread.archived_at is not None:
                        raise AutomationValidationError("automation thread is archived")
                    return
            except AutomationValidationError:
                raise
            except Exception:
                raise AutomationValidationError(
                    "automation target is invalid"
                ) from None
            raise AutomationValidationError("automation target is invalid")

        resolved_automations = AutomationStore(
            storage.root,
            target_validator=validate_automation_target,
        )
        resolved_capabilities_manager = CapabilitiesManager(
            storage,
            cast(Any, resolved_app_server),
            resolved_runtime_gate,
        )
        resolved_agents_manager = WorkspaceAgentsManager(
            storage,
            runtime_gate=resolved_runtime_gate,
            private_backup_root=storage.root / "agent-backups",
            codex_home=codex_home,
        )
        resolved_mcp_manager = McpManager(
            cast(Any, resolved_app_server),
            resolved_runtime_gate,
            enabled=enable_mcp,
        )
    if resolved_app_server is not None:
        if auth_coordinator_factory is not None:
            resolved_auth_coordinator = auth_coordinator_factory(resolved_app_server)
        else:
            initial_auth_status = _load_durable_auth_status(
                storage.root / _AUTH_STATE_FILENAME
            )
            initial_auth_identity_status = (
                initial_auth_status or CodexAuthStatusRecord()
            )
            auth_catalog_identity = (
                initial_auth_identity_status.auth_mode,
                initial_auth_identity_status.auth_required,
                initial_auth_identity_status.plan_type,
            )

            def persist_auth_status(status: CodexAuthStatusRecord) -> None:
                nonlocal auth_catalog_identity
                payload = status.model_dump(mode="json")
                next_catalog_identity = (
                    status.auth_mode,
                    status.auth_required,
                    status.plan_type,
                )
                if next_catalog_identity != auth_catalog_identity:
                    invalidate_catalog = getattr(
                        resolved_model_catalog_probe, "invalidate", None
                    )
                    if callable(invalidate_catalog):
                        invalidate_catalog()
                    if resolved_capabilities_manager is not None:
                        resolved_capabilities_manager.invalidate_provider_capabilities()
                    auth_catalog_identity = next_catalog_identity
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
            max_chunk_body_bytes=8 * 1024 * 1024,
            # Session metadata is intentionally much smaller than the
            # durable manifest ceiling and is bounded before FastAPI/Pydantic
            # attempts to parse hostile JSON.
            max_session_body_bytes=64 * 1024,
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
    app.state.runtime_startup_failed = False
    # Task 10 accepts only an injected/proven health signal.  Task 21 owns
    # proving real OS confinement, so HA fails closed when none is supplied.
    app.state.sandbox_ready = sandbox_ready
    app.state.runtime_gate = resolved_runtime_gate
    app.state.auth_coordinator = resolved_auth_coordinator
    app.state.account_probe = resolved_account_probe
    app.state.diagnostics_probe = diagnostics_probe or BridgeDiagnosticsProbe(
        storage=storage,
        build_info=resolved_build_info,
        codex_command=codex_command,
        codex_home=codex_home,
        runtime_version_provider=(
            lambda: (
                getattr(resolved_app_server, "server_version", None)
                if resolved_app_server is not None
                else None
            )
        ),
    )
    app.state.model_catalog_probe = resolved_model_catalog_probe
    app.state.automations = resolved_automations
    app.state.capabilities_manager = resolved_capabilities_manager
    app.state.agents_manager = resolved_agents_manager
    app.state.mcp_manager = resolved_mcp_manager
    feature_capabilities = ["api_v1", "legacy_v0"]
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        feature_capabilities.extend(
            [
                "automations_v1",
                "skills_v1",
                "plugins_v1",
                "agents_v1",
            ]
        )
        # Elicitations must be rejected before we expose MCP administration.
        # Without the app-server callback, an OAuth-enabled MCP server could
        # request data through an interaction path the Bridge cannot control.
        if (
            resolved_mcp_manager is not None
            and resolved_mcp_manager.enabled
            and resolved_mcp_manager.elicitation_handler_registered
        ):
            feature_capabilities.append("mcp_admin_v1")
    app.state.feature_capabilities = tuple(feature_capabilities)
    app.state.auth_manager = (
        None
        if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT
        else auth_manager
        or CodexAuthManager(
            codex_command=codex_command,
            codex_home=codex_home,
        )
    )

    def automation_run_terminal(
        run_id: str,
        status: str,
        client_request_id: str,
        unattended: bool,
    ) -> None:
        if resolved_automations is None:
            return
        automation_status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "interrupted": "interrupted_restart",
        }.get(status)
        if automation_status is None:
            return
        try:
            resolved_automations.complete_runtime_run(
                run_id,
                client_request_id=client_request_id,
                unattended=unattended,
                status=automation_status,
            )
        except AutomationError:
            # Most interactive runs are not automation-owned.
            return

    resolved_runner = (
        runner_factory(storage)
        if runner_factory is not None
        else RuntimeBroker(
            storage,
            cast(Any, resolved_app_server),
            cast(RuntimeGate, resolved_runtime_gate),
            resource_limits=resolved_resource_limits,
            run_terminal_listener=automation_run_terminal,
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

    def dispatch_automation(claim: Mapping[str, Any]) -> object:
        if resolved_automations is None or resolved_runner is None:
            raise RuntimeError("automations are unavailable")
        automation_id = str(claim.get("automation_id", ""))
        automation_run_id = str(claim.get("automation_run_id", ""))
        definition = resolved_automations.get(automation_id)
        target = definition["target"]
        # Targets are validated when an automation is created or updated, but
        # the referenced project/thread can be archived or deleted while a
        # claim is waiting for dispatch.  Revalidate immediately before any
        # storage mutation or runtime submission so stale definitions fail
        # closed without creating a thread, updating one, or starting Codex.
        validate_automation_target(target)
        mode = RunMode(definition["mode"])
        try:
            web_search = claim.get("web_search")
            if web_search is not None and not supports_web_search(app.state):
                raise AutomationValidationError("web search is unavailable")
            with storage.prepare_automation_target(
                target,
                title=definition["name"],
                mode=mode,
                model_override=definition.get("model"),
                thinking_override=definition.get("thinking"),
            ) as thread:
                run = resolved_runner.submit_prompt(
                    thread.thread_id,
                    definition["prompt"],
                    client_request_id=f"automation:{automation_run_id}",
                    unattended=True,
                    web_search=web_search,
                )
        except (ProjectMutationError, FileNotFoundError) as error:
            raise AutomationValidationError(str(error)) from None
        resolved_automations.mark_running(
            automation_run_id,
            bridge_run_id=run.run_id,
        )
        return run

    app.state.automation_dispatch = (
        dispatch_automation if resolved_automations is not None else None
    )
    app.include_router(artifacts.router)
    if resolved_runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        app.include_router(approvals.router)
        app.include_router(agents.router)
        app.include_router(automations.router)
        app.include_router(capabilities.router)
        app.include_router(mcp.router)
        app.include_router(uploads.router)
    else:
        # The multipart endpoint is the external-v0 rollback adapter. HA uses
        # only bounded resumable chunks so Core never parses a whole file.
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
