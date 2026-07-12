from pathlib import Path

from fastapi import FastAPI

from .account import CodexAccountProbe
from .build_info import BuildInfo
from .codex_auth import CodexAuthManager
from .diagnostics import BridgeDiagnosticsProbe
from .limits import CodexLimitsProbe
from .model_catalog import CodexModelCatalogProbe
from .models import RuntimeProfile
from .routes import artifacts, attachments, codex_auth, events, health, projects, prompts, status, threads
from .runner import BridgeRunner
from .storage import BridgeStorage


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
) -> FastAPI:
    app = FastAPI(title="Codex Bridge")
    resolved_build_info = (
        build_info if build_info is not None else BuildInfo.from_environment()
    )
    resolved_model_catalog_probe = model_catalog_probe or CodexModelCatalogProbe(
        codex_command=codex_command,
        codex_home=codex_home,
    )

    def special_project_defaults() -> tuple[str, str, bool]:
        catalog = resolved_model_catalog_probe.probe()
        return catalog.default_model, catalog.default_thinking_level, catalog.stale

    storage = BridgeStorage(
        root_path=root_path,
        limits_probe=limits_probe,
        special_project_defaults_provider=special_project_defaults,
        runtime_profile=runtime_profile,
        workspace_root=workspace_root,
    )
    if initialize_special_projects:
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
    app.state.auth_token = auth_token
    app.state.build_info = resolved_build_info
    app.state.account_probe = account_probe
    app.state.diagnostics_probe = diagnostics_probe or BridgeDiagnosticsProbe(
        storage=storage,
        build_info=resolved_build_info,
        codex_command=codex_command,
        codex_home=codex_home,
    )
    app.state.model_catalog_probe = resolved_model_catalog_probe
    app.state.auth_manager = auth_manager or CodexAuthManager(
        codex_command=codex_command,
        codex_home=codex_home,
    )
    app.state.runner = (
        runner_factory(storage)
        if runner_factory is not None
        else BridgeRunner(
            storage,
            codex_command=codex_command,
            codex_home=codex_home,
            idle_timeout_seconds=run_idle_timeout_seconds,
        )
    )
    app.include_router(artifacts.router)
    app.include_router(attachments.router)
    app.include_router(codex_auth.router)
    app.include_router(events.router)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(prompts.router)
    app.include_router(status.router)
    app.include_router(threads.router)
    return app
