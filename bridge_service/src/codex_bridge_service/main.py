from pathlib import Path

from fastapi import FastAPI

from .account import CodexAccountProbe
from .app import create_app
from .limits import CodexLimitsProbe
from .runner import BridgeRunner
from .settings import Settings


def _resolve_codex_home(settings: Settings) -> Path | None:
    if settings.codex_home:
        return Path(settings.codex_home)

    wrapper_path = Path(settings.codex_wrapper_path)
    if wrapper_path.suffix:
        for parent in wrapper_path.parents:
            if parent.name == ".codex":
                return parent
    return Path.home() / ".codex"


def build_app() -> FastAPI:
    settings = Settings()
    codex_home = _resolve_codex_home(settings)
    return create_app(
        root_path=settings.root_path,
        auth_token=settings.auth_token,
        limits_probe=CodexLimitsProbe(codex_home) if codex_home else None,
        account_probe=CodexAccountProbe(codex_home) if codex_home else None,
        codex_command=settings.codex_wrapper_path,
        run_idle_timeout_seconds=settings.run_idle_timeout_seconds,
        runner_factory=lambda storage: BridgeRunner(
            storage=storage,
            codex_command=settings.codex_wrapper_path,
            bypass_sandbox=settings.bypass_sandbox,
            ignore_user_config=settings.ignore_user_config,
            idle_timeout_seconds=settings.run_idle_timeout_seconds,
        ),
    )


app = build_app()
