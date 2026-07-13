from typing import Any, Literal

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .api_contract import API_CONTRACT, ApiContractRecord

DEFAULT_MODEL = "gpt-5.5"
DEFAULT_THINKING_LEVEL = "medium"
SUPPORTED_MODELS = [
    "gpt-5.5",
    "gpt-5.4-mini",
]
SUPPORTED_THINKING_LEVELS = [
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
]


def normalize_model(model: str | None) -> str:
    if not model or not model.strip():
        return DEFAULT_MODEL
    return model.strip()


class RunMode(StrEnum):
    OBSERVE = "observe"
    EDIT = "edit"
    FULL_AUTO = "full-auto"


class ProjectKind(StrEnum):
    PROJECT = "project"
    DIRECT = "direct"
    IMPORTED = "imported"


class ProjectDefaultsOrigin(StrEnum):
    LEGACY = "legacy"
    CODEX = "codex"
    FALLBACK = "fallback"
    EXPLICIT = "explicit"


class RuntimeProfile(StrEnum):
    EXTERNAL_LEGACY = "external_legacy"
    HOME_ASSISTANT = "home_assistant"


class ArtifactSource(StrEnum):
    WORKSPACE = "workspace"
    WORKSPACE_ARCHIVE = "workspace_archive"


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
    source: ArtifactSource = ArtifactSource.WORKSPACE


class ProjectRecord(BaseModel):
    project_id: str
    name: str
    root_path: str
    kind: ProjectKind = ProjectKind.PROJECT
    default_model: str = DEFAULT_MODEL
    default_thinking_level: str = DEFAULT_THINKING_LEVEL
    defaults_origin: ProjectDefaultsOrigin = ProjectDefaultsOrigin.LEGACY
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


class PendingPromptRecord(BaseModel):
    run_id: str
    prompt: str
    created_at: str


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
    pending_prompts: list[PendingPromptRecord] = Field(default_factory=list)
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


class CodexAuthStatusRecord(BaseModel):
    revision: int = Field(default=0, ge=0)
    state: str = "unknown"
    busy: bool = False
    auth_required: bool = False
    auth_mode: str | None = None
    plan_type: str | None = None
    message: str | None = None
    verification_uri: str | None = None
    login_url: str | None = None
    user_code: str | None = None
    output_tail: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class CodexModelRecord(BaseModel):
    model: str
    display_name: str
    description: str | None = None
    is_default: bool = False
    default_thinking_level: str = DEFAULT_THINKING_LEVEL
    thinking_levels: list[str] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=list)
    catalogued: bool = True


class CodexModelCatalogRecord(BaseModel):
    source: str = "fallback"
    models: list[CodexModelRecord] = Field(default_factory=list)
    default_model: str = DEFAULT_MODEL
    default_thinking_level: str = DEFAULT_THINKING_LEVEL
    configured_model: str | None = None
    configured_thinking_level: str | None = None
    refreshed_at: str | None = None
    stale: bool = False
    error: str | None = None


class DiagnosticToolRecord(BaseModel):
    name: str
    available: bool = False
    path: str | None = None
    version: str | None = None


class ComponentVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str | None = None


class ImageBuildRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: str | None = None
    release_lock_digest: str | None = None


class ReadinessStateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: Literal["ready"] = "ready"
    reasons: tuple[str, ...] = ()


class BridgeReadinessRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    api: ApiContractRecord = Field(default_factory=lambda: API_CONTRACT)
    app: ComponentVersionRecord = Field(default_factory=ComponentVersionRecord)
    bridge: ComponentVersionRecord
    codex: ComponentVersionRecord = Field(default_factory=ComponentVersionRecord)
    image: ImageBuildRecord = Field(default_factory=ImageBuildRecord)
    architecture: Literal["amd64", "aarch64", "unknown"] = "unknown"
    capabilities: tuple[Literal["api_v1"], Literal["legacy_v0"]] = (
        "api_v1",
        "legacy_v0",
    )
    readiness: ReadinessStateRecord = Field(default_factory=ReadinessStateRecord)


class BridgeDiagnosticsRecord(BaseModel):
    app_version: str | None = None
    bridge_version: str | None = None
    api_current: int = API_CONTRACT.current
    api_minimum: int = API_CONTRACT.minimum
    api_maximum: int = API_CONTRACT.maximum
    bundled_codex_version: str | None = None
    image_revision: str | None = None
    architecture: Literal["amd64", "aarch64", "unknown"] = "unknown"
    release_lock_digest: str | None = None
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
    model_catalog: CodexModelCatalogRecord = Field(default_factory=CodexModelCatalogRecord)
    limits: LimitsStatusRecord = Field(default_factory=LimitsStatusRecord)
    account: CodexAccountRecord = Field(default_factory=CodexAccountRecord)
    auth: CodexAuthStatusRecord = Field(default_factory=CodexAuthStatusRecord)
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
