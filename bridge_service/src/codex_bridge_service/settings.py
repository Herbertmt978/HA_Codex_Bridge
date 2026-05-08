from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEX_BRIDGE_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8766
    root_path: str = "C:/CodexHA"
    auth_token: str = "change-me"
    codex_wrapper_path: str = "codex"
