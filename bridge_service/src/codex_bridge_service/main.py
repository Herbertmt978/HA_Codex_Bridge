from fastapi import FastAPI

from .app import create_app
from .runner import BridgeRunner
from .settings import Settings


def build_app() -> FastAPI:
    settings = Settings()
    return create_app(
        root_path=settings.root_path,
        auth_token=settings.auth_token,
        runner_factory=lambda storage: BridgeRunner(
            storage=storage,
            codex_command=settings.codex_wrapper_path,
        ),
    )


app = build_app()
