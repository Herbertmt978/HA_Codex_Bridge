from pathlib import Path

from fastapi import FastAPI

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
        runner_factory=lambda storage: BridgeRunner(
            storage=storage,
            codex_command=settings.codex_wrapper_path,
            bypass_sandbox=settings.bypass_sandbox,
        ),
    )


app = build_app()
