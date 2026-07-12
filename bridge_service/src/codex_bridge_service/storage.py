import json
import mimetypes
import re
import string
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock, RLock
from typing import BinaryIO, Callable, Literal
from uuid import uuid4
import zipfile

from .limits import CodexLimitsProbe
from .models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    ArtifactRecord,
    AttachmentRecord,
    LimitsStatusRecord,
    LimitsWindowRecord,
    PathBrowseEntryRecord,
    PathBrowseRecord,
    ProjectDefaultsOrigin,
    ProjectKind,
    ProjectRecord,
    RuntimeProfile,
    RunMode,
    ThreadEventRecord,
    ThreadRecord,
    ThreadViewRecord,
    normalize_model,
)
from .workspace import WorkspaceBoundary, WorkspaceEscapeError, WorkspaceInputError

_UNSET = object()
_WORKSPACE_ID_PATTERN = re.compile(r"^ws_[0-9a-f]{12}$")


class ThreadNotFoundError(FileNotFoundError):
    pass


class ProjectNotFoundError(FileNotFoundError):
    pass


class ProjectMutationError(ValueError):
    pass


class BridgeStorage:
    imported_project_name = "Imported Threads"
    direct_project_name = "Direct chats"

    def __init__(
        self,
        root_path: Path | str,
        *,
        limits_probe: CodexLimitsProbe | None = None,
        special_project_defaults_provider: Callable[[], tuple[str, str, bool]] | None = None,
        runtime_profile: RuntimeProfile | str = RuntimeProfile.EXTERNAL_LEGACY,
        workspace_root: Path | str | None = None,
    ) -> None:
        try:
            self.runtime_profile = RuntimeProfile(runtime_profile)
        except ValueError:
            raise ValueError("runtime profile is invalid") from None
        self.root = Path(root_path)
        self.projects_dir = self.root / "projects"
        self.threads_dir = self.root / "threads"
        self.workspaces_dir = self.root / "workspaces"
        self.project_workspaces_dir = self.root / "project-workspaces"
        self.uploads_dir = self.root / "uploads"
        self.artifacts_dir = self.root / "artifacts"
        self.logs_dir = self.root / "logs"
        self.workspace_boundary: WorkspaceBoundary | None = None
        self.workspace_root: Path | None = None
        self.limits_status_path = self.root / "limits_status.json"
        self.limits_probe = limits_probe
        self.special_project_defaults_provider = special_project_defaults_provider
        self._special_default_migration_enabled = False
        self._special_default_migration_pending = False
        self._special_migration_lock = Lock()
        self._event_lock = Lock()
        self._project_mutation_lock = RLock()

        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            if workspace_root is None or not str(workspace_root).strip():
                raise ValueError("home_assistant profile requires a workspace root")
            state_identity = self.root.resolve(strict=False)
            workspace_identity = Path(workspace_root).resolve(strict=False)
            if (
                state_identity == workspace_identity
                or workspace_identity.is_relative_to(state_identity)
                or state_identity.is_relative_to(workspace_identity)
            ):
                raise ValueError("workspace root must be separate from private state")
            self.workspace_boundary = WorkspaceBoundary(workspace_root, create=True)
            self.workspace_root = self.workspace_boundary.root
            self.workspaces_dir = self.workspace_root
            self.project_workspaces_dir = self.workspace_root

        private_directories = (
            self.projects_dir,
            self.threads_dir,
            self.uploads_dir,
            self.artifacts_dir,
            self.logs_dir,
        )
        for directory in private_directories:
            directory.mkdir(parents=True, exist_ok=True)

        if self.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY:
            self.workspaces_dir.mkdir(parents=True, exist_ok=True)
            self.project_workspaces_dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _project_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def _thread_path(self, thread_id: str) -> Path:
        return self.threads_dir / f"{thread_id}.json"

    def _event_log_path(self, thread_id: str) -> Path:
        return self.logs_dir / f"{thread_id}.events.jsonl"

    def _atomic_write_json(self, target: Path, payload: dict[str, object]) -> None:
        temp_target = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temp_target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_target.replace(target)

    def _normalize_root_path(self, root_path: str) -> Path:
        target = Path(root_path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        return target

    def _home_assistant_boundary(self) -> WorkspaceBoundary:
        boundary = self.workspace_boundary
        if boundary is None:
            raise RuntimeError("workspace boundary is unavailable")
        return boundary

    def _special_project_root(self) -> str:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            return "."
        return str(self.workspaces_dir)

    def resolve_workspace_path(
        self,
        workspace_path: str,
        *,
        must_exist: bool = True,
        kind: Literal["file", "directory"] | None = "directory",
    ) -> Path:
        """Resolve a trusted internal path without changing its public representation."""
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            return self._home_assistant_boundary().resolve_relative(
                workspace_path,
                must_exist=must_exist,
                kind=kind,
            )

        target = self._normalize_root_path(workspace_path)
        if must_exist and not target.exists():
            raise FileNotFoundError("workspace path not found")
        if kind == "directory" and (not target.exists() or not target.is_dir()):
            raise NotADirectoryError("workspace path is not a directory")
        if kind == "file" and (not target.exists() or not target.is_file()):
            raise FileNotFoundError("workspace file not found")
        return target

    def _safe_project_folder_name(self, name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join(
            " " if char in invalid_chars or ord(char) < 32 else char
            for char in name.strip()
        )
        cleaned = " ".join(cleaned.split()).strip(" .")
        return cleaned or "Project"

    def _default_project_root(self, name: str) -> Path:
        folder_name = self._safe_project_folder_name(name)
        candidate = self.project_workspaces_dir / folder_name
        suffix = 2
        while candidate.exists():
            candidate = self.project_workspaces_dir / f"{folder_name} {suffix}"
            suffix += 1
        return candidate

    def _default_home_assistant_project_root(self, name: str) -> str:
        boundary = self._home_assistant_boundary()
        folder_name = self._safe_project_folder_name(name)
        # Reserve room for the separator and random suffix while retaining a
        # readable workspace name. Randomized names avoid a create/check race.
        folder_name = folder_name[:246].rstrip(" .") or "Project"
        try:
            folder_name = boundary.normalize(folder_name)
        except ValueError:
            folder_name = "Project"
        relative = f"{folder_name}-{uuid4().hex[:8]}"
        return boundary.create_directory(relative)

    def _ensure_project_workspace(self, record: ProjectRecord) -> ProjectRecord:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return record
        boundary = self._home_assistant_boundary()
        is_special = self.is_special_project_id(record.project_id) or record.kind in {
            ProjectKind.DIRECT,
            ProjectKind.IMPORTED,
        }
        normalized = "." if is_special else boundary.normalize(record.root_path)
        boundary.resolve_relative(normalized, must_exist=True, kind="directory")
        if record.root_path != normalized:
            record.root_path = normalized
            self.save_project(record)
        return record

    def _ensure_thread_workspace(self, record: ThreadRecord) -> ThreadRecord:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return record
        boundary = self._home_assistant_boundary()
        normalized = boundary.normalize(record.workspace_path)
        boundary.resolve_relative(normalized, must_exist=True, kind="directory")
        if record.workspace_path != normalized:
            record.workspace_path = normalized
            self.save_thread(record)
        return record

    def _validate_thread_project_workspace(
        self,
        record: ThreadRecord,
        project: ProjectRecord,
    ) -> None:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return
        boundary = self._home_assistant_boundary()
        if _WORKSPACE_ID_PATTERN.fullmatch(record.workspace_id) is None:
            raise WorkspaceInputError()
        if project.kind in (ProjectKind.DIRECT, ProjectKind.IMPORTED):
            expected = boundary.normalize(record.workspace_id)
        else:
            expected = boundary.normalize(project.root_path)
        if boundary.normalize(record.workspace_path) != expected:
            raise WorkspaceEscapeError()

    def _preflight_project_threads(self, project: ProjectRecord) -> list[ThreadRecord]:
        """Load associated HA threads without repairing or mutating persisted state."""
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return []
        boundary = self._home_assistant_boundary()
        records: list[ThreadRecord] = []
        for path in self.threads_dir.glob("*.json"):
            record = ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.project_id != project.project_id:
                continue
            normalized = boundary.normalize(record.workspace_path)
            if record.workspace_path != normalized:
                raise WorkspaceInputError()
            boundary.resolve_relative(normalized, must_exist=True, kind="directory")
            self._validate_thread_project_workspace(record, project)
            records.append(record)
        return records

    def _imported_project_id(self) -> str:
        return "prj_imported"

    def _direct_project_id(self) -> str:
        return "prj_direct"

    def is_special_project_id(self, project_id: str | None) -> bool:
        return project_id in {
            self._imported_project_id(),
            self._direct_project_id(),
        }

    def _special_project_defaults(
        self,
        default_model: str | None,
        default_thinking_level: str | None,
        defaults_provisional: bool | None,
    ) -> tuple[str, str, bool]:
        if default_model is not None and default_thinking_level is not None:
            return default_model, default_thinking_level, bool(defaults_provisional)
        discovered_model = DEFAULT_MODEL
        discovered_thinking = DEFAULT_THINKING_LEVEL
        discovered_provisional = False
        if self.special_project_defaults_provider is not None:
            (
                discovered_model,
                discovered_thinking,
                discovered_provisional,
            ) = self.special_project_defaults_provider()
        return (
            default_model or discovered_model,
            default_thinking_level or discovered_thinking,
            discovered_provisional if defaults_provisional is None else defaults_provisional,
        )

    def _migrate_provisional_special_defaults(
        self,
        record: ProjectRecord,
        *,
        default_model: str,
        default_thinking_level: str,
        defaults_provisional: bool,
    ) -> bool:
        if record.defaults_origin is not ProjectDefaultsOrigin.FALLBACK or defaults_provisional:
            return False
        record.default_model = default_model
        record.default_thinking_level = default_thinking_level
        record.defaults_origin = ProjectDefaultsOrigin.CODEX
        record.updated_at = self._now()
        return True

    def _migrate_legacy_special_defaults(
        self,
        record: ProjectRecord,
        *,
        default_model: str,
        default_thinking_level: str,
    ) -> bool:
        if not self._special_default_migration_enabled:
            return False
        if record.defaults_origin is not ProjectDefaultsOrigin.LEGACY:
            return False
        if (
            record.default_model != DEFAULT_MODEL
            or record.default_thinking_level != DEFAULT_THINKING_LEVEL
        ):
            return False
        if (
            default_model == DEFAULT_MODEL
            and default_thinking_level == DEFAULT_THINKING_LEVEL
        ):
            return False
        self._materialize_special_thread_defaults(record)
        record.default_model = default_model
        record.default_thinking_level = default_thinking_level
        record.defaults_origin = ProjectDefaultsOrigin.CODEX
        record.updated_at = self._now()
        return True

    def _materialize_special_thread_defaults(self, project: ProjectRecord) -> None:
        for path in self.threads_dir.glob("*.json"):
            try:
                thread = ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            belongs_to_project = thread.project_id == project.project_id
            if project.project_id == self._imported_project_id() and thread.project_id is None:
                belongs_to_project = True
            if not belongs_to_project:
                continue
            changed = False
            if thread.model_override is None:
                thread.model_override = project.default_model
                changed = True
            if thread.thinking_override is None:
                thread.thinking_override = project.default_thinking_level
                changed = True
            if changed:
                self.save_thread(thread)

    def _materialize_missing_special_defaults(
        self,
        *,
        project_id: str,
        name: str,
        kind: ProjectKind,
        discovered_model: str,
        discovered_thinking_level: str,
        defaults_provisional: bool,
    ) -> None:
        if (
            not defaults_provisional
            and discovered_model == DEFAULT_MODEL
            and discovered_thinking_level == DEFAULT_THINKING_LEVEL
        ):
            return
        timestamp = self._now()
        legacy_project = ProjectRecord(
            project_id=project_id,
            name=name,
            root_path=self._special_project_root(),
            kind=kind,
            default_model=DEFAULT_MODEL,
            default_thinking_level=DEFAULT_THINKING_LEVEL,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._materialize_special_thread_defaults(legacy_project)

    def initialize_special_projects(
        self,
        *,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        defaults_provisional: bool | None = None,
    ) -> None:
        self._special_default_migration_enabled = True
        try:
            self.list_projects(
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
        finally:
            self._special_default_migration_enabled = False

    def defer_special_project_migration(self) -> None:
        with self._special_migration_lock:
            self._special_default_migration_pending = True

    def reconcile_special_projects(
        self,
        *,
        default_model: str,
        default_thinking_level: str,
        defaults_provisional: bool,
    ) -> bool:
        if defaults_provisional:
            return False
        with self._special_migration_lock:
            if not self._special_default_migration_pending or self._has_active_thread_runs():
                return False
            self.initialize_special_projects(
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=False,
            )
            self._special_default_migration_pending = False
            return True

    def _has_active_thread_runs(self) -> bool:
        for path in self.threads_dir.glob("*.json"):
            try:
                record = ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.status in {"queued", "running", "cancelling"}:
                return True
        return False

    def _ensure_thread_timestamps(self, record: ThreadRecord) -> ThreadRecord:
        changed = False
        if not record.created_at:
            record.created_at = self._now()
            changed = True
        if not record.updated_at:
            record.updated_at = record.created_at
            changed = True
        if changed:
            self.save_thread(record)
        return record

    def _touch_thread(self, record: ThreadRecord) -> None:
        if not record.created_at:
            record.created_at = self._now()
        record.updated_at = self._now()

    def _has_legacy_threads(self) -> bool:
        for path in self.threads_dir.glob("*.json"):
            try:
                record = ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.project_id is None or record.project_id == self._imported_project_id():
                return True
        return False

    def ensure_imported_project(
        self,
        *,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        defaults_provisional: bool | None = None,
    ) -> ProjectRecord:
        default_model, default_thinking_level, defaults_provisional = self._special_project_defaults(
            default_model,
            default_thinking_level,
            defaults_provisional,
        )
        target = self._project_path(self._imported_project_id())
        if target.exists():
            record = ProjectRecord.model_validate_json(target.read_text(encoding="utf-8"))
            record = self._ensure_project_workspace(record)
            changed = self._migrate_provisional_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            changed = self._migrate_legacy_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
            ) or changed
            if record.kind is not ProjectKind.IMPORTED or record.name != self.imported_project_name:
                record.kind = ProjectKind.IMPORTED
                record.name = self.imported_project_name
                changed = True
            if changed:
                self.save_project(record)
            return record

        self._materialize_missing_special_defaults(
            project_id=self._imported_project_id(),
            name=self.imported_project_name,
            kind=ProjectKind.IMPORTED,
            discovered_model=default_model,
            discovered_thinking_level=default_thinking_level,
            defaults_provisional=defaults_provisional,
        )
        record = ProjectRecord(
            project_id=self._imported_project_id(),
            name=self.imported_project_name,
            root_path=self._special_project_root(),
            kind=ProjectKind.IMPORTED,
            default_model=default_model,
            default_thinking_level=default_thinking_level,
            defaults_origin=(
                ProjectDefaultsOrigin.FALLBACK
                if defaults_provisional
                else ProjectDefaultsOrigin.CODEX
            ),
            created_at=self._now(),
            updated_at=self._now(),
        )
        self.save_project(record)
        return record

    def ensure_direct_project(
        self,
        *,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        defaults_provisional: bool | None = None,
    ) -> ProjectRecord:
        default_model, default_thinking_level, defaults_provisional = self._special_project_defaults(
            default_model,
            default_thinking_level,
            defaults_provisional,
        )
        target = self._project_path(self._direct_project_id())
        if target.exists():
            record = ProjectRecord.model_validate_json(target.read_text(encoding="utf-8"))
            record = self._ensure_project_workspace(record)
            changed = self._migrate_provisional_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            changed = self._migrate_legacy_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
            ) or changed
            if record.kind is not ProjectKind.DIRECT or record.name != self.direct_project_name:
                record.kind = ProjectKind.DIRECT
                record.name = self.direct_project_name
                changed = True
            if changed:
                self.save_project(record)
            return record

        self._materialize_missing_special_defaults(
            project_id=self._direct_project_id(),
            name=self.direct_project_name,
            kind=ProjectKind.DIRECT,
            discovered_model=default_model,
            discovered_thinking_level=default_thinking_level,
            defaults_provisional=defaults_provisional,
        )
        record = ProjectRecord(
            project_id=self._direct_project_id(),
            name=self.direct_project_name,
            root_path=self._special_project_root(),
            kind=ProjectKind.DIRECT,
            default_model=default_model,
            default_thinking_level=default_thinking_level,
            defaults_origin=(
                ProjectDefaultsOrigin.FALLBACK
                if defaults_provisional
                else ProjectDefaultsOrigin.CODEX
            ),
            created_at=self._now(),
            updated_at=self._now(),
        )
        self.save_project(record)
        return record

    def load_project(self, project_id: str) -> ProjectRecord:
        target = self._project_path(project_id)
        if not target.exists():
            raise ProjectNotFoundError(project_id)
        record = ProjectRecord.model_validate_json(target.read_text(encoding="utf-8"))
        record = self._ensure_project_workspace(record)
        record = self._ensure_project_defaults(record)
        if project_id == self._imported_project_id() and record.kind is not ProjectKind.IMPORTED:
            record.kind = ProjectKind.IMPORTED
            record.name = self.imported_project_name
            self.save_project(record)
        if project_id == self._direct_project_id() and record.kind is not ProjectKind.DIRECT:
            record.kind = ProjectKind.DIRECT
            record.name = self.direct_project_name
            self.save_project(record)
        return record

    def _ensure_project_defaults(self, record: ProjectRecord) -> ProjectRecord:
        normalized_model = normalize_model(record.default_model)
        if normalized_model != record.default_model:
            record.default_model = normalized_model
            record.updated_at = self._now()
            self.save_project(record)
        return record

    def list_projects(
        self,
        *,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        defaults_provisional: bool | None = None,
    ) -> list[ProjectRecord]:
        records = {
            record.project_id: record
            for record in [
                self._ensure_project_defaults(
                    self._ensure_project_workspace(
                        ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    )
                )
                for path in self.projects_dir.glob("*.json")
            ]
        }
        direct = self.ensure_direct_project(
            default_model=default_model,
            default_thinking_level=default_thinking_level,
            defaults_provisional=defaults_provisional,
        )
        records[direct.project_id] = direct
        if self._project_path(self._imported_project_id()).exists() or self._has_legacy_threads():
            imported = self.ensure_imported_project(
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            records[imported.project_id] = imported
        ordered = sorted(records.values(), key=lambda record: record.updated_at, reverse=True)
        ordered.sort(key=self._project_rank)
        return ordered

    def _project_rank(self, record: ProjectRecord) -> int:
        if record.kind is ProjectKind.DIRECT:
            return 0
        if record.kind is ProjectKind.IMPORTED:
            return 2
        return 1

    def create_project(
        self,
        *,
        name: str,
        root_path: str | None = None,
        default_model: str = DEFAULT_MODEL,
        default_thinking_level: str = DEFAULT_THINKING_LEVEL,
    ) -> ProjectRecord:
        if not name.strip():
            raise ValueError("name must not be blank")

        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            project_root: Path | str
            if root_path and root_path.strip():
                project_root = boundary.create_directory(boundary.normalize(root_path))
            else:
                project_root = self._default_home_assistant_project_root(name)
        else:
            project_root = (
                self._normalize_root_path(root_path)
                if root_path and root_path.strip()
                else self._default_project_root(name)
            )
            project_root.mkdir(parents=True, exist_ok=True)
        now = self._now()
        record = ProjectRecord(
            project_id=f"prj_{uuid4().hex[:12]}",
            name=name.strip(),
            root_path=str(project_root),
            kind=ProjectKind.PROJECT,
            default_model=normalize_model(default_model),
            default_thinking_level=default_thinking_level or DEFAULT_THINKING_LEVEL,
            defaults_origin=ProjectDefaultsOrigin.EXPLICIT,
            created_at=now,
            updated_at=now,
        )
        self.save_project(record)
        return record

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        root_path: str | None = None,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
    ) -> ProjectRecord:
        with self._project_mutation_lock:
            return self._update_project_locked(
                project_id,
                name=name,
                root_path=root_path,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
            )

    def _update_project_locked(
        self,
        project_id: str,
        *,
        name: str | None = None,
        root_path: str | None = None,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
    ) -> ProjectRecord:
        record = self.load_project(project_id)
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be blank")
            record.name = name.strip()
        if root_path is not None:
            if (
                self.runtime_profile is RuntimeProfile.HOME_ASSISTANT
                and record.kind is not ProjectKind.PROJECT
            ):
                raise ProjectMutationError("special project workspaces cannot be changed")
            if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
                boundary = self._home_assistant_boundary()
                normalized_root = boundary.normalize(root_path)
                if normalized_root != record.root_path:
                    if self._preflight_project_threads(record):
                        raise ProjectMutationError(
                            "project workspace cannot be changed after chats are created"
                        )
                    record.root_path = boundary.create_directory(normalized_root)
            else:
                normalized = self._normalize_root_path(root_path)
                normalized.mkdir(parents=True, exist_ok=True)
                record.root_path = str(normalized)
        if default_model is not None:
            record.default_model = normalize_model(default_model)
        if default_thinking_level is not None:
            record.default_thinking_level = default_thinking_level or DEFAULT_THINKING_LEVEL
        if default_model is not None or default_thinking_level is not None:
            record.defaults_origin = ProjectDefaultsOrigin.EXPLICIT
        record.updated_at = self._now()
        self.save_project(record)
        return record

    def save_project(self, record: ProjectRecord) -> None:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            if self.is_special_project_id(record.project_id) or record.kind in {
                ProjectKind.DIRECT,
                ProjectKind.IMPORTED,
            }:
                if record.root_path != ".":
                    raise WorkspaceInputError()
            else:
                record.root_path = boundary.normalize(record.root_path)
                boundary.resolve_relative(record.root_path, must_exist=True, kind="directory")
        self._atomic_write_json(self._project_path(record.project_id), record.model_dump())

    def archive_project(self, project_id: str) -> ProjectRecord:
        record = self.load_project(project_id)
        if record.kind is not ProjectKind.PROJECT:
            raise ProjectMutationError("only normal projects can be archived")
        record.archived_at = self._now()
        record.updated_at = record.archived_at
        self.save_project(record)
        return record

    def restore_project(self, project_id: str) -> ProjectRecord:
        record = self.load_project(project_id)
        if record.kind is not ProjectKind.PROJECT:
            raise ProjectMutationError("only normal projects can be restored")
        record.archived_at = None
        record.updated_at = self._now()
        self.save_project(record)
        return record

    def delete_project(self, project_id: str) -> None:
        record = self.load_project(project_id)
        if record.kind is not ProjectKind.PROJECT:
            raise ProjectMutationError("only normal projects can be deleted")
        for thread in self.list_threads(include_archived=True):
            if thread.project_id == project_id:
                self.delete_thread(thread.thread_id)
        target = self._project_path(project_id)
        if target.exists():
            target.unlink()

    def browse_paths(self, path: str | None = None) -> PathBrowseRecord:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            relative = "." if path is None or not str(path).strip() else boundary.normalize(
                path,
                allow_root=True,
            )
            directories = [
                PathBrowseEntryRecord(
                    path=child,
                    name=PurePosixPath(child).name,
                )
                for child in boundary.list_directories(relative)
            ]
            parent_path: str | None = None
            if relative != ".":
                parent = PurePosixPath(relative).parent.as_posix()
                parent_path = "." if parent == "." else parent
            return PathBrowseRecord(
                path=relative,
                parent_path=parent_path,
                directories=directories,
            )

        if path is None or not str(path).strip():
            directories = []
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:\\")
                if drive.exists():
                    directories.append(
                        PathBrowseEntryRecord(
                            path=str(drive),
                            name=str(drive),
                        )
                    )
            return PathBrowseRecord(path=None, parent_path=None, directories=directories)

        target = self._normalize_root_path(path)
        if target.is_file():
            target = target.parent
        if not target.exists():
            raise FileNotFoundError(path)

        directories = [
            PathBrowseEntryRecord(path=str(child), name=child.name or str(child))
            for child in sorted(target.iterdir(), key=lambda item: item.name.lower())
            if child.is_dir()
        ]
        parent_path = str(target.parent) if target.parent != target else None
        return PathBrowseRecord(
            path=str(target),
            parent_path=parent_path,
            directories=directories,
        )

    def create_folder(self, *, parent_path: str, folder_name: str) -> PathBrowseEntryRecord:
        if not parent_path.strip():
            raise ValueError("parent_path must not be blank")
        if not folder_name.strip():
            raise ValueError("folder_name must not be blank")
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            parent = boundary.normalize(parent_path, allow_root=True)
            name = boundary.normalize(folder_name)
            if "/" in name:
                raise WorkspaceInputError()
            target = name if parent == "." else f"{parent}/{name}"
            created = boundary.create_directory(target)
            return PathBrowseEntryRecord(path=created, name=name)
        parent = self._normalize_root_path(parent_path)
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / folder_name.strip()
        target.mkdir(parents=True, exist_ok=True)
        return PathBrowseEntryRecord(path=str(target), name=target.name)

    def load_thread(self, thread_id: str) -> ThreadRecord:
        target = self._thread_path(thread_id)
        if not target.exists():
            raise ThreadNotFoundError(thread_id)
        record = ThreadRecord.model_validate_json(target.read_text(encoding="utf-8"))
        record = self._ensure_thread_workspace(record)
        record = self._ensure_thread_project(record)
        record = self._ensure_thread_model(record)
        return self._ensure_thread_timestamps(record)

    def get_thread(self, thread_id: str) -> ThreadViewRecord:
        return self._resolve_thread(self.load_thread(thread_id))

    def list_threads(self, *, include_archived: bool = False) -> list[ThreadViewRecord]:
        records = [
            self._ensure_thread_project(
                self._ensure_thread_workspace(
                    ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
                )
            )
            for path in self.threads_dir.glob("*.json")
        ]
        records = [self._ensure_thread_timestamps(record) for record in records]
        if not include_archived:
            records = [record for record in records if not record.archived_at]
        resolved = [self._resolve_thread(record) for record in records]
        return sorted(
            resolved,
            key=lambda record: record.updated_at or record.created_at or "",
            reverse=True,
        )

    def create_thread(
        self,
        *,
        title: str,
        mode: RunMode,
        project_id: str | None = None,
        model_override: str | None = None,
        thinking_override: str | None = None,
        direct_default_model: str | None = None,
        direct_default_thinking_level: str | None = None,
        direct_defaults_provisional: bool | None = None,
    ) -> ThreadViewRecord:
        with self._project_mutation_lock:
            return self._create_thread_locked(
                title=title,
                mode=mode,
                project_id=project_id,
                model_override=model_override,
                thinking_override=thinking_override,
                direct_default_model=direct_default_model,
                direct_default_thinking_level=direct_default_thinking_level,
                direct_defaults_provisional=direct_defaults_provisional,
            )

    def _create_thread_locked(
        self,
        *,
        title: str,
        mode: RunMode,
        project_id: str | None = None,
        model_override: str | None = None,
        thinking_override: str | None = None,
        direct_default_model: str | None = None,
        direct_default_thinking_level: str | None = None,
        direct_defaults_provisional: bool | None = None,
    ) -> ThreadViewRecord:
        if not title.strip():
            raise ValueError("title must not be blank")

        project = (
            self.ensure_direct_project(
                default_model=direct_default_model,
                default_thinking_level=direct_default_thinking_level,
                defaults_provisional=direct_defaults_provisional,
            )
            if project_id is None
            else self.load_project(project_id)
        )
        if project.defaults_origin is ProjectDefaultsOrigin.FALLBACK:
            if model_override is None:
                model_override = project.default_model
            if thinking_override is None:
                thinking_override = project.default_thinking_level
        workspace_id = f"ws_{uuid4().hex[:12]}"
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            workspace_path: Path | str
            if project.kind in (ProjectKind.DIRECT, ProjectKind.IMPORTED):
                workspace_path = boundary.create_directory(workspace_id)
            else:
                workspace_path = boundary.create_directory(boundary.normalize(project.root_path))
        else:
            workspace_root = Path(project.root_path)
            workspace_path = (
                workspace_root / workspace_id
                if project.kind in (ProjectKind.DIRECT, ProjectKind.IMPORTED)
                else workspace_root
            )
            workspace_path.mkdir(parents=True, exist_ok=True)
        now = self._now()

        record = ThreadRecord(
            thread_id=f"thr_{uuid4().hex[:12]}",
            project_id=project.project_id,
            title=title.strip(),
            workspace_id=workspace_id,
            workspace_path=str(workspace_path),
            status="idle",
            mode=mode,
            model_override=normalize_model(model_override) if model_override else None,
            thinking_override=thinking_override,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self.save_thread(record)
        self.append_thread_event(
            thread_id=record.thread_id,
            event_type="thread.created",
            payload={
                "title": record.title,
                "project_id": project.project_id,
                "project_name": project.name,
                "workspace_id": record.workspace_id,
                "workspace_path": record.workspace_path,
                "mode": record.mode.value,
                "model_override": record.model_override,
                "thinking_override": record.thinking_override,
                "created_at": record.created_at,
            },
        )
        return self._resolve_thread(record)

    def update_thread(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        mode: RunMode | None = None,
        model_override: str | None | object = _UNSET,
        thinking_override: str | None | object = _UNSET,
    ) -> ThreadViewRecord:
        record = self.load_thread(thread_id)
        if title is not None:
            if not title.strip():
                raise ValueError("title must not be blank")
            record.title = title.strip()
        if mode is not None:
            record.mode = mode
        if model_override is not _UNSET:
            record.model_override = normalize_model(model_override) if model_override else None
        if thinking_override is not _UNSET:
            record.thinking_override = thinking_override
        self._touch_thread(record)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=record.thread_id,
            event_type="thread.updated",
            payload={
                "title": record.title,
                "mode": record.mode.value,
                "model_override": record.model_override,
                "thinking_override": record.thinking_override,
            },
        )
        return self._resolve_thread(record)

    def archive_thread(self, thread_id: str) -> ThreadViewRecord:
        record = self.load_thread(thread_id)
        record.archived_at = self._now()
        self._touch_thread(record)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=thread_id,
            event_type="thread.archived",
            payload={"archived_at": record.archived_at},
        )
        return self._resolve_thread(record)

    def restore_thread(self, thread_id: str) -> ThreadViewRecord:
        record = self.load_thread(thread_id)
        record.archived_at = None
        self._touch_thread(record)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=thread_id,
            event_type="thread.restored",
            payload={"restored_at": record.updated_at},
        )
        return self._resolve_thread(record)

    def delete_thread(self, thread_id: str) -> None:
        self.load_thread(thread_id)
        thread_path = self._thread_path(thread_id)
        if thread_path.exists():
            thread_path.unlink()

        event_path = self._event_log_path(thread_id)
        if event_path.exists():
            event_path.unlink()

        upload_dir = self.uploads_dir / thread_id
        if upload_dir.exists():
            for path in sorted(upload_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            upload_dir.rmdir()

    def save_thread(self, record: ThreadRecord) -> None:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            record.workspace_path = boundary.normalize(record.workspace_path)
            boundary.resolve_relative(record.workspace_path, must_exist=True, kind="directory")
            if record.project_id is None:
                raise WorkspaceInputError()
            self._validate_thread_project_workspace(
                record,
                self.load_project(record.project_id),
            )
        if not record.created_at:
            record.created_at = self._now()
        if not record.updated_at:
            record.updated_at = record.created_at
        self._atomic_write_json(self._thread_path(record.thread_id), record.model_dump())

    def _ensure_thread_project(self, record: ThreadRecord) -> ThreadRecord:
        if record.project_id:
            try:
                project = self.load_project(record.project_id)
            except ProjectNotFoundError:
                imported_project = self.ensure_imported_project()
                if imported_project.defaults_origin is ProjectDefaultsOrigin.FALLBACK:
                    if record.model_override is None:
                        record.model_override = DEFAULT_MODEL
                    if record.thinking_override is None:
                        record.thinking_override = DEFAULT_THINKING_LEVEL
                record.project_id = imported_project.project_id
                self.save_thread(record)
                project = imported_project
            self._validate_thread_project_workspace(record, project)
            return record

        imported_project = self.ensure_imported_project()
        if (
            imported_project.defaults_origin is ProjectDefaultsOrigin.FALLBACK
            or imported_project.default_model != DEFAULT_MODEL
            or imported_project.default_thinking_level != DEFAULT_THINKING_LEVEL
        ):
            if record.model_override is None:
                record.model_override = DEFAULT_MODEL
            if record.thinking_override is None:
                record.thinking_override = DEFAULT_THINKING_LEVEL
        record.project_id = imported_project.project_id
        self.save_thread(record)
        self._validate_thread_project_workspace(record, imported_project)
        return record

    def _ensure_thread_model(self, record: ThreadRecord) -> ThreadRecord:
        if record.model_override:
            normalized_model = normalize_model(record.model_override)
            if normalized_model != record.model_override:
                record.model_override = None
                self.save_thread(record)
        return record

    def _resolve_thread(self, record: ThreadRecord) -> ThreadViewRecord:
        project = self.load_project(record.project_id or self.ensure_imported_project().project_id)
        self._validate_thread_project_workspace(record, project)
        effective_model = normalize_model(record.model_override or project.default_model)
        effective_thinking_level = record.thinking_override or project.default_thinking_level
        return ThreadViewRecord(
            **record.model_dump(),
            project_name=project.name,
            project_root_path=project.root_path,
            project_kind=project.kind,
            default_model=project.default_model,
            default_thinking_level=project.default_thinking_level,
            effective_model=effective_model,
            effective_thinking_level=effective_thinking_level,
        )

    def append_thread_event(
        self,
        *,
        thread_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> ThreadEventRecord:
        with self._event_lock:
            sequence = self._next_thread_event_sequence(thread_id)
            record = ThreadEventRecord(
                event_id=f"evt_{uuid4().hex[:12]}",
                thread_id=thread_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                timestamp=self._now(),
            )
            target = self._event_log_path(thread_id)
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record.model_dump()))
                stream.write("\n")
            return record

    def list_thread_events(self, thread_id: str, *, after: int | None = None) -> list[ThreadEventRecord]:
        target = self._event_log_path(thread_id)
        if not target.exists():
            return []

        events: list[ThreadEventRecord] = []
        with target.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = ThreadEventRecord.model_validate_json(line)
                if after is None or event.sequence > after:
                    events.append(event)
        return events

    def _next_thread_event_sequence(self, thread_id: str) -> int:
        target = self._event_log_path(thread_id)
        if not target.exists():
            return 1

        latest_sequence = 0
        with target.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = ThreadEventRecord.model_validate_json(line)
                latest_sequence = max(latest_sequence, event.sequence)
        return latest_sequence + 1

    def get_attachment(self, thread_id: str, attachment_id: str) -> AttachmentRecord:
        record = self.load_thread(thread_id)
        for attachment in record.attachments:
            if attachment.attachment_id == attachment_id:
                return attachment
        raise ThreadNotFoundError(attachment_id)

    def attach_file(
        self,
        *,
        thread_id: str,
        filename: str,
        mime_type: str,
        content: bytes | BinaryIO,
        relative_path: str | None = None,
    ) -> AttachmentRecord:
        record = self.load_thread(thread_id)
        safe_name = Path(filename).name.strip()
        if not safe_name:
            raise ValueError("filename must not be blank")

        thread_upload_dir = self.uploads_dir / thread_id
        thread_upload_dir.mkdir(parents=True, exist_ok=True)
        relative_target = self._sanitize_relative_path(relative_path, safe_name)
        target = thread_upload_dir / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{target.stem}-{uuid4().hex[:8]}{target.suffix}")

        size_bytes = 0
        with target.open("wb") as handle:
            if hasattr(content, "read"):
                while True:
                    chunk = content.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    size_bytes += len(chunk)
            else:
                handle.write(content)
                size_bytes = len(content)

        attachment = AttachmentRecord(
            attachment_id=f"att_{uuid4().hex[:12]}",
            filename=target.name,
            mime_type=mime_type,
            stored_path=str(target),
            relative_path=str(target.relative_to(thread_upload_dir)).replace("\\", "/"),
            size_bytes=size_bytes,
        )
        record.attachments.append(attachment)
        self._touch_thread(record)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=thread_id,
            event_type="attachment.added",
            payload={
                "attachment_id": attachment.attachment_id,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "stored_path": attachment.stored_path,
                "relative_path": attachment.relative_path,
                "size_bytes": attachment.size_bytes,
            },
        )
        return attachment

    def get_artifact(self, thread_id: str, artifact_id: str) -> ArtifactRecord:
        record = self.load_thread(thread_id)
        for artifact in record.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        raise ThreadNotFoundError(artifact_id)

    def sync_thread_artifacts(self, thread_id: str) -> list[ArtifactRecord]:
        record = self.load_thread(thread_id)
        workspace_path = Path(record.workspace_path)
        known_by_path = {artifact.stored_path: artifact for artifact in record.artifacts}
        added_any = False

        for target in sorted(path for path in workspace_path.rglob("*") if path.is_file()):
            stored_path = str(target)
            if stored_path in known_by_path:
                continue

            artifact = ArtifactRecord(
                artifact_id=f"art_{uuid4().hex[:12]}",
                filename=target.name,
                mime_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                stored_path=stored_path,
                relative_path=str(target.relative_to(workspace_path)).replace("\\", "/"),
                size_bytes=target.stat().st_size,
            )
            record.artifacts.append(artifact)
            self.append_thread_event(
                thread_id=thread_id,
                event_type="artifact.added",
                payload={
                    "artifact_id": artifact.artifact_id,
                    "filename": artifact.filename,
                    "mime_type": artifact.mime_type,
                    "stored_path": artifact.stored_path,
                    "relative_path": artifact.relative_path,
                    "size_bytes": artifact.size_bytes,
                },
            )
            added_any = True

        if added_any:
            self._touch_thread(record)
        self.save_thread(record)
        return record.artifacts

    def create_workspace_archive(self, thread_id: str) -> ArtifactRecord:
        record = self.load_thread(thread_id)
        target_dir = self.artifacts_dir / thread_id
        target_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        title_stem = "".join(
            char.lower() if char.isalnum() else "-"
            for char in (record.title or record.workspace_id)
        ).strip("-")
        filename = f"{title_stem or record.workspace_id}-{timestamp}.zip"
        target = target_dir / filename

        workspace_root = Path(record.workspace_path)
        uploads_root = self.uploads_dir / thread_id
        included_files = 0

        with zipfile.ZipFile(
            target,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            if workspace_root.exists():
                for path in sorted(candidate for candidate in workspace_root.rglob("*") if candidate.is_file()):
                    archive.write(path, arcname=str(Path("workspace") / path.relative_to(workspace_root)))
                    included_files += 1

            if uploads_root.exists():
                for path in sorted(candidate for candidate in uploads_root.rglob("*") if candidate.is_file()):
                    archive.write(path, arcname=str(Path("uploads") / path.relative_to(uploads_root)))
                    included_files += 1

            if included_files == 0:
                archive.writestr(
                    "README.txt",
                    "This chat did not have any workspace files or uploaded files yet.\n",
                )

        artifact = ArtifactRecord(
            artifact_id=f"art_{uuid4().hex[:12]}",
            filename=filename,
            mime_type="application/zip",
            stored_path=str(target),
            relative_path=filename,
            size_bytes=target.stat().st_size,
        )
        record.artifacts.append(artifact)
        self._touch_thread(record)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=thread_id,
            event_type="artifact.added",
            payload={
                "artifact_id": artifact.artifact_id,
                "filename": artifact.filename,
                "mime_type": artifact.mime_type,
                "stored_path": artifact.stored_path,
                "relative_path": artifact.relative_path,
                "size_bytes": artifact.size_bytes,
                "source": "workspace_archive",
            },
        )
        return artifact

    def _sanitize_relative_path(self, relative_path: str | None, filename: str) -> Path:
        if not relative_path:
            return Path(filename)

        candidate = PurePosixPath(relative_path.replace("\\", "/"))
        if candidate.is_absolute():
            raise ValueError("relative_path must be relative")
        parts = [part for part in candidate.parts if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("relative_path must stay inside the upload root")
        return Path(*parts)

    def get_limits_status(self, *, refresh: bool = False) -> LimitsStatusRecord:
        if not self.limits_status_path.exists():
            status = LimitsStatusRecord()
        else:
            status = LimitsStatusRecord.model_validate_json(
                self.limits_status_path.read_text(encoding="utf-8")
            )

        if refresh and self.limits_probe is not None:
            snapshot = self.limits_probe.probe()
            if snapshot is not None:
                merged = self._merge_limits_status(status, snapshot)
                if merged.model_dump() != status.model_dump():
                    self.save_limits_status(merged)
                return merged
        return status

    def save_limits_status(self, status: LimitsStatusRecord) -> None:
        self._atomic_write_json(self.limits_status_path, status.model_dump())

    def update_limits_from_rate_data(self, rate_limits: dict[str, object]) -> LimitsStatusRecord:
        status = LimitsStatusRecord(
            available=True,
            blocked=False,
            message=None,
            primary=self._limits_window(rate_limits.get("primary")),
            secondary=self._limits_window(rate_limits.get("secondary")),
            credits=rate_limits.get("credits") if isinstance(rate_limits.get("credits"), dict) else None,
            plan_type=str(rate_limits.get("plan_type")) if rate_limits.get("plan_type") is not None else None,
            updated_at=self._now(),
        )
        self.save_limits_status(status)
        return status

    def mark_limits_blocked(self, message: str) -> LimitsStatusRecord:
        status = self.get_limits_status()
        status.available = True
        status.blocked = True
        status.message = message
        status.updated_at = self._now()
        self.save_limits_status(status)
        return status

    def clear_limits_blocked(self) -> LimitsStatusRecord:
        status = self.get_limits_status()
        if status.blocked or status.message:
            status.blocked = False
            status.message = None
            status.updated_at = self._now()
            self.save_limits_status(status)
        return status

    def _merge_limits_status(
        self,
        current: LimitsStatusRecord,
        snapshot: LimitsStatusRecord,
    ) -> LimitsStatusRecord:
        snapshot.blocked = snapshot.blocked or current.blocked
        if current.blocked and current.message:
            snapshot.message = current.message
        if current.updated_at and (snapshot.updated_at is None or current.updated_at > snapshot.updated_at):
            snapshot.updated_at = current.updated_at
        return snapshot

    def _limits_window(self, payload: object) -> LimitsWindowRecord | None:
        if not isinstance(payload, dict):
            return None

        used = payload.get("used_percent")
        used_percent = float(used) if isinstance(used, (int, float)) else None
        remaining_percent = None
        if used_percent is not None:
            remaining_percent = max(0.0, min(100.0, 100.0 - used_percent))

        window_minutes = payload.get("window_minutes")
        resets_at = payload.get("resets_at")
        return LimitsWindowRecord(
            used_percent=used_percent,
            remaining_percent=remaining_percent,
            window_minutes=int(window_minutes) if isinstance(window_minutes, (int, float)) else None,
            resets_at=int(resets_at) if isinstance(resets_at, (int, float)) else None,
        )
