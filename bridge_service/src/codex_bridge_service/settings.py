from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import RuntimeProfile


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEX_BRIDGE_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    host: str = "127.0.0.1"
    port: int = 8766
    root_path: str = "C:/CodexHA"
    runtime_profile: RuntimeProfile = RuntimeProfile.EXTERNAL_LEGACY
    workspace_root: str | None = None
    auth_token: str
    codex_wrapper_path: str = "codex"
    codex_home: str | None = None
    bypass_sandbox: bool = False
    ignore_user_config: bool = False
    run_idle_timeout_seconds: float | None = 1800.0
    model_discovery_timeout_seconds: float = 10.0
    model_cache_ttl_seconds: float = 600.0

    @field_validator("auth_token")
    @classmethod
    def validate_auth_token(cls, value: str) -> str:
        token = value.strip()
        known_placeholders = {
            "change-me",
            "replace-this-with-a-long-random-token",
        }
        if token.lower() in known_placeholders or len(token) < 32:
            raise ValueError("auth token must be an explicit random value of at least 32 characters")
        return token

    @field_validator("workspace_root")
    @classmethod
    def validate_workspace_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or value != value.strip():
            raise ValueError("workspace root must be a nonblank trimmed path")
        return value

    @model_validator(mode="after")
    def require_home_assistant_workspace_root(self) -> "Settings":
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT and self.workspace_root is None:
            raise ValueError("home_assistant profile requires a workspace root")
        return self
