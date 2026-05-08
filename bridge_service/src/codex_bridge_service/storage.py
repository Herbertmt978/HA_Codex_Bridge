import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import ArtifactRecord, AttachmentRecord, RunMode, ThreadEventRecord, ThreadRecord


class ThreadNotFoundError(FileNotFoundError):
    pass


class BridgeStorage:
    def __init__(self, root_path: Path | str) -> None:
        self.root = Path(root_path)
        self.threads_dir = self.root / "threads"
        self.workspaces_dir = self.root / "workspaces"
        self.uploads_dir = self.root / "uploads"
        self.artifacts_dir = self.root / "artifacts"
        self.logs_dir = self.root / "logs"

        for directory in (
            self.threads_dir,
            self.workspaces_dir,
            self.uploads_dir,
            self.artifacts_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _thread_path(self, thread_id: str) -> Path:
        return self.threads_dir / f"{thread_id}.json"

    def _event_log_path(self, thread_id: str) -> Path:
        return self.logs_dir / f"{thread_id}.events.jsonl"

    def load_thread(self, thread_id: str) -> ThreadRecord:
        target = self._thread_path(thread_id)
        if not target.exists():
            raise ThreadNotFoundError(thread_id)
        return ThreadRecord.model_validate_json(target.read_text(encoding="utf-8"))

    def list_threads(self) -> list[ThreadRecord]:
        records = [
            ThreadRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.threads_dir.glob("*.json")
        ]
        return sorted(
            records,
            key=lambda record: self._thread_path(record.thread_id).stat().st_mtime,
            reverse=True,
        )

    def create_thread(self, title: str, mode: RunMode) -> ThreadRecord:
        if not title.strip():
            raise ValueError("title must not be blank")

        thread_id = f"thr_{uuid4().hex[:12]}"
        workspace_id = f"ws_{uuid4().hex[:12]}"
        workspace_path = self.workspaces_dir / workspace_id
        workspace_path.mkdir(parents=True, exist_ok=True)

        record = ThreadRecord(
            thread_id=thread_id,
            title=title,
            workspace_id=workspace_id,
            workspace_path=str(workspace_path),
            status="idle",
            mode=mode,
        )
        self.save_thread(record)
        self.append_thread_event(
            thread_id=record.thread_id,
            event_type="thread.created",
            payload={
                "title": record.title,
                "workspace_id": record.workspace_id,
                "workspace_path": record.workspace_path,
                "mode": record.mode.value,
            },
        )
        return record

    def save_thread(self, record: ThreadRecord) -> None:
        target = self._thread_path(record.thread_id)
        temp_target = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temp_target.write_text(
            json.dumps(record.model_dump(), indent=2),
            encoding="utf-8",
        )
        temp_target.replace(target)

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
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
        content: bytes,
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

        target.write_bytes(content)

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
        new_artifacts: list[ArtifactRecord] = []

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
            new_artifacts.append(artifact)
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
