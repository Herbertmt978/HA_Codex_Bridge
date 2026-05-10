from typing import Any

from enum import StrEnum

from pydantic import BaseModel, Field

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_THINKING_LEVEL = "medium"
SUPPORTED_MODELS = [
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
]
SUPPORTED_THINKING_LEVELS = [
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
]


class RunMode(StrEnum):
    OBSERVE = "observe"
    EDIT = "edit"
    FULL_AUTO = "full-auto"


class ProjectKind(StrEnum):
    PROJECT = "project"
    DIRECT = "direct"
    IMPORTED = "imported"


class AttachmentRecord(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    stored_path: str
    relative_path: str | None = None
    size_bytes: int | None = None


class ArtifactRecord(BaseModel):
    artifact_id: str
    filename: str
    mime_type: str
    stored_path: str
    relative_path: str | None = None
    size_bytes: int | None = None


class ProjectRecord(BaseModel):
    project_id: str
    name: str
    root_path: str
    kind: ProjectKind = ProjectKind.PROJECT
    default_model: str = DEFAULT_MODEL
    default_thinking_level: str = DEFAULT_THINKING_LEVEL
    created_at: str
    updated_at: str
    archived_at: str | None = None


class ThreadEventRecord(BaseModel):
    event_id: str
    thread_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str


class ThreadRecord(BaseModel):
    thread_id: str
    project_id: str | None = None
    title: str
    workspace_id: str
    workspace_path: str
    status: str
    mode: RunMode = Field(default=RunMode.FULL_AUTO)
    codex_session_id: str | None = None
    active_run_id: str | None = None
    last_error: str | None = None
    attachments: list[AttachmentRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    model_override: str | None = None
    thinking_override: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None


class ThreadViewRecord(ThreadRecord):
    project_name: str
    project_root_path: str
    project_kind: ProjectKind = ProjectKind.PROJECT
    default_model: str = DEFAULT_MODEL
    default_thinking_level: str = DEFAULT_THINKING_LEVEL
    effective_model: str = DEFAULT_MODEL
    effective_thinking_level: str = DEFAULT_THINKING_LEVEL


class RunRecord(BaseModel):
    run_id: str
    thread_id: str
    status: str


class LimitsWindowRecord(BaseModel):
    used_percent: float | None = None
    remaining_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None


class LimitsStatusRecord(BaseModel):
    available: bool = False
    blocked: bool = False
    message: str | None = None
    primary: LimitsWindowRecord | None = None
    secondary: LimitsWindowRecord | None = None
    credits: dict[str, Any] | None = None
    plan_type: str | None = None
    updated_at: str | None = None


class CodexAccountRecord(BaseModel):
    available: bool = False
    auth_mode: str | None = None
    email: str | None = None
    name: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    plan_type: str | None = None
    organization_id: str | None = None
    organization_title: str | None = None
    updated_at: str | None = None


class DiagnosticToolRecord(BaseModel):
    name: str
    available: bool = False
    path: str | None = None
    version: str | None = None


class BridgeDiagnosticsRecord(BaseModel):
    bridge_version: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    python_version: str | None = None
    python_executable: str | None = None
    platform: str | None = None
    codex_cli_version: str | None = None
    service_started_at: str | None = None
    service_uptime_seconds: float | None = None
    last_error: str | None = None
    tools: list[DiagnosticToolRecord] = Field(default_factory=list)


class BridgeStatusRecord(BaseModel):
    models: list[str] = Field(default_factory=lambda: list(SUPPORTED_MODELS))
    thinking_levels: list[str] = Field(default_factory=lambda: list(SUPPORTED_THINKING_LEVELS))
    limits: LimitsStatusRecord = Field(default_factory=LimitsStatusRecord)
    account: CodexAccountRecord = Field(default_factory=CodexAccountRecord)
    diagnostics: BridgeDiagnosticsRecord = Field(default_factory=BridgeDiagnosticsRecord)


class PathBrowseEntryRecord(BaseModel):
    path: str
    name: str


class PathBrowseRecord(BaseModel):
    path: str | None = None
    parent_path: str | None = None
    directories: list[PathBrowseEntryRecord] = Field(default_factory=list)


class ThreadCollectionRecord(BaseModel):
    projects: list[ProjectRecord] = Field(default_factory=list)
    threads: list[ThreadViewRecord] = Field(default_factory=list)
