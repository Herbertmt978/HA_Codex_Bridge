from pathlib import PurePosixPath, PureWindowsPath

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import RuntimeProfile
from .resource_limits import ResourceLimits


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
    max_active_turns: int = Field(default=1, gt=0)
    max_queued_prompts: int = Field(default=8, ge=0)
    run_total_timeout_seconds: float = Field(default=4 * 60 * 60, gt=0)
    ha_run_idle_timeout_seconds: float = Field(default=10 * 60, gt=0)
    cancel_grace_seconds: float = Field(default=15, gt=0)
    max_upload_file_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    max_upload_request_overhead_bytes: int = Field(default=1024 * 1024, gt=0)
    max_workspace_bytes: int = Field(default=10 * 1024 * 1024 * 1024, gt=0)
    max_private_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    max_archive_entries: int = Field(default=20_000, gt=0)
    max_archive_expanded_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    max_archive_expansion_ratio: float = Field(default=100, gt=0)
    max_archive_metadata_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_events_per_thread: int = Field(default=25_000, gt=0)
    max_event_log_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_event_payload_bytes: int = Field(default=1024 * 1024, gt=0)
    service_log_file_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    service_log_backups: int = Field(default=10, ge=0)
    minimum_free_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)
    minimum_free_fraction: float = Field(default=0.05, ge=0, lt=1)
    max_transient_snapshot_bytes: int = Field(default=256 * 1024 * 1024, gt=0)

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
        if not (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
        ):
            raise ValueError("workspace root must be absolute")
        return value

    @model_validator(mode="after")
    def require_home_assistant_workspace_root(self) -> "Settings":
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT and self.workspace_root is None:
            raise ValueError("home_assistant profile requires a workspace root")
        return self

    def to_resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            max_active_turns=self.max_active_turns,
            max_queued_prompts=self.max_queued_prompts,
            run_total_timeout_seconds=self.run_total_timeout_seconds,
            run_idle_timeout_seconds=self.ha_run_idle_timeout_seconds,
            cancel_grace_seconds=self.cancel_grace_seconds,
            max_upload_file_bytes=self.max_upload_file_bytes,
            max_upload_request_overhead_bytes=self.max_upload_request_overhead_bytes,
            max_workspace_bytes=self.max_workspace_bytes,
            max_private_bytes=self.max_private_bytes,
            max_archive_entries=self.max_archive_entries,
            max_archive_expanded_bytes=self.max_archive_expanded_bytes,
            max_archive_expansion_ratio=self.max_archive_expansion_ratio,
            max_archive_metadata_bytes=self.max_archive_metadata_bytes,
            max_events_per_thread=self.max_events_per_thread,
            max_event_log_bytes=self.max_event_log_bytes,
            max_event_payload_bytes=self.max_event_payload_bytes,
            service_log_file_bytes=self.service_log_file_bytes,
            service_log_backups=self.service_log_backups,
            minimum_free_bytes=self.minimum_free_bytes,
            minimum_free_fraction=self.minimum_free_fraction,
            max_transient_snapshot_bytes=self.max_transient_snapshot_bytes,
        )
