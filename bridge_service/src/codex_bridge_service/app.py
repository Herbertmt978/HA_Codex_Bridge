from pathlib import Path

from fastapi import FastAPI

from .account import CodexAccountProbe
from .diagnostics import BridgeDiagnosticsProbe
from .limits import CodexLimitsProbe
from .routes import artifacts, attachments, events, health, projects, prompts, status, threads
from .runner import BridgeRunner
from .storage import BridgeStorage


def create_app(
    root_path: Path | str,
    auth_token: str,
    limits_probe: CodexLimitsProbe | None = None,
    account_probe: CodexAccountProbe | None = None,
    diagnostics_probe: BridgeDiagnosticsProbe | None = None,
    codex_command: str = "codex",
    run_idle_timeout_seconds: float | None = 1800.0,
    runner_factory=None,
) -> FastAPI:
    app = FastAPI(title="Codex Bridge")
    storage = BridgeStorage(root_path=root_path, limits_probe=limits_probe)
    app.state.storage = storage
    app.state.auth_token = auth_token
    app.state.account_probe = account_probe
    app.state.diagnostics_probe = diagnostics_probe or BridgeDiagnosticsProbe(
        storage=storage,
        codex_command=codex_command,
    )
    app.state.runner = (
        runner_factory(storage)
        if runner_factory is not None
        else BridgeRunner(
            storage,
            codex_command=codex_command,
            idle_timeout_seconds=run_idle_timeout_seconds,
        )
    )
    app.include_router(artifacts.router)
    app.include_router(attachments.router)
    app.include_router(events.router)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(prompts.router)
    app.include_router(status.router)
    app.include_router(threads.router)
    return app
