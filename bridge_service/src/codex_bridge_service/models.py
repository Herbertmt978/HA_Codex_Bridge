import re
from typing import Annotated, Any, Literal, Self

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .api_contract import API_CONTRACT, ApiContractRecord
from .workspace import normalize_portable_relative_path

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
    GENERATED_IMAGE = "generated_image"


class EventScope(StrEnum):
    AUTH = "auth"
    RUNTIME = "runtime"
    THREAD = "thread"


class AttachmentRecord(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    stored_path: str
    relative_path: str | None = None
    size_bytes: int | None = None
    # Legacy multipart attachments predate integrity metadata.  New resumable
    # HA uploads always set this, while old persisted records remain readable.
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        return value


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


class EventRecord(BaseModel):
    """Safe API v1 projection of one globally ordered Bridge event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: int = Field(ge=1)
    event_id: str = Field(min_length=1, max_length=128)
    scope: EventScope
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_thread_scope(self) -> Self:
        if self.scope is EventScope.THREAD and self.thread_id is None:
            raise ValueError("thread events require a thread identifier")
        if self.scope is not EventScope.THREAD and self.thread_id is not None:
            raise ValueError("only thread events may include a thread identifier")
        return self


class EventBatchRecord(BaseModel):
    """Bounded replay/wait result addressed by a global cursor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    events: list[EventRecord] = Field(default_factory=list)
    next_cursor: int = Field(ge=0)
    minimum_cursor: int = Field(ge=0)
    has_more: bool = False
    heartbeat: bool = False

    @model_validator(mode="after")
    def validate_cursor_order(self) -> Self:
        cursors = [event.cursor for event in self.events]
        if any(current <= previous for previous, current in zip(cursors, cursors[1:])):
            raise ValueError("event cursors must be strictly increasing")
        if cursors and self.next_cursor < cursors[-1]:
            raise ValueError("next cursor cannot precede the last event")
        if self.heartbeat and (self.events or self.has_more):
            raise ValueError("heartbeat batches cannot include events or more pages")
        return self


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
    # ``codex_session_id`` belongs to the deprecated ``codex exec`` adapter.
    # The app-server thread identifier is deliberately separate so a fresh HA
    # runtime can never import or resume a legacy VM session by accident.
    codex_session_id: str | None = None
    codex_thread_id: str | None = None
    active_turn_id: str | None = None
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


BoundedAnswerValue = Annotated[str, Field(min_length=1, max_length=4096)]


class InteractionOptionRecord(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=512)


class InteractionQuestionRecord(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    header: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=2048)
    options: list[InteractionOptionRecord] = Field(default_factory=list, max_length=32)
    multiple: bool = False
    allow_free_text: bool = False


class InteractionDisplayRecord(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=512)
    command: str | None = Field(default=None, max_length=512)
    workspace_paths: list[str] = Field(default_factory=list, max_length=128)
    questions: list[InteractionQuestionRecord] = Field(
        default_factory=list, max_length=32
    )

    @field_validator("workspace_paths")
    @classmethod
    def validate_workspace_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                normalized = normalize_portable_relative_path(value)
            except ValueError:
                raise ValueError(
                    "interaction workspace paths must be safe and relative"
                )
            if len(value) > 240 or normalized != value:
                raise ValueError(
                    "interaction workspace paths must be safe and relative"
                )
        return values


class PendingInteractionRecord(BaseModel):
    interaction_id: str = Field(min_length=1, max_length=128)
    kind: Literal["command_approval", "file_change_approval", "user_input"]
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)
    event_id: int = Field(ge=0)
    status: Literal["pending"] = "pending"
    expires_at: str = Field(min_length=1, max_length=64)
    display: InteractionDisplayRecord
    allowed_actions: list[Literal["accept", "decline", "cancel", "answer"]] = Field(
        max_length=4
    )


class PendingInteractionCollectionRecord(BaseModel):
    items: list[PendingInteractionRecord]
    count: int = Field(ge=0)
    thread_id: str | None = Field(default=None, max_length=128)


class InteractionDecisionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)
    decision: Literal["accept", "decline", "cancel"]
    client_request_id: str = Field(min_length=1, max_length=256)


class InteractionAnswerRecord(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    values: list[BoundedAnswerValue] = Field(min_length=1, max_length=32)


class InteractionAnswerRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)
    answers: list[InteractionAnswerRecord] = Field(min_length=1, max_length=32)
    client_request_id: str = Field(min_length=1, max_length=256)

    @field_validator("answers")
    @classmethod
    def validate_unique_question_ids(
        cls,
        values: list[InteractionAnswerRecord],
    ) -> list[InteractionAnswerRecord]:
        question_ids = [answer.question_id for answer in values]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question ids must be unique")
        return values


class InteractionResultRecord(BaseModel):
    interaction_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    status: Literal["accepted", "declined", "cancelled", "answered"]
    client_request_id: str = Field(min_length=1, max_length=256)


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

    state: Literal["ready", "auth_required", "degraded_catalogue", "fatal"] = "ready"
    reasons: tuple[str, ...] = ()


class SandboxStatusRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: int | None = None
    attested: bool = False


class BridgeReadinessRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    api: ApiContractRecord = Field(default_factory=lambda: API_CONTRACT)
    app: ComponentVersionRecord = Field(default_factory=ComponentVersionRecord)
    bridge: ComponentVersionRecord
    codex: ComponentVersionRecord = Field(default_factory=ComponentVersionRecord)
    image: ImageBuildRecord = Field(default_factory=ImageBuildRecord)
    architecture: Literal["amd64", "aarch64", "unknown"] = "unknown"
    capabilities: tuple[
        Literal[
            "api_v1",
            "legacy_v0",
            "automations_v1",
            "mcp_admin_v1",
            "skills_v1",
            "plugins_v1",
            "agents_v1",
            "web_search_v1",
            "image_generation_v1",
        ],
        ...,
    ] = (
        "api_v1",
        "legacy_v0",
    )
    sandbox: SandboxStatusRecord = Field(default_factory=SandboxStatusRecord)
    readiness: ReadinessStateRecord = Field(default_factory=ReadinessStateRecord)


class BridgeDiagnosticsRecord(BaseModel):
    app_version: str | None = None
    bridge_version: str | None = None
    api_current: int = API_CONTRACT.current
    api_minimum: int = API_CONTRACT.minimum
    api_maximum: int = API_CONTRACT.maximum
    bundled_codex_version: str | None = None
    active_codex_version: str | None = None
    codex_version_match: bool | None = None
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


class ProviderCapabilitiesRecord(BaseModel):
    """Bounded provider capability state safe to expose to the Integration."""

    model_config = ConfigDict(frozen=True)

    image_generation: bool | None = None
    web_search: bool | None = None
    namespace_tools: bool | None = None


class BridgeStatusRecord(BaseModel):
    models: list[str] = Field(default_factory=lambda: list(SUPPORTED_MODELS))
    thinking_levels: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_THINKING_LEVELS)
    )
    model_catalog: CodexModelCatalogRecord = Field(
        default_factory=CodexModelCatalogRecord
    )
    limits: LimitsStatusRecord = Field(default_factory=LimitsStatusRecord)
    account: CodexAccountRecord = Field(default_factory=CodexAccountRecord)
    auth: CodexAuthStatusRecord = Field(default_factory=CodexAuthStatusRecord)
    diagnostics: BridgeDiagnosticsRecord = Field(
        default_factory=BridgeDiagnosticsRecord
    )
    provider_capabilities: ProviderCapabilitiesRecord = Field(
        default_factory=lambda: ProviderCapabilitiesRecord()
    )


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
