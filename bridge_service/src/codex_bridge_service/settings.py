from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEX_BRIDGE_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8766
    root_path: str = "C:/CodexHA"
    auth_token: str = "change-me"
    codex_wrapper_path: str = "codex"
    codex_home: str | None = None
    bypass_sandbox: bool = False
    ignore_user_config: bool = False
    run_idle_timeout_seconds: float | None = 1800.0
