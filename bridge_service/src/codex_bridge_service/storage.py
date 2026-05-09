import json
import mimetypes
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from .models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    ArtifactRecord,
    AttachmentRecord,
    LimitsStatusRecord,
    LimitsWindowRecord,
    PathBrowseEntryRecord,
    PathBrowseRecord,
    ProjectRecord,
    RunMode,
    ThreadEventRecord,
    ThreadRecord,
    ThreadViewRecord,
)

_UNSET = object()


class ThreadNotFoundError(FileNotFoundError):
    pass


class ProjectNotFoundError(FileNotFoundError):
    pass


class BridgeStorage:
    imported_project_name = "Imported Threads"

    def __init__(self, root_path: Path | str) -> None:
        self.root = Path(root_path)
        self.projects_dir = self.root / "projects"
        self.threads_dir = self.root / "threads"
        self.workspaces_dir = self.root / "workspaces"
        self.uploads_dir = self.root / "uploads"
        self.artifacts_dir = self.root / "artifacts"
        self.logs_dir = self.root / "logs"
        self.limits_status_path = self.root / "limits_status.json"

        for directory in (
            self.projects_dir,
            self.threads_dir,
            self.workspaces_dir,
            self.uploads_dir,
            self.artifacts_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

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

    def _imported_project_id(self) -> str:
        return "prj_imported"

    def ensure_imported_project(self) -> ProjectRecord:
        target = self._project_path(self._imported_project_id())
        if target.exists():
            return ProjectRecord.model_validate_json(target.read_text(encoding="utf-8"))

        record = ProjectRecord(
            project_id=self._imported_project_id(),
            name=self.imported_project_name,
            root_path=str(self.workspaces_dir),
            default_model=DEFAULT_MODEL,
            default_thinking_level=DEFAULT_THINKING_LEVEL,
            created_at=self._now(),
            updated_at=self._now(),
        )
        self.save_project(record)
        return record

    def load_project(self, project_id: str) -> ProjectRecord:
        target = self._project_path(project_id)
        if not target.exists():
            raise ProjectNotFoundError(project_id)
        return ProjectRecord.model_validate_json(target.read_text(encoding="utf-8"))

    def list_projects(self) -> list[ProjectRecord]:
        records = [
            ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.projects_dir.glob("*.json")
        ]
        if not records and any(self.threads_dir.glob("*.json")):
            records.append(self.ensure_imported_project())
        return sorted(
            records,
            key=lambda record: self._project_path(record.project_id).stat().st_mtime,
            reverse=True,
        )

    def create_project(
        self,
        *,
        name: str,
        root_path: str,
        default_model: str = DEFAULT_MODEL,
        default_thinking_level: str = DEFAULT_THINKING_LEVEL,
    ) -> ProjectRecord:
        if not name.strip():
            raise ValueError("name must not be blank")
        if not root_path.strip():
            raise ValueError("root_path must not be blank")

        project_root = self._normalize_root_path(root_path)
        project_root.mkdir(parents=True, exist_ok=True)
        record = ProjectRecord(
            project_id=f"prj_{uuid4().hex[:12]}",
            name=name.strip(),
            root_path=str(project_root),
            default_model=default_model or DEFAULT_MODEL,
            default_thinking_level=default_thinking_level or DEFAULT_THINKING_LEVEL,
            created_at=self._now(),
            updated_at=self._now(),
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
        record = self.load_project(project_id)
        if name is not None:
            if not name.strip():
                raise ValueError("name must not be blank")
            record.name = name.strip()
        if root_path is not None:
            normalized = self._normalize_root_path(root_path)
            normalized.mkdir(parents=True, exist_ok=True)
            record.root_path = str(normalized)
        if default_model is not None:
            record.default_model = default_model or DEFAULT_MODEL
        if default_thinking_level is not None:
            record.default_thinking_level = default_thinking_level or DEFAULT_THINKING_LEVEL
        record.updated_at = self._now()
        self.save_project(record)
        return record

    def save_project(self, record: ProjectRecord) -> None:
        self._atomic_write_json(self._project_path(record.project_id), record.model_dump())

    def browse_paths(self, path: str | None = None) -> PathBrowseRecord:
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
        return self._ensure_thread_project(record)

    def get_thread(self, thread_id: str) -> ThreadViewRecord:
        return self._resolve_thread(self.load_thread(thread_id))

    def list_threads(self) -> list[ThreadViewRecord]:
        records = [
            self._ensure_thread_project(
                ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            )
            for path in self.threads_dir.glob("*.json")
        ]
        resolved = [self._resolve_thread(record) for record in records]
        return sorted(
            resolved,
            key=lambda record: self._thread_path(record.thread_id).stat().st_mtime,
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
    ) -> ThreadViewRecord:
        if not title.strip():
            raise ValueError("title must not be blank")

        project = self.ensure_imported_project() if project_id is None else self.load_project(project_id)
        workspace_id = f"ws_{uuid4().hex[:12]}"
        workspace_path = Path(project.root_path)
        workspace_path.mkdir(parents=True, exist_ok=True)

        record = ThreadRecord(
            thread_id=f"thr_{uuid4().hex[:12]}",
            project_id=project.project_id,
            title=title.strip(),
            workspace_id=workspace_id,
            workspace_path=str(workspace_path),
            status="idle",
            mode=mode,
            model_override=model_override,
            thinking_override=thinking_override,
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
            record.model_override = model_override
        if thinking_override is not _UNSET:
            record.thinking_override = thinking_override
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

    def save_thread(self, record: ThreadRecord) -> None:
        self._atomic_write_json(self._thread_path(record.thread_id), record.model_dump())

    def _ensure_thread_project(self, record: ThreadRecord) -> ThreadRecord:
        if record.project_id:
            try:
                self.load_project(record.project_id)
            except ProjectNotFoundError:
                record.project_id = self.ensure_imported_project().project_id
                self.save_thread(record)
            return record

        record.project_id = self.ensure_imported_project().project_id
        self.save_thread(record)
        return record

    def _resolve_thread(self, record: ThreadRecord) -> ThreadViewRecord:
        project = self.load_project(record.project_id or self.ensure_imported_project().project_id)
        effective_model = record.model_override or project.default_model
        effective_thinking_level = record.thinking_override or project.default_thinking_level
        return ThreadViewRecord(
            **record.model_dump(),
            project_name=project.name,
            project_root_path=project.root_path,
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
        sequence = len(self.list_thread_events(thread_id)) + 1
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

        events = [
            ThreadEventRecord.model_validate_json(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if after is None:
            return events
        return [event for event in events if event.sequence > after]

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
    ) -> AttachmentRecord:
        record = self.load_thread(thread_id)
        safe_name = Path(filename).name.strip()
        if not safe_name:
            raise ValueError("filename must not be blank")

        thread_upload_dir = self.uploads_dir / thread_id
        thread_upload_dir.mkdir(parents=True, exist_ok=True)
        target = thread_upload_dir / safe_name
        if target.exists():
            target = thread_upload_dir / f"{target.stem}-{uuid4().hex[:8]}{target.suffix}"

        with target.open("wb") as handle:
            if hasattr(content, "read"):
                while True:
                    chunk = content.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            else:
                handle.write(content)

        attachment = AttachmentRecord(
            attachment_id=f"att_{uuid4().hex[:12]}",
            filename=target.name,
            mime_type=mime_type,
            stored_path=str(target),
        )
        record.attachments.append(attachment)
        self.save_thread(record)
        self.append_thread_event(
            thread_id=thread_id,
            event_type="attachment.added",
            payload={
                "attachment_id": attachment.attachment_id,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "stored_path": attachment.stored_path,
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

        for target in sorted(path for path in workspace_path.rglob("*") if path.is_file()):
            stored_path = str(target)
            if stored_path in known_by_path:
                continue

            artifact = ArtifactRecord(
                artifact_id=f"art_{uuid4().hex[:12]}",
                filename=target.name,
                mime_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                stored_path=stored_path,
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
                },
            )

        self.save_thread(record)
        return record.artifacts

    def get_limits_status(self) -> LimitsStatusRecord:
        if not self.limits_status_path.exists():
            return LimitsStatusRecord()
        return LimitsStatusRecord.model_validate_json(self.limits_status_path.read_text(encoding="utf-8"))

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
