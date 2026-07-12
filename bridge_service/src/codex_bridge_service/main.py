from fastapi import FastAPI

from .account import CodexAccountProbe
from .app import create_app
from .codex_process import resolve_codex_home
from .limits import CodexLimitsProbe
from .model_catalog import CodexModelCatalogProbe
from .runner import BridgeRunner
from .settings import Settings

def build_app() -> FastAPI:
    settings = Settings()
    codex_home = resolve_codex_home(settings.codex_home, settings.codex_wrapper_path)
    return create_app(
        root_path=settings.root_path,
        runtime_profile=settings.runtime_profile,
        workspace_root=settings.workspace_root,
        auth_token=settings.auth_token,
        limits_probe=CodexLimitsProbe(codex_home) if codex_home else None,
        account_probe=CodexAccountProbe(codex_home) if codex_home else None,
        model_catalog_probe=CodexModelCatalogProbe(
            codex_command=settings.codex_wrapper_path,
            codex_home=codex_home,
            timeout_seconds=settings.model_discovery_timeout_seconds,
            cache_ttl_seconds=settings.model_cache_ttl_seconds,
        ),
        codex_command=settings.codex_wrapper_path,
        codex_home=codex_home,
        run_idle_timeout_seconds=settings.run_idle_timeout_seconds,
        runner_factory=lambda storage: BridgeRunner(
            storage=storage,
            codex_command=settings.codex_wrapper_path,
            codex_home=codex_home,
            bypass_sandbox=settings.bypass_sandbox,
            ignore_user_config=settings.ignore_user_config,
            idle_timeout_seconds=settings.run_idle_timeout_seconds,
        ),
        initialize_special_projects=True,
    )


app = build_app()
