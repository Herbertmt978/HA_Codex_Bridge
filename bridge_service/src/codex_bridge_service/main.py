from fastapi import FastAPI

from .account import CodexAccountProbe
from .app import create_app
from .build_info import BuildInfo
from .codex_process import resolve_codex_home
from .limits import CodexLimitsProbe
from .model_catalog import CodexModelCatalogProbe
from .models import RuntimeProfile
from .runner import BridgeRunner
from .sandbox_attestation import sandbox_attestation_ready
from .settings import Settings


def build_app() -> FastAPI:
    settings = Settings()
    build_info = BuildInfo.from_environment()
    codex_home = resolve_codex_home(settings.codex_home, settings.codex_wrapper_path)
    external_legacy = settings.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY
    return create_app(
        root_path=settings.root_path,
        runtime_profile=settings.runtime_profile,
        workspace_root=settings.workspace_root,
        resource_limits=settings.to_resource_limits(),
        auth_token=settings.auth_token,
        build_info=build_info,
        sandbox_ready=(
            sandbox_attestation_ready(build_info.model_dump())
            if not external_legacy
            else None
        ),
        limits_probe=(
            CodexLimitsProbe(codex_home) if external_legacy and codex_home else None
        ),
        account_probe=(
            CodexAccountProbe(codex_home) if external_legacy and codex_home else None
        ),
        model_catalog_probe=(
            CodexModelCatalogProbe(
                codex_command=settings.codex_wrapper_path,
                codex_home=codex_home,
                timeout_seconds=settings.model_discovery_timeout_seconds,
                cache_ttl_seconds=settings.model_cache_ttl_seconds,
            )
            if external_legacy
            else None
        ),
        codex_command=settings.codex_wrapper_path,
        codex_home=codex_home,
        run_idle_timeout_seconds=settings.run_idle_timeout_seconds,
        model_discovery_timeout_seconds=settings.model_discovery_timeout_seconds,
        model_cache_ttl_seconds=settings.model_cache_ttl_seconds,
        enable_mcp=settings.enable_mcp,
        runner_factory=(
            (
                lambda storage: BridgeRunner(
                    storage=storage,
                    codex_command=settings.codex_wrapper_path,
                    codex_home=codex_home,
                    bypass_sandbox=settings.bypass_sandbox,
                    ignore_user_config=settings.ignore_user_config,
                    idle_timeout_seconds=settings.run_idle_timeout_seconds,
                )
            )
            if external_legacy
            else None
        ),
        initialize_special_projects=True,
    )


app = build_app()
