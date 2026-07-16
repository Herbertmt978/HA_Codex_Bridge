import json
import io
import base64
import hashlib
import mimetypes
import os
import re
import string
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock, RLock
from typing import BinaryIO, Callable, Literal
from uuid import uuid4
import zipfile
from contextlib import contextmanager, nullcontext
from collections.abc import Iterator, Mapping

from .codex_auth import AUTH_EXPIRED_MESSAGE
from .event_store import BridgeEventStore, DurableOutbox, EventDraft, OutboxWrite
from .limits import CodexLimitsProbe
from .models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    ArtifactRecord,
    ArtifactSource,
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
from .resource_limits import (
    QuotaExceededError,
    QuotaManager,
    QuotaPool,
    QuotaReservation,
    ReservationConflictError,
    ResourceLimits,
    StreamingByteCounter,
    archive_container_detected,
    open_inspected_archive,
)
from .workspace import (
    WorkspaceAnonymousFileLease,
    WorkspaceBoundary,
    WorkspaceBoundaryError,
    WorkspaceEscapeError,
    WorkspaceExistsError,
    WorkspaceFileIdentity,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspaceResourceLimitError,
    normalize_portable_relative_path,
)

_UNSET = object()
_WORKSPACE_ID_PATTERN = re.compile(r"^ws_[0-9a-f]{12}$")
_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UPLOAD_MANIFEST_MAX_BYTES = 64 * 1024
_UPLOAD_MAX_FILENAME_BYTES = 255
_UPLOAD_MAX_RELATIVE_PATH_BYTES = 2048
_UPLOAD_MAX_RELATIVE_PATH_DEPTH = 16
_UPLOAD_MAX_MIME_TYPE_BYTES = 255
_UPLOAD_TERMINAL_SESSION_LIMIT = 128
_UPLOAD_SESSION_LIMIT = 256
# This is intentionally independent of the per-archive entry limit: an
# aggregate workspace measurement spans many chats, but must still be bounded.
_WORKSPACE_AGGREGATE_SCAN_MAX_ENTRIES = 100_000
_UPLOAD_SESSION_FIELDS = frozenset(
    {
        "upload_id",
        "thread_id",
        "filename",
        "mime_type",
        "relative_path",
        "size_bytes",
        "sha256",
        "received",
        "status",
    }
)
_GENERATED_IMAGE_MAX_BYTES = 25 * 1024 * 1024
_GENERATED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_GENERATED_IMAGE_MAGIC = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}


def _decode_generated_image(
    result: object,
    declared_mime_type: object = None,
) -> tuple[str, bytes]:
    """Decode one bounded Codex image result and verify its container signature."""
    if not isinstance(result, str) or not result:
        raise WorkspaceInputError()
    mime_type: str | None = None
    encoded = result
    if result.startswith("data:"):
        header, separator, encoded = result.partition(",")
        if separator != "," or not header.startswith("data:"):
            raise WorkspaceInputError()
        prefix = header[5:]
        if not prefix.endswith(";base64"):
            raise WorkspaceInputError()
        mime_type = prefix[:-7]
    if mime_type is None:
        if isinstance(declared_mime_type, str):
            mime_type = declared_mime_type.split(";", 1)[0].strip().lower()
    else:
        mime_type = mime_type.lower()
        if isinstance(declared_mime_type, str):
            declared = declared_mime_type.split(";", 1)[0].strip().lower()
            if declared and declared != mime_type:
                raise WorkspaceInputError()
    if not isinstance(encoded, str) or not encoded or len(encoded) > (
        (_GENERATED_IMAGE_MAX_BYTES + 2) * 4 // 3 + 8
    ):
        raise WorkspaceInputError()
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, base64.binascii.Error):
        raise WorkspaceInputError() from None
    if not raw or len(raw) > _GENERATED_IMAGE_MAX_BYTES:
        raise WorkspaceInputError()
    detected_mime = None
    if raw.startswith(_GENERATED_IMAGE_MAGIC["image/png"]):
        detected_mime = "image/png"
    elif raw.startswith(_GENERATED_IMAGE_MAGIC["image/jpeg"]):
        detected_mime = "image/jpeg"
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        detected_mime = "image/webp"
    if detected_mime is None or (mime_type is not None and mime_type != detected_mime):
        raise WorkspaceInputError()
    mime_type = detected_mime
    if mime_type not in _GENERATED_IMAGE_MIME_TYPES:
        raise WorkspaceInputError()
    return mime_type, raw


def _write_all(output: BinaryIO, content: bytes | bytearray | memoryview) -> int:
    view = memoryview(content)
    total = len(view)
    while view:
        written = output.write(view)
        if not isinstance(written, int) or written <= 0:
            raise OSError("storage write failed")
        view = view[written:]
    return total


class _QuotaSequentialWriter:
    """Make ZipFile use data descriptors while reserving each appended byte."""

    def __init__(self, output: BinaryIO, reservation: QuotaReservation) -> None:
        self.output = output
        self.reservation = reservation

    def write(self, content: bytes | bytearray | memoryview) -> int:
        view = memoryview(content)
        total = len(view)
        self.reservation.consume(total)
        return _write_all(self.output, view)

    def tell(self) -> int:
        return self.output.tell()

    def seek(self, *_args: object) -> int:
        raise io.UnsupportedOperation("sequential archive output")

    def seekable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        self.output.flush()


class _ReleasingBinaryStream:
    def __init__(
        self,
        stream: BinaryIO,
        release: Callable[[], None] | None,
    ) -> None:
        self._stream = stream
        self._release = release

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def __enter__(self) -> "_ReleasingBinaryStream":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def fileno(self) -> int:
        return self._stream.fileno()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def close(self) -> None:
        release = self._release
        self._release = None
        try:
            self._stream.close()
        finally:
            if release is not None:
                release()


class ThreadNotFoundError(FileNotFoundError):
    pass


class ProjectNotFoundError(FileNotFoundError):
    pass


class ProjectMutationError(ValueError):
    pass


class UploadNotFoundError(FileNotFoundError):
    pass


class UploadConflictError(ValueError):
    pass


class UploadValidationError(ValueError):
    pass


class _UploadChunkWriter:
    """One descriptor-rooted chunk write; owns the upload lock until finished."""

    def __init__(
        self,
        storage: "BridgeStorage",
        payload: dict[str, object],
        index: int,
        digest: str,
        expected: int,
        reservation: QuotaReservation,
    ) -> None:
        self.storage, self.payload, self.index, self.digest, self.expected = (
            storage,
            payload,
            index,
            digest,
            expected,
        )
        self.reservation = reservation
        self.boundary = storage._home_assistant_uploads_boundary()
        self.part = f"{storage._upload_payload_dir(str(payload['upload_id']))}/{index}.{uuid4().hex}.part"
        self.chunk = (
            f"{storage._upload_payload_dir(str(payload['upload_id']))}/{index}.chunk"
        )
        self.output = self.boundary.create_file_exclusive(self.part)
        self.identity = self.boundary.identify_open_file(self.output)
        storage._active_upload_parts.add(self.part)
        self.counter = 0
        self.hasher = hashlib.sha256()
        self.chunk_published = False
        self.closed = False

    def write(self, block: bytes) -> None:
        if (
            self.closed
            or not isinstance(block, bytes)
            or self.counter + len(block) > self.expected
        ):
            raise UploadValidationError("content_length")
        self.reservation.consume(len(block))
        _write_all(self.output, block)
        self.hasher.update(block)
        self.counter += len(block)

    def finish(self) -> dict[str, object]:
        self.storage._upload_mutation_lock.acquire()
        try:
            self.output.flush()
            os.fsync(self.output.fileno())
            self.output.close()
            if self.counter != self.expected or self.hasher.hexdigest() != self.digest:
                raise UploadValidationError("chunk_sha256")
            current = self.storage._read_upload_session_locked(
                str(self.payload["upload_id"])
            )
            if current["status"] != "active":
                raise UploadConflictError("upload is not active")
            received = current["received"]
            assert isinstance(received, dict)
            if str(self.index) in received or {int(value) for value in received} != set(
                range(self.index)
            ):
                raise UploadConflictError("chunk order")
            self.boundary.replace_regular_file(
                self.part, self.chunk, expected_identity=self.identity
            )
            self.chunk_published = True
            received[str(self.index)] = {
                "sha256": self.digest,
                "size_bytes": self.counter,
            }
            self.storage._write_upload_session_locked(current)
            self.reservation.commit(persisted_bytes=self.counter)
            return self.storage._upload_view(current)
        except BaseException:
            self._rollback_finish_failure()
            raise
        finally:
            self._release_lock()

    def abort(self) -> None:
        with self.storage._upload_mutation_lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.output.close()
            finally:
                try:
                    self.boundary.unlink_regular_file(
                        self.part,
                        missing_ok=True,
                        expected_identity=self.identity,
                    )
                finally:
                    self.storage._active_upload_parts.discard(self.part)
                    if self.reservation.active:
                        self.reservation.release()

    def _rollback_finish_failure(self) -> None:
        """Undo a pre-manifest publish without unlinking a durable retry."""
        try:
            manifest_persisted = False
            if self.chunk_published:
                try:
                    current = self.storage._read_upload_session_locked(
                        str(self.payload["upload_id"])
                    )
                    received = current["received"]
                    assert isinstance(received, dict)
                    metadata = received.get(str(self.index))
                    manifest_persisted = (
                        isinstance(metadata, dict)
                        and metadata.get("sha256") == self.digest
                        and metadata.get("size_bytes") == self.counter
                    )
                except (UploadNotFoundError, WorkspaceBoundaryError):
                    manifest_persisted = False
                if manifest_persisted:
                    # An atomic manifest operation may report an error after
                    # its durable replace.  Retain its exact chunk for the
                    # idempotent retry and settle (or release) the transient
                    # reservation without creating an accounting leak.
                    if self.reservation.active:
                        try:
                            self.reservation.commit(persisted_bytes=self.counter)
                        except BaseException:
                            if self.reservation.active:
                                self.reservation.release()
                else:
                    self.boundary.unlink_regular_file(
                        self.chunk,
                        missing_ok=True,
                        expected_identity=self.identity,
                    )
            else:
                self.boundary.unlink_regular_file(
                    self.part,
                    missing_ok=True,
                    expected_identity=self.identity,
                )
        finally:
            if self.reservation.active:
                self.reservation.release()

    def _release_lock(self) -> None:
        if not self.closed:
            self.closed = True
        self.storage._active_upload_parts.discard(self.part)
        try:
            self.storage._upload_mutation_lock.release()
        except RuntimeError:
            pass


class BridgeStorage:
    imported_project_name = "Imported Threads"
    direct_project_name = "Direct chats"

    def __init__(
        self,
        root_path: Path | str,
        *,
        limits_probe: CodexLimitsProbe | None = None,
        special_project_defaults_provider: Callable[[], tuple[str, str, bool]]
        | None = None,
        runtime_profile: RuntimeProfile | str = RuntimeProfile.EXTERNAL_LEGACY,
        workspace_root: Path | str | None = None,
        resource_limits: ResourceLimits | None = None,
        event_store: BridgeEventStore | None = None,
        durable_outbox: DurableOutbox | None = None,
        outbox_failure_injector: Callable[[str], None] | None = None,
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
        self.uploads_boundary: WorkspaceBoundary | None = None
        self.artifacts_boundary: WorkspaceBoundary | None = None
        self.workspace_root: Path | None = None
        self.resource_limits: ResourceLimits | None = None
        self.quota_manager: QuotaManager | None = None
        self.transient_quota_manager: QuotaManager | None = None
        self.limits_status_path = self.root / "limits_status.json"
        self.limits_probe = limits_probe
        self.special_project_defaults_provider = special_project_defaults_provider
        self._special_default_migration_enabled = False
        self._special_default_migration_pending = False
        self._special_migration_lock = Lock()
        self._event_lock = Lock()
        self._event_next_sequences: dict[str, int] = {}
        self._project_mutation_lock = RLock()
        self._thread_mutation_lock = RLock()
        # Automation dispatch reserves its target across thread preparation and
        # runtime submission. Archive/delete take this lock first and reject a
        # reserved target instead of interleaving with an accepted prompt.
        self._automation_target_lock = RLock()
        self._automation_project_reservations: dict[str, int] = {}
        self._automation_thread_reservations: dict[str, int] = {}
        # A single process lock deliberately precedes the thread lock in every
        # upload transition and deletion, avoiding complete/cancel/delete ABBA.
        self._upload_mutation_lock = RLock()
        # Open request streams release the mutation lock while bytes arrive.
        # Reaping consults these exact descriptor-rooted locators so a live
        # writer is never mistaken for debris; a restarted process begins
        # empty and can therefore reclaim genuinely abandoned parts.
        self._active_upload_parts: set[str] = set()

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
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT and os.name != "nt":
            private_state_boundary = WorkspaceBoundary(self.root, create=True)
            try:
                for directory in private_directories:
                    private_state_boundary.create_directory(directory.name)
            finally:
                private_state_boundary.close()
        else:
            # Windows is validation-only for the HA profile; production HA
            # runs on Linux and takes the descriptor-rooted branch above.
            for directory in private_directories:
                directory.mkdir(parents=True, exist_ok=True)

        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            # Retain a descriptor for the private upload root before handling
            # any untrusted attachment or archive locator.
            self.uploads_boundary = WorkspaceBoundary(self.uploads_dir)
            if os.name == "nt":
                # Windows is validation-only for the HA profile; production
                # upload mutations require POSIX descriptor-relative APIs.
                (self.uploads_dir / ".sessions").mkdir(exist_ok=True)
            else:
                self.uploads_boundary.create_directory(".sessions")
            self.artifacts_boundary = WorkspaceBoundary(self.artifacts_dir)
            self.resource_limits = resource_limits or ResourceLimits()
            limits = self.resource_limits
            self.quota_manager = QuotaManager(
                pools={
                    "workspace": QuotaPool(
                        limit_bytes=limits.max_workspace_bytes,
                        usage_bytes=self._workspace_usage_bytes,
                        free_bytes=self._workspace_free_bytes,
                        total_bytes=self._workspace_total_bytes,
                        filesystem_id=self._workspace_filesystem_id,
                    ),
                    "private": QuotaPool(
                        limit_bytes=limits.max_private_bytes,
                        usage_bytes=self._private_usage_bytes,
                        free_bytes=self._private_free_bytes,
                        total_bytes=self._private_total_bytes,
                        filesystem_id=self._private_filesystem_id,
                    ),
                },
                minimum_free_bytes=limits.minimum_free_bytes,
                minimum_free_fraction=limits.minimum_free_fraction,
                ledger_path=self.root / "quota.sqlite3",
            )
            self.transient_quota_manager = QuotaManager(
                pools={
                    "transient": QuotaPool(
                        limit_bytes=limits.max_transient_snapshot_bytes,
                        usage_bytes=lambda: 0,
                        free_bytes=lambda: limits.max_transient_snapshot_bytes,
                        total_bytes=lambda: limits.max_transient_snapshot_bytes,
                        filesystem_id=lambda: "transient-memory",
                    )
                },
                minimum_free_bytes=0,
                minimum_free_fraction=0,
            )

        if self.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY:
            self.workspaces_dir.mkdir(parents=True, exist_ok=True)
            self.project_workspaces_dir.mkdir(parents=True, exist_ok=True)

        if durable_outbox is not None:
            if outbox_failure_injector is not None:
                raise ValueError(
                    "an outbox failure injector requires storage-owned outbox setup"
                )
            resolved_event_store = event_store or durable_outbox.event_store
            if durable_outbox.event_store is not resolved_event_store:
                raise ValueError("event store and durable outbox do not match")
            self.event_store = resolved_event_store
            self.durable_outbox = durable_outbox
        else:
            limits = self.resource_limits or resource_limits or ResourceLimits()
            self.event_store = event_store or BridgeEventStore(
                self.root / "events.sqlite3",
                max_event_payload_bytes=limits.max_event_payload_bytes,
                max_events_per_thread=limits.max_events_per_thread,
                max_thread_event_bytes=limits.max_event_log_bytes,
                max_events_per_non_thread_scope=limits.max_events_per_thread,
                max_non_thread_event_bytes=limits.max_event_log_bytes,
                max_journal_bytes=limits.max_event_journal_bytes,
            )
            self.durable_outbox = DurableOutbox(
                self.event_store,
                state_root=self.root,
                failure_injector=outbox_failure_injector,
            )
        self._import_legacy_event_logs()
        self.durable_outbox.reconcile()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _project_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def _thread_path(self, thread_id: str) -> Path:
        return self.threads_dir / f"{thread_id}.json"

    def _event_log_path(self, thread_id: str) -> Path:
        return self.logs_dir / f"{thread_id}.events.jsonl"

    def _import_legacy_event_logs(self) -> None:
        suffix = ".events.jsonl"
        for path in sorted(
            self.logs_dir.glob(f"*{suffix}"), key=lambda item: item.name
        ):
            thread_id = path.name[: -len(suffix)]
            if not thread_id:
                continue
            self.event_store.import_legacy_jsonl(path, thread_id=thread_id)

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

    def _home_assistant_uploads_boundary(self) -> WorkspaceBoundary:
        boundary = self.uploads_boundary
        if boundary is None:
            raise RuntimeError("upload boundary is unavailable")
        return boundary

    def _home_assistant_artifacts_boundary(self) -> WorkspaceBoundary:
        boundary = self.artifacts_boundary
        if boundary is None:
            raise RuntimeError("artifact boundary is unavailable")
        return boundary

    def _resource_limits(self) -> ResourceLimits:
        if self.resource_limits is None:
            raise RuntimeError("resource limits are unavailable")
        return self.resource_limits

    def _disk_quota(self) -> QuotaManager:
        if self.quota_manager is None:
            raise RuntimeError("disk quota manager is unavailable")
        return self.quota_manager

    def _transient_quota(self) -> QuotaManager:
        if self.transient_quota_manager is None:
            raise RuntimeError("transient quota manager is unavailable")
        return self.transient_quota_manager

    @staticmethod
    def _boundary_usage_bytes(boundary: WorkspaceBoundary) -> int:
        try:
            return boundary.measure_regular_files(
                ".", reject_unsafe=False
            ).logical_bytes
        except WorkspaceBoundaryError:
            raise ReservationConflictError("filesystem_scan") from None

    @staticmethod
    def _boundary_space(boundary: WorkspaceBoundary):
        try:
            return boundary.filesystem_space()
        except WorkspaceBoundaryError:
            raise ReservationConflictError("filesystem_space") from None

    def _workspace_usage_bytes(self) -> int:
        return self._boundary_usage_bytes(self._home_assistant_boundary())

    def _private_usage_bytes(self) -> int:
        return self._boundary_usage_bytes(
            self._home_assistant_uploads_boundary()
        ) + self._boundary_usage_bytes(self._home_assistant_artifacts_boundary())

    def _workspace_free_bytes(self) -> int:
        return self._boundary_space(self._home_assistant_boundary()).free_bytes

    def _workspace_total_bytes(self) -> int:
        return self._boundary_space(self._home_assistant_boundary()).total_bytes

    def _workspace_filesystem_id(self) -> str:
        return self._boundary_space(self._home_assistant_boundary()).filesystem_id

    def _private_free_bytes(self) -> int:
        return self._boundary_space(self._home_assistant_uploads_boundary()).free_bytes

    def _private_total_bytes(self) -> int:
        return self._boundary_space(self._home_assistant_uploads_boundary()).total_bytes

    def _private_filesystem_id(self) -> str:
        return self._boundary_space(
            self._home_assistant_uploads_boundary()
        ).filesystem_id

    def reserve_workspace_mutation(self) -> QuotaReservation:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError("workspace mutation reservations require Home Assistant")
        return self._disk_quota().reserve(
            "workspace",
            conflict_key="codex-workspace-mutation",
        )

    def observe_workspace_growth(self, reservation: QuotaReservation) -> int:
        if reservation.pool != "workspace":
            raise ReservationConflictError("workspace")
        return self._disk_quota().observe_growth(reservation)

    def _lease_transient_snapshot(
        self,
        boundary: WorkspaceBoundary,
        stored_locator: str,
    ) -> WorkspaceAnonymousFileLease:
        file_stat = boundary.regular_file_stat(stored_locator)
        reservation = self._transient_quota().reserve(
            "transient",
            amount_bytes=file_stat.size_bytes,
            item_limit_bytes=self._resource_limits().max_transient_snapshot_bytes,
        )
        try:
            lease = boundary.copy_regular_file_to_anonymous_lease(
                stored_locator,
                max_bytes=file_stat.size_bytes,
            )
            if lease.size_bytes != file_stat.size_bytes:
                lease.close()
                raise WorkspaceEscapeError()
            lease.set_close_callback(reservation.release)
            return lease
        except BaseException:
            if reservation.active:
                reservation.release()
            raise

    def _validate_uploaded_archive_if_present(
        self,
        boundary: WorkspaceBoundary,
        output: BinaryIO,
        *,
        filename: str,
        mime_type: str,
    ) -> None:
        declared_archive = filename.casefold().endswith(".zip") or (
            mime_type.split(";", 1)[0].strip().casefold()
            in {"application/zip", "application/x-zip-compressed"}
        )
        with boundary.open_readonly_duplicate(output) as stream:
            if not declared_archive and not archive_container_detected(stream):
                return
            stream.seek(0)
            with open_inspected_archive(stream, self._resource_limits()):
                pass

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

    def open_workspace_directory_fd(self, workspace_path: str) -> int:
        """Lease an HA workspace directory descriptor for process launch."""
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError(
                "workspace descriptor leases require the home_assistant profile"
            )
        return self._home_assistant_boundary().open_directory_fd(workspace_path)

    def lease_run_attachments(
        self,
        record: ThreadRecord,
    ) -> dict[str, WorkspaceAnonymousFileLease]:
        """Create sealed anonymous copies for one HA Codex process."""
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError(
                "anonymous attachment leases require the home_assistant profile"
            )
        self._validate_thread_attachments(record)
        boundary = self._home_assistant_uploads_boundary()
        leases: dict[str, WorkspaceAnonymousFileLease] = {}
        try:
            for attachment in record.attachments:
                lease = self._lease_transient_snapshot(
                    boundary,
                    attachment.stored_path,
                )
                if (
                    attachment.size_bytes is not None
                    and lease.size_bytes != attachment.size_bytes
                ):
                    lease.close()
                    raise WorkspaceEscapeError()
                if attachment.sha256 is not None:
                    digest = hashlib.sha256()
                    offset = 0
                    while block := os.pread(lease.fileno(), 1024 * 1024, offset):
                        digest.update(block)
                        offset += len(block)
                    if digest.hexdigest() != attachment.sha256:
                        lease.close()
                        raise WorkspaceEscapeError()
                leases[attachment.attachment_id] = lease
            return leases
        except BaseException:
            for lease in leases.values():
                lease.close()
            raise

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

    def _validate_thread_attachments(self, record: ThreadRecord) -> ThreadRecord:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return record
        boundary = self._home_assistant_uploads_boundary()
        thread_locator = boundary.normalize(record.thread_id)
        if thread_locator != record.thread_id or "/" in thread_locator:
            raise WorkspaceInputError()
        attachment_ids: set[str] = set()
        attachment_locators: set[str] = set()
        for attachment in record.attachments:
            if attachment.relative_path is None:
                raise WorkspaceInputError()
            relative = boundary.normalize(attachment.relative_path)
            stored = boundary.normalize(attachment.stored_path)
            filename = boundary.normalize(attachment.filename)
            if (
                relative != attachment.relative_path
                or stored != attachment.stored_path
                or filename != attachment.filename
                or "/" in filename
                or PurePosixPath(relative).name != filename
            ):
                raise WorkspaceInputError()
            expected_stored = f"{thread_locator}/{relative}"
            if stored != expected_stored:
                raise WorkspaceEscapeError()
            if (
                attachment.attachment_id in attachment_ids
                or stored in attachment_locators
            ):
                raise WorkspaceInputError()
            attachment_ids.add(attachment.attachment_id)
            attachment_locators.add(stored)
            boundary.validate_file_locator(stored)
        return record

    def _validate_thread_artifacts(self, record: ThreadRecord) -> ThreadRecord:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            return record
        workspace_boundary = self._home_assistant_boundary()
        archive_boundary = self._home_assistant_artifacts_boundary()
        artifact_ids: set[str] = set()
        artifact_owners: set[tuple[ArtifactSource, str]] = set()
        for artifact in record.artifacts:
            if artifact.relative_path is None:
                raise WorkspaceInputError()
            if artifact.source is ArtifactSource.WORKSPACE:
                boundary = workspace_boundary
                relative = boundary.normalize(artifact.relative_path)
                stored = boundary.normalize(artifact.stored_path)
                expected_stored = self._workspace_child_locator(
                    record.workspace_path,
                    relative,
                )
            elif artifact.source is ArtifactSource.WORKSPACE_ARCHIVE:
                boundary = archive_boundary
                thread_locator = boundary.normalize(record.thread_id)
                if thread_locator != record.thread_id or "/" in thread_locator:
                    raise WorkspaceInputError()
                relative = boundary.normalize(artifact.relative_path)
                stored = boundary.normalize(artifact.stored_path)
                expected_stored = f"{thread_locator}/{relative}"
            elif artifact.source is ArtifactSource.GENERATED_IMAGE:
                boundary = archive_boundary
                thread_locator = boundary.normalize(record.thread_id)
                if thread_locator != record.thread_id or "/" in thread_locator:
                    raise WorkspaceInputError()
                relative = boundary.normalize(artifact.relative_path)
                stored = boundary.normalize(artifact.stored_path)
                expected_stored = f"{thread_locator}/generated/{relative}"
            else:
                raise WorkspaceInputError()

            filename = boundary.normalize(artifact.filename)
            if (
                relative != artifact.relative_path
                or stored != artifact.stored_path
                or filename != artifact.filename
                or "/" in filename
                or PurePosixPath(relative).name != filename
            ):
                raise WorkspaceInputError()
            if stored != expected_stored:
                raise WorkspaceEscapeError()
            owner = (artifact.source, stored)
            if artifact.artifact_id in artifact_ids or owner in artifact_owners:
                raise WorkspaceInputError()
            artifact_ids.add(artifact.artifact_id)
            artifact_owners.add(owner)
            boundary.validate_file_locator(stored)
        return record

    def _workspace_child_locator(self, workspace_path: str, child: str) -> str:
        boundary = self._home_assistant_boundary()
        workspace = boundary.normalize(workspace_path, allow_root=True)
        relative = boundary.normalize(child)
        if workspace == ".":
            return relative
        return boundary.normalize(f"{workspace}/{relative}")

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
            discovered_provisional
            if defaults_provisional is None
            else defaults_provisional,
        )

    def _migrate_provisional_special_defaults(
        self,
        record: ProjectRecord,
        *,
        default_model: str,
        default_thinking_level: str,
        defaults_provisional: bool,
    ) -> bool:
        if (
            record.defaults_origin is not ProjectDefaultsOrigin.FALLBACK
            or defaults_provisional
        ):
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
                thread = ThreadRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            belongs_to_project = thread.project_id == project.project_id
            if (
                project.project_id == self._imported_project_id()
                and thread.project_id is None
            ):
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
            if (
                not self._special_default_migration_pending
                or self._has_active_thread_runs()
            ):
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
                record = ThreadRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
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
                record = ThreadRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if (
                record.project_id is None
                or record.project_id == self._imported_project_id()
            ):
                return True
        return False

    def ensure_imported_project(
        self,
        *,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        defaults_provisional: bool | None = None,
    ) -> ProjectRecord:
        default_model, default_thinking_level, defaults_provisional = (
            self._special_project_defaults(
                default_model,
                default_thinking_level,
                defaults_provisional,
            )
        )
        target = self._project_path(self._imported_project_id())
        if target.exists():
            record = ProjectRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            record = self._ensure_project_workspace(record)
            changed = self._migrate_provisional_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            changed = (
                self._migrate_legacy_special_defaults(
                    record,
                    default_model=default_model,
                    default_thinking_level=default_thinking_level,
                )
                or changed
            )
            if (
                record.kind is not ProjectKind.IMPORTED
                or record.name != self.imported_project_name
            ):
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
        default_model, default_thinking_level, defaults_provisional = (
            self._special_project_defaults(
                default_model,
                default_thinking_level,
                defaults_provisional,
            )
        )
        target = self._project_path(self._direct_project_id())
        if target.exists():
            record = ProjectRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            record = self._ensure_project_workspace(record)
            changed = self._migrate_provisional_special_defaults(
                record,
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            changed = (
                self._migrate_legacy_special_defaults(
                    record,
                    default_model=default_model,
                    default_thinking_level=default_thinking_level,
                )
                or changed
            )
            if (
                record.kind is not ProjectKind.DIRECT
                or record.name != self.direct_project_name
            ):
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
        if (
            project_id == self._imported_project_id()
            and record.kind is not ProjectKind.IMPORTED
        ):
            record.kind = ProjectKind.IMPORTED
            record.name = self.imported_project_name
            self.save_project(record)
        if (
            project_id == self._direct_project_id()
            and record.kind is not ProjectKind.DIRECT
        ):
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
                        ProjectRecord.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
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
        if (
            self._project_path(self._imported_project_id()).exists()
            or self._has_legacy_threads()
        ):
            imported = self.ensure_imported_project(
                default_model=default_model,
                default_thinking_level=default_thinking_level,
                defaults_provisional=defaults_provisional,
            )
            records[imported.project_id] = imported
        ordered = sorted(
            records.values(), key=lambda record: record.updated_at, reverse=True
        )
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
                raise ProjectMutationError(
                    "special project workspaces cannot be changed"
                )
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
            record.default_thinking_level = (
                default_thinking_level or DEFAULT_THINKING_LEVEL
            )
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
                boundary.resolve_relative(
                    record.root_path, must_exist=True, kind="directory"
                )
        self._atomic_write_json(
            self._project_path(record.project_id), record.model_dump()
        )

    def archive_project(self, project_id: str) -> ProjectRecord:
        with self._automation_target_lock:
            self._assert_automation_project_unreserved(project_id)
            with self._project_mutation_lock:
                record = self.load_project(project_id)
                if record.kind is not ProjectKind.PROJECT:
                    raise ProjectMutationError("only normal projects can be archived")
                record.archived_at = self._now()
                record.updated_at = record.archived_at
                self.save_project(record)
                return record

    def restore_project(self, project_id: str) -> ProjectRecord:
        with self._project_mutation_lock:
            record = self.load_project(project_id)
            if record.kind is not ProjectKind.PROJECT:
                raise ProjectMutationError("only normal projects can be restored")
            record.archived_at = None
            record.updated_at = self._now()
            self.save_project(record)
            return record

    def delete_project(self, project_id: str) -> None:
        with self._automation_target_lock:
            self._assert_automation_project_unreserved(project_id)
            with self._project_mutation_lock:
                self._delete_project_locked(project_id)

    def _delete_project_locked(self, project_id: str) -> None:
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
            relative = (
                "."
                if path is None or not str(path).strip()
                else boundary.normalize(
                    path,
                    allow_root=True,
                )
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
            return PathBrowseRecord(
                path=None, parent_path=None, directories=directories
            )

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

    def create_folder(
        self, *, parent_path: str, folder_name: str
    ) -> PathBrowseEntryRecord:
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
        with self._thread_mutation_lock:
            target = self._thread_path(thread_id)
            if not target.exists():
                raise ThreadNotFoundError(thread_id)
            record = ThreadRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            record = self._ensure_thread_workspace(record)
            record = self._ensure_thread_project(record)
            record = self._ensure_thread_model(record)
            record = self._validate_thread_attachments(record)
            record = self._validate_thread_artifacts(record)
            return self._ensure_thread_timestamps(record)

    def get_thread(self, thread_id: str) -> ThreadViewRecord:
        return self._resolve_thread(self.load_thread(thread_id))

    def list_threads(self, *, include_archived: bool = False) -> list[ThreadViewRecord]:
        with self._thread_mutation_lock:
            records = [
                self._ensure_thread_project(
                    self._ensure_thread_workspace(
                        ThreadRecord.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                    )
                )
                for path in self.threads_dir.glob("*.json")
            ]
            records = [self._validate_thread_attachments(record) for record in records]
            records = [self._validate_thread_artifacts(record) for record in records]
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
                workspace_path = boundary.create_directory(
                    boundary.normalize(project.root_path)
                )
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
        self._save_thread_with_events(
            record,
            EventDraft(
                scope="thread",
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
            ),
        )
        return self._resolve_thread(record)

    @contextmanager
    def prepare_automation_target(
        self,
        target: Mapping[str, str],
        *,
        title: str,
        mode: RunMode,
        model_override: str | None = None,
        thinking_override: str | None = None,
    ) -> Iterator[ThreadViewRecord]:
        """Prepare and reserve an automation target through prompt submission.

        The reservation is deliberately separate from the storage mutation
        locks. Runtime submission re-enters storage synchronously, while
        archive/delete reject a reserved project or thread instead of racing
        between target validation, thread preparation, and prompt admission.
        """
        kind = target.get("kind")
        project_id = target.get("project_id")
        thread_id = target.get("thread_id")
        if kind not in {"standalone", "continue_thread"}:
            raise ProjectMutationError("automation target is invalid")

        with self._automation_target_lock:
            with self._project_mutation_lock:
                with (
                    self._thread_mutation_lock
                    if kind == "continue_thread"
                    else nullcontext()
                ):
                    if kind == "standalone":
                        if not project_id:
                            raise ProjectMutationError("automation target is invalid")
                        project = self.load_project(project_id)
                        if project.archived_at is not None:
                            raise ProjectMutationError("automation project is archived")
                        self._reserve_automation_target_locked(project_id, None)
                        try:
                            thread = self._create_thread_locked(
                                title=title,
                                mode=mode,
                                project_id=project_id,
                                model_override=model_override,
                                thinking_override=thinking_override,
                            )
                        except BaseException:
                            self._release_automation_target_locked(project_id, None)
                            raise
                    else:
                        if not thread_id:
                            raise ProjectMutationError("automation target is invalid")
                        record = self.load_thread(thread_id)
                        project = self.load_project(record.project_id or "")
                        if (
                            project.archived_at is not None
                            or record.archived_at is not None
                        ):
                            raise ProjectMutationError("automation target is archived")
                        if (
                            record.active_run_id is not None
                            or record.pending_prompts
                            or record.status in {"queued", "running"}
                        ):
                            raise ProjectMutationError("automation thread is busy")
                        if self._automation_thread_reservations.get(thread_id, 0):
                            raise ProjectMutationError(
                                "automation thread is reserved for an automation run"
                            )
                        self._reserve_automation_target_locked(
                            project.project_id, thread_id
                        )
                        try:
                            thread = self._update_thread_record_locked(
                                record,
                                mode=mode,
                                model_override=model_override,
                                thinking_override=thinking_override,
                            )
                        except BaseException:
                            self._release_automation_target_locked(
                                project.project_id, thread_id
                            )
                            raise
        try:
            yield thread
        finally:
            with self._automation_target_lock:
                self._release_automation_target_locked(
                    thread.project_id,
                    None if kind == "standalone" else thread.thread_id,
                )

    def _reserve_automation_target_locked(
        self, project_id: str, thread_id: str | None
    ) -> None:
        self._automation_project_reservations[project_id] = (
            self._automation_project_reservations.get(project_id, 0) + 1
        )
        if thread_id is not None:
            self._automation_thread_reservations[thread_id] = (
                self._automation_thread_reservations.get(thread_id, 0) + 1
            )

    def _release_automation_target_locked(
        self, project_id: str, thread_id: str | None
    ) -> None:
        project_count = self._automation_project_reservations.get(project_id, 0)
        if project_count <= 1:
            self._automation_project_reservations.pop(project_id, None)
        else:
            self._automation_project_reservations[project_id] = project_count - 1
        if thread_id is not None:
            thread_count = self._automation_thread_reservations.get(thread_id, 0)
            if thread_count <= 1:
                self._automation_thread_reservations.pop(thread_id, None)
            else:
                self._automation_thread_reservations[thread_id] = thread_count - 1

    def _assert_automation_project_unreserved(self, project_id: str) -> None:
        if self._automation_project_reservations.get(project_id, 0):
            raise ProjectMutationError("project is reserved for an automation run")

    def _assert_automation_thread_unreserved(self, record: ThreadRecord) -> None:
        if self._automation_thread_reservations.get(
            record.thread_id, 0
        ) or self._automation_project_reservations.get(record.project_id or "", 0):
            raise ProjectMutationError("thread is reserved for an automation run")

    @contextmanager
    def admit_thread_deletion(self, thread_id: str) -> Iterator[ThreadRecord]:
        """Admit broker-owned deletion before it takes the runtime lock."""
        with self._automation_target_lock:
            with self._thread_mutation_lock:
                record = self.load_thread(thread_id)
                self._assert_automation_thread_unreserved(record)
            yield record

    @contextmanager
    def admit_project_deletion(self, project_id: str) -> Iterator[ProjectRecord]:
        """Admit broker-owned cascade deletion before it takes the runtime lock."""
        with self._automation_target_lock:
            with self._project_mutation_lock:
                record = self.load_project(project_id)
                self._assert_automation_project_unreserved(project_id)
            yield record

    def update_thread(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        mode: RunMode | None = None,
        model_override: str | None | object = _UNSET,
        thinking_override: str | None | object = _UNSET,
    ) -> ThreadViewRecord:
        with self._automation_target_lock:
            with self._project_mutation_lock:
                with self._thread_mutation_lock:
                    record = self.load_thread(thread_id)
                    self._assert_automation_thread_unreserved(record)
                    return self._update_thread_record_locked(
                        record,
                        title=title,
                        mode=mode,
                        model_override=model_override,
                        thinking_override=thinking_override,
                    )

    def _update_thread_record_locked(
        self,
        record: ThreadRecord,
        *,
        title: str | None = None,
        mode: RunMode | None = None,
        model_override: str | None | object = _UNSET,
        thinking_override: str | None | object = _UNSET,
    ) -> ThreadViewRecord:
        if title is not None:
            if not title.strip():
                raise ValueError("title must not be blank")
            record.title = title.strip()
        if mode is not None:
            record.mode = mode
        if model_override is not _UNSET:
            record.model_override = (
                normalize_model(model_override) if model_override else None
            )
        if thinking_override is not _UNSET:
            record.thinking_override = thinking_override
        self._touch_thread(record)
        self._save_thread_with_events(
            record,
            EventDraft(
                scope="thread",
                thread_id=record.thread_id,
                event_type="thread.updated",
                payload={
                    "title": record.title,
                    "mode": record.mode.value,
                    "model_override": record.model_override,
                    "thinking_override": record.thinking_override,
                },
            ),
        )
        return self._resolve_thread(record)

    def archive_thread(self, thread_id: str) -> ThreadViewRecord:
        with self._automation_target_lock:
            with self._project_mutation_lock:
                with self._thread_mutation_lock:
                    record = self.load_thread(thread_id)
                    self._assert_automation_thread_unreserved(record)
                    record.archived_at = self._now()
                    self._touch_thread(record)
                    self._save_thread_with_events(
                        record,
                        EventDraft(
                            scope="thread",
                            thread_id=thread_id,
                            event_type="thread.archived",
                            payload={"archived_at": record.archived_at},
                        ),
                    )
                    return self._resolve_thread(record)

    def restore_thread(self, thread_id: str) -> ThreadViewRecord:
        with self._project_mutation_lock:
            with self._thread_mutation_lock:
                record = self.load_thread(thread_id)
                record.archived_at = None
                self._touch_thread(record)
                self._save_thread_with_events(
                    record,
                    EventDraft(
                        scope="thread",
                        thread_id=thread_id,
                        event_type="thread.restored",
                        payload={"restored_at": record.updated_at},
                    ),
                )
                return self._resolve_thread(record)

    def delete_thread(self, thread_id: str) -> None:
        # Keep the same upload -> thread ordering used by completion/cancel.
        with self._automation_target_lock:
            with self._thread_mutation_lock:
                self._assert_automation_thread_unreserved(self.load_thread(thread_id))
            with self._upload_mutation_lock:
                if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
                    boundary = self._home_assistant_uploads_boundary()
                    manifest_paths = boundary.walk_regular_files(".sessions")
                else:
                    manifest_paths = ()
                for manifest_path in manifest_paths:
                    if not manifest_path.endswith(".json"):
                        continue
                    try:
                        with boundary.open_regular_file(manifest_path) as stream:
                            payload = json.loads(stream.read().decode("utf-8"))
                    except (WorkspaceBoundaryError, OSError, ValueError):
                        continue
                    if (
                        isinstance(payload, dict)
                        and payload.get("thread_id") == thread_id
                    ):
                        upload_id = payload.get("upload_id")
                        if isinstance(upload_id, str) and re.fullmatch(
                            r"upl_[0-9a-f]{32}", upload_id
                        ):
                            received = payload.get("received", {})
                            if isinstance(received, dict):
                                if payload.get("status") == "publishing":
                                    try:
                                        self._rollback_published_upload_locked(payload)
                                    except (
                                        UploadConflictError,
                                        WorkspaceBoundaryError,
                                    ):
                                        # Thread deletion will fail closed when a
                                        # hostile replacement prevents precise
                                        # cleanup; never unlink by pathname alone.
                                        raise
                                self._clear_upload_payload_locked(upload_id, received)
                                boundary.remove_empty_directory(
                                    self._upload_payload_dir(upload_id), missing_ok=True
                                )
                        boundary.unlink_regular_file(manifest_path, missing_ok=True)
                self._delete_thread_locked(thread_id)

    def _delete_thread_locked(self, thread_id: str) -> None:
        with self._thread_mutation_lock:
            record = self.load_thread(thread_id)
            self._assert_automation_thread_unreserved(record)
            # Remove replayable prompts/deltas before deleting metadata. If a
            # later filesystem cleanup fails, privacy fails closed and a retry
            # can finish the remaining idempotent deletion work.
            self.event_store.purge_thread(thread_id)
            if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
                artifacts_boundary = self._home_assistant_artifacts_boundary()
                for artifact in record.artifacts:
                    if artifact.source in {
                        ArtifactSource.WORKSPACE_ARCHIVE,
                        ArtifactSource.GENERATED_IMAGE,
                    }:
                        artifacts_boundary.unlink_regular_file(
                            artifact.stored_path,
                            missing_ok=True,
                        )
                # A generated image is published before thread metadata.  If
                # the process died in that crash window, the deterministic
                # orphan is absent from ``record.artifacts``; reap only the
                # generated-name regular files we own and leave unknown or
                # hostile entries for a fail-closed retry.
                generated_locator = f"{record.thread_id}/generated"
                try:
                    generated_orphans = artifacts_boundary.walk_regular_files(
                        generated_locator,
                        reject_unsafe=True,
                    )
                except WorkspaceNotFoundError:
                    generated_orphans = ()
                for orphan in generated_orphans:
                    if re.fullmatch(
                        r"codex-image-[0-9a-f]{24}\.(?:png|jpg|webp)",
                        PurePosixPath(orphan).name,
                    ):
                        artifacts_boundary.unlink_regular_file(
                            orphan,
                            missing_ok=True,
                        )
                artifacts_boundary.remove_empty_directory(
                    generated_locator,
                    missing_ok=True,
                )
                artifacts_boundary.remove_empty_directory(
                    record.thread_id, missing_ok=True
                )
            thread_path = self._thread_path(thread_id)
            if thread_path.exists():
                thread_path.unlink()

            event_path = self._event_log_path(thread_id)
            if event_path.exists():
                event_path.unlink()
            self._event_next_sequences.pop(thread_id, None)

            if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
                self._remove_upload_tree_locked(thread_id)
            else:
                upload_dir = self.uploads_dir / thread_id
                if upload_dir.exists():
                    for path in sorted(upload_dir.rglob("*"), reverse=True):
                        if path.is_file():
                            path.unlink()
                        elif path.is_dir():
                            path.rmdir()
                    upload_dir.rmdir()

    def _remove_upload_tree_locked(self, relative: str) -> None:
        """Clear Bridge-owned uploads through the retained private descriptor."""

        boundary = self._home_assistant_uploads_boundary()
        try:
            files = boundary.walk_regular_files(relative, reject_unsafe=True)
        except WorkspaceNotFoundError:
            return
        directories = {relative}
        pending = [relative]
        while pending:
            directory = pending.pop()
            for child in boundary.list_directories(directory):
                directories.add(child)
                pending.append(child)
        for locator in files:
            boundary.unlink_regular_file(locator, missing_ok=True)
        for directory in sorted(
            directories, key=lambda item: item.count("/"), reverse=True
        ):
            boundary.remove_empty_directory(directory, missing_ok=True)

    def save_thread(self, record: ThreadRecord) -> None:
        with self._thread_mutation_lock:
            self._prepare_thread_for_save_locked(record)
            self._atomic_write_json(
                self._thread_path(record.thread_id), record.model_dump()
            )

    def _save_thread_with_events(
        self,
        record: ThreadRecord,
        *events: EventDraft,
    ) -> None:
        """Commit canonical thread metadata and its public events together."""
        with self._thread_mutation_lock:
            self._prepare_thread_for_save_locked(record)
            self._commit_prepared_thread_with_events_locked(record, events)

    def _commit_prepared_thread_with_events_locked(
        self,
        record: ThreadRecord,
        events: tuple[EventDraft, ...],
    ) -> None:
        relative_path = f"threads/{record.thread_id}.json"
        revision = self.durable_outbox.next_state_revision(relative_path)
        self.durable_outbox.commit_operation(
            operation_id=f"thread-state:{record.thread_id}:{revision}:{uuid4().hex}",
            writes=(
                OutboxWrite(
                    relative_path=relative_path,
                    state_revision=revision,
                    state_payload=record.model_dump(mode="json"),
                ),
            ),
            events=events,
        )

    def _prepare_thread_for_save_locked(self, record: ThreadRecord) -> None:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            boundary = self._home_assistant_boundary()
            record.workspace_path = boundary.normalize(record.workspace_path)
            boundary.resolve_relative(
                record.workspace_path, must_exist=True, kind="directory"
            )
            if record.project_id is None:
                raise WorkspaceInputError()
            self._validate_thread_project_workspace(
                record,
                self.load_project(record.project_id),
            )
            self._validate_thread_attachments(record)
            self._validate_thread_artifacts(record)
            self._merge_persisted_home_assistant_attachments(record)
            self._merge_persisted_home_assistant_artifacts(record)
            self._validate_thread_attachments(record)
            self._validate_thread_artifacts(record)
        if not record.created_at:
            record.created_at = self._now()
        if not record.updated_at:
            record.updated_at = record.created_at

    def _merge_persisted_home_assistant_attachments(self, record: ThreadRecord) -> None:
        """Preserve append-only attachment metadata across stale thread writers."""
        target = self._thread_path(record.thread_id)
        try:
            persisted = ThreadRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return
        if persisted.thread_id != record.thread_id:
            raise WorkspaceInputError()
        self._validate_thread_attachments(persisted)

        incoming_by_id: dict[str, AttachmentRecord] = {}
        for attachment in record.attachments:
            existing = incoming_by_id.get(attachment.attachment_id)
            if existing is not None and existing != attachment:
                raise WorkspaceInputError()
            incoming_by_id[attachment.attachment_id] = attachment

        merged = list(persisted.attachments)
        persisted_by_id = {
            attachment.attachment_id: attachment for attachment in persisted.attachments
        }
        for attachment in record.attachments:
            persisted_attachment = persisted_by_id.get(attachment.attachment_id)
            if persisted_attachment is not None:
                if persisted_attachment != attachment:
                    raise WorkspaceInputError()
                continue
            merged.append(attachment)
        record.attachments = merged

    def _merge_persisted_home_assistant_artifacts(self, record: ThreadRecord) -> None:
        """Preserve append-only artifact metadata across stale thread writers."""
        target = self._thread_path(record.thread_id)
        try:
            persisted = ThreadRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return
        if persisted.thread_id != record.thread_id:
            raise WorkspaceInputError()
        self._validate_thread_artifacts(persisted)

        persisted_by_id = {
            artifact.artifact_id: artifact for artifact in persisted.artifacts
        }
        merged = list(persisted.artifacts)
        for artifact in record.artifacts:
            persisted_artifact = persisted_by_id.get(artifact.artifact_id)
            if persisted_artifact is not None:
                if persisted_artifact != artifact:
                    raise WorkspaceInputError()
                continue
            merged.append(artifact)
        record.artifacts = merged

    def fail_home_assistant_run_without_workspace_validation(
        self,
        *,
        thread_id: str,
        run_id: str,
        failure_type: str,
    ) -> bool:
        """Terminalize one failed HA run when its workspace cannot be reloaded.

        This deliberately bypasses only workspace validation. The private JSON
        still passes the ThreadRecord schema, the active run must match, and
        the emitted payload is selected from fixed path-free messages.
        """
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError(
                "terminal metadata fallback requires the home_assistant profile"
            )
        with self._thread_mutation_lock:
            target = self._thread_path(thread_id)
            if not target.exists():
                raise ThreadNotFoundError(thread_id)
            record = ThreadRecord.model_validate_json(
                target.read_text(encoding="utf-8")
            )
            if record.thread_id != thread_id or record.active_run_id != run_id:
                return False

            safe_failures: dict[str, tuple[str, bool, bool]] = {
                "auth.expired": (
                    AUTH_EXPIRED_MESSAGE,
                    False,
                    True,
                ),
                "model.unsupported": (
                    "The selected Codex model is not supported.",
                    False,
                    False,
                ),
                "limits.exhausted": (
                    "Codex usage limits have been reached.",
                    True,
                    False,
                ),
                "run.failed": ("The workspace is unavailable.", False, False),
            }
            normalized_failure_type = (
                failure_type if failure_type in safe_failures else "run.failed"
            )
            message, blocked, auth_required = safe_failures[normalized_failure_type]
            queued_count = len(record.pending_prompts)
            record.status = "error"
            record.active_run_id = None
            record.last_error = message
            record.pending_prompts.clear()
            self._touch_thread(record)
            payload: dict[str, object] = {
                "run_id": run_id,
                "error": message,
                "blocked": blocked,
                "failure_type": normalized_failure_type,
            }
            if auth_required:
                payload["auth_required"] = True
            events = [
                EventDraft(
                    scope="thread",
                    thread_id=thread_id,
                    event_type="run.failed",
                    payload=payload,
                )
            ]
            if queued_count:
                events.append(
                    EventDraft(
                        scope="thread",
                        thread_id=thread_id,
                        event_type="run.queue_cleared",
                        payload={
                            "reason": "active run failed",
                            "queued_count": queued_count,
                        },
                    )
                )
            # This fallback intentionally bypasses workspace validation, but
            # the validated private record and its public terminal events must
            # still cross the durability boundary as one operation.
            self._commit_prepared_thread_with_events_locked(record, tuple(events))
            return True

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
        project = self.load_project(
            record.project_id or self.ensure_imported_project().project_id
        )
        self._validate_thread_project_workspace(record, project)
        effective_model = normalize_model(
            record.model_override or project.default_model
        )
        effective_thinking_level = (
            record.thinking_override or project.default_thinking_level
        )
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
        stored = self.event_store.append(
            operation_key=f"thread:{thread_id}:{uuid4().hex}",
            scope="thread",
            thread_id=thread_id,
            event_type=event_type,
            payload=payload,
        )
        return ThreadEventRecord(
            event_id=stored.event_id,
            thread_id=thread_id,
            sequence=stored.scope_sequence,
            event_type=stored.event_type,
            payload=stored.payload,
            timestamp=stored.timestamp,
        )

    def list_thread_events(
        self, thread_id: str, *, after: int | None = None
    ) -> list[ThreadEventRecord]:
        return [
            ThreadEventRecord(
                event_id=event.event_id,
                thread_id=thread_id,
                sequence=event.scope_sequence,
                event_type=event.event_type,
                payload=event.payload,
                timestamp=event.timestamp,
            )
            for event in self.event_store.replay_thread(
                thread_id,
                after_sequence=after,
            )
        ]

    def _next_thread_event_sequence(self, thread_id: str) -> int:
        cached = self._event_next_sequences.get(thread_id)
        if cached is not None:
            return cached
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

    # Resumable upload state is intentionally private metadata rather than an
    # event/outbox payload: chunk progress is not a public thread transition.
    def _upload_session_path(self, upload_id: str) -> str:
        if not re.fullmatch(r"upl_[0-9a-f]{32}", upload_id):
            raise UploadNotFoundError(upload_id)
        return f".sessions/{upload_id}.json"

    def _upload_payload_dir(self, upload_id: str) -> str:
        if not re.fullmatch(r"upl_[0-9a-f]{32}", upload_id):
            raise UploadNotFoundError(upload_id)
        return f".sessions/{upload_id}"

    @staticmethod
    def _validate_upload_sha256(value: object) -> str:
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise UploadValidationError("sha256")
        return value

    def _read_upload_session_locked(self, upload_id: str) -> dict[str, object]:
        try:
            with self._home_assistant_uploads_boundary().open_regular_file(
                self._upload_session_path(upload_id)
            ) as stream:
                raw = stream.read(_UPLOAD_MANIFEST_MAX_BYTES + 1)
                if len(raw) > _UPLOAD_MANIFEST_MAX_BYTES:
                    raise UploadNotFoundError(upload_id)
                payload = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            raise UploadNotFoundError(upload_id) from None
        except (OSError, ValueError, TypeError):
            raise UploadNotFoundError(upload_id) from None
        if not isinstance(payload, dict):
            raise UploadNotFoundError(upload_id)
        if not _UPLOAD_SESSION_FIELDS.issubset(payload):
            raise UploadNotFoundError(upload_id)
        if payload["upload_id"] != upload_id or not isinstance(
            payload["thread_id"], str
        ):
            raise UploadNotFoundError(upload_id)
        if type(payload["size_bytes"]) is not int or payload["size_bytes"] <= 0:
            raise UploadNotFoundError(upload_id)
        if payload["size_bytes"] > self._resource_limits().max_upload_file_bytes:
            raise UploadNotFoundError(upload_id)
        try:
            self._validate_upload_sha256(payload["sha256"])
        except UploadValidationError:
            raise UploadNotFoundError(upload_id) from None
        if not isinstance(payload["received"], dict) or payload["status"] not in {
            "active",
            "publishing",
            "cancelled",
            "completed",
        }:
            raise UploadNotFoundError(upload_id)
        try:
            thread_id = normalize_portable_relative_path(payload["thread_id"])
            filename = normalize_portable_relative_path(payload["filename"])
            relative = normalize_portable_relative_path(payload["relative_path"])
        except (WorkspaceInputError, TypeError):
            raise UploadNotFoundError(upload_id) from None
        if (
            "/" in thread_id
            or "/" in filename
            or thread_id != payload["thread_id"]
            or filename != payload["filename"]
            or relative != payload["relative_path"]
            or len(filename.encode("utf-8")) > _UPLOAD_MAX_FILENAME_BYTES
            or len(relative.encode("utf-8")) > _UPLOAD_MAX_RELATIVE_PATH_BYTES
            or len(PurePosixPath(relative).parts) > _UPLOAD_MAX_RELATIVE_PATH_DEPTH
        ):
            raise UploadNotFoundError(upload_id)
        if PurePosixPath(relative).name != filename:
            raise UploadNotFoundError(upload_id)
        mime_type = payload.get("mime_type")
        if (
            not isinstance(mime_type, str)
            or not mime_type
            or len(mime_type.encode("utf-8")) > _UPLOAD_MAX_MIME_TYPE_BYTES
            or any(ord(char) < 32 for char in mime_type)
        ):
            raise UploadNotFoundError(upload_id)
        total = int(payload["size_bytes"])
        chunks = (total + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE
        received_indices: list[int] = []
        for raw_index, metadata in payload["received"].items():
            if (
                not isinstance(raw_index, str)
                or not raw_index.isdecimal()
                or str(int(raw_index)) != raw_index
            ):
                raise UploadNotFoundError(upload_id)
            index = int(raw_index)
            if index >= chunks or not isinstance(metadata, dict):
                raise UploadNotFoundError(upload_id)
            expected = min(_UPLOAD_CHUNK_SIZE, total - index * _UPLOAD_CHUNK_SIZE)
            if (
                type(metadata.get("size_bytes")) is not int
                or metadata.get("size_bytes") != expected
            ):
                raise UploadNotFoundError(upload_id)
            try:
                self._validate_upload_sha256(metadata.get("sha256"))
            except UploadValidationError:
                raise UploadNotFoundError(upload_id) from None
            received_indices.append(index)
        if sorted(received_indices) != list(range(len(received_indices))):
            raise UploadNotFoundError(upload_id)
        status = payload["status"]
        has_attachment = "attachment" in payload
        allowed_fields = _UPLOAD_SESSION_FIELDS | (
            {"attachment"} if has_attachment else set()
        )
        if set(payload) != allowed_fields:
            raise UploadNotFoundError(upload_id)
        if status in {"publishing", "completed"} and not has_attachment:
            raise UploadNotFoundError(upload_id)
        if has_attachment:
            try:
                raw_attachment = payload["attachment"]
                if not isinstance(raw_attachment, dict) or set(raw_attachment) != set(
                    AttachmentRecord.model_fields
                ):
                    raise UploadValidationError("attachment")
                attachment = AttachmentRecord.model_validate(raw_attachment)
                expected_attachment = self._upload_attachment_from_payload(payload)
            except (TypeError, ValueError, UploadValidationError):
                raise UploadNotFoundError(upload_id) from None
            if attachment != expected_attachment:
                raise UploadNotFoundError(upload_id)
        return payload

    def _upload_attachment_from_payload(
        self,
        payload: dict[str, object],
    ) -> AttachmentRecord:
        upload_id = str(payload["upload_id"])
        thread_id = str(payload["thread_id"])
        relative = str(payload["relative_path"])
        return AttachmentRecord(
            attachment_id=f"att_{upload_id}",
            filename=str(payload["filename"]),
            mime_type=str(payload["mime_type"]),
            stored_path=f"{thread_id}/resumable/{upload_id}/{relative}",
            relative_path=f"resumable/{upload_id}/{relative}",
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
        )

    def _verify_upload_attachment_locked(
        self,
        attachment: AttachmentRecord,
    ) -> WorkspaceFileIdentity:
        if attachment.size_bytes is None or attachment.sha256 is None:
            raise WorkspaceEscapeError()
        boundary = self._home_assistant_uploads_boundary()
        digest = hashlib.sha256()
        size_bytes = 0
        with boundary.open_regular_file(attachment.stored_path) as stream:
            identity = boundary.identify_open_file(stream)
            while block := stream.read(1024 * 1024):
                digest.update(block)
                size_bytes += len(block)
        if (
            size_bytes != attachment.size_bytes
            or digest.hexdigest() != attachment.sha256
        ):
            raise WorkspaceEscapeError()
        boundary.validate_regular_file_identity(attachment.stored_path, identity)
        return identity

    def _write_upload_session_locked(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        if len(encoded) > _UPLOAD_MANIFEST_MAX_BYTES:
            raise UploadValidationError("manifest")
        # The replacement briefly creates a second private manifest.  Check
        # both logical quota and filesystem free-space before creating it;
        # this has no retained reservation on a failed write.
        self._disk_quota().check("private", additional_bytes=len(encoded))
        self._home_assistant_uploads_boundary().atomic_write_bytes(
            self._upload_session_path(str(payload["upload_id"])), encoded
        )

    def _reconcile_published_upload_locked(
        self,
        payload: dict[str, object],
    ) -> AttachmentRecord | None:
        """Turn a durable attachment/event into a completed upload tombstone.

        The thread record is the authoritative commit point.  If the process
        died between that commit and the final manifest rewrite, cancellation
        must preserve the published inode rather than rolling it back.
        """
        if "attachment" not in payload:
            return None
        expected = self._upload_attachment_from_payload(payload)
        record = self.load_thread(str(payload["thread_id"]))
        existing = next(
            (
                item
                for item in record.attachments
                if item.attachment_id == expected.attachment_id
            ),
            None,
        )
        if existing is None:
            return None
        if existing != expected:
            raise UploadConflictError("attachment conflict")
        try:
            self._verify_upload_attachment_locked(existing)
        except WorkspaceBoundaryError as exc:
            raise UploadConflictError("attachment conflict") from exc
        payload["status"] = "completed"
        payload["attachment"] = existing.model_dump(mode="json")
        self._write_upload_session_locked(payload)
        received = payload["received"]
        assert isinstance(received, dict)
        self._clear_upload_payload_locked(str(payload["upload_id"]), received)
        return existing

    def _clear_upload_payload_locked(
        self, upload_id: str, received: dict[str, object]
    ) -> None:
        boundary = self._home_assistant_uploads_boundary()
        try:
            files = boundary.walk_regular_files(
                self._upload_payload_dir(upload_id), reject_unsafe=True
            )
        except WorkspaceNotFoundError:
            return
        for locator in files:
            boundary.unlink_regular_file(locator, missing_ok=True)

    def _reap_terminal_upload_sessions_locked(self) -> None:
        """Bound idle-session metadata and directories without touching active work."""
        boundary = self._home_assistant_uploads_boundary()
        terminal: list[tuple[str, dict[str, object]]] = []
        manifests = [
            locator
            for locator in boundary.walk_regular_files(".sessions")
            if locator.endswith(".json")
        ]
        for locator in manifests:
            upload_id = PurePosixPath(locator).stem
            try:
                payload = self._read_upload_session_locked(upload_id)
            except UploadNotFoundError:
                continue
            if payload["status"] in {"completed", "cancelled"}:
                terminal.append((upload_id, payload))
            # Live request streams register their exact part locators while
            # this lock is released. Every other unmanifested regular file is
            # recoverable debris from an interrupted writer or assembly.
            received = payload["received"]
            assert isinstance(received, dict)
            allowed = {
                f"{self._upload_payload_dir(upload_id)}/{index}.chunk"
                for index in received
            }
            try:
                for orphan in boundary.walk_regular_files(
                    self._upload_payload_dir(upload_id), reject_unsafe=True
                ):
                    if (
                        orphan not in allowed
                        and orphan not in self._active_upload_parts
                    ):
                        boundary.unlink_regular_file(orphan, missing_ok=True)
            except WorkspaceNotFoundError:
                pass
            if payload["status"] in {"completed", "cancelled"} and received:
                self._clear_upload_payload_locked(upload_id, received)
                payload["received"] = {}
                self._write_upload_session_locked(payload)
        reaped_manifest_paths: set[str] = set()
        for upload_id, payload in sorted(terminal)[
            : max(0, len(terminal) - _UPLOAD_TERMINAL_SESSION_LIMIT)
        ]:
            received = payload["received"]
            assert isinstance(received, dict)
            self._clear_upload_payload_locked(upload_id, received)
            boundary.remove_empty_directory(
                self._upload_payload_dir(upload_id), missing_ok=True
            )
            boundary.unlink_regular_file(
                self._upload_session_path(upload_id), missing_ok=True
            )
            reaped_manifest_paths.add(self._upload_session_path(upload_id))
        # Empty/partial directories without a valid manifest are not
        # sessions.  Remove only generated-name, regular-file-only entries;
        # hostile or unknown entries remain counted, so they cannot bypass
        # the resource ceiling by making cleanup unsafe.
        safe_directories = []
        for locator in boundary.list_directories(".sessions"):
            upload_id = PurePosixPath(locator).name
            if not re.fullmatch(r"upl_[0-9a-f]{32}", upload_id):
                continue
            safe_directories.append(locator)
            manifest_path = self._upload_session_path(upload_id)
            try:
                with boundary.open_regular_file(manifest_path):
                    continue
            except WorkspaceNotFoundError:
                pass
            try:
                for orphan in boundary.walk_regular_files(locator, reject_unsafe=True):
                    boundary.unlink_regular_file(orphan, missing_ok=True)
                boundary.remove_empty_directory(locator, missing_ok=True)
                safe_directories.pop()
            except WorkspaceBoundaryError:
                # Preserve unknown/hostile state for a manual investigation,
                # while retaining it in the hard count below.
                continue
        remaining_manifest_count = sum(
            locator not in reaped_manifest_paths for locator in manifests
        )
        if (
            remaining_manifest_count >= _UPLOAD_SESSION_LIMIT
            or len(safe_directories) >= _UPLOAD_SESSION_LIMIT
        ):
            raise QuotaExceededError("upload_sessions")

    @staticmethod
    def _upload_view(payload: dict[str, object]) -> dict[str, object]:
        received = payload["received"]
        assert isinstance(received, dict)
        indices = sorted(int(index) for index in received)
        size = int(payload["size_bytes"])
        next_index = 0
        while next_index in indices:
            next_index += 1
        return {
            "upload_id": payload["upload_id"],
            "thread_id": payload["thread_id"],
            "filename": payload["filename"],
            "mime_type": payload["mime_type"],
            "relative_path": payload.get("relative_path"),
            "size_bytes": size,
            "sha256": payload["sha256"],
            "chunk_size": _UPLOAD_CHUNK_SIZE,
            "total_chunks": (size + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE,
            "received_indices": indices,
            "next_offset": next_index * _UPLOAD_CHUNK_SIZE,
            "status": payload["status"],
        }

    def create_upload_session(
        self,
        *,
        thread_id: str,
        filename: str,
        mime_type: str = "application/octet-stream",
        relative_path: str | None = None,
        size_bytes: int,
        sha256: str,
    ) -> dict[str, object]:
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise UploadValidationError("runtime_profile")
        try:
            safe_filename = normalize_portable_relative_path(filename)
            if "/" in safe_filename or safe_filename != filename:
                raise WorkspaceInputError()
            safe_relative = normalize_portable_relative_path(
                relative_path or safe_filename
            )
            if PurePosixPath(safe_relative).name != safe_filename:
                raise WorkspaceInputError()
        except WorkspaceInputError:
            raise UploadValidationError("filename") from None
        if (
            len(safe_filename.encode("utf-8")) > _UPLOAD_MAX_FILENAME_BYTES
            or len(safe_relative.encode("utf-8")) > _UPLOAD_MAX_RELATIVE_PATH_BYTES
            or len(PurePosixPath(safe_relative).parts) > _UPLOAD_MAX_RELATIVE_PATH_DEPTH
        ):
            raise UploadValidationError("filename")
        if (
            not isinstance(mime_type, str)
            or not mime_type
            or len(mime_type.encode("utf-8")) > _UPLOAD_MAX_MIME_TYPE_BYTES
            or any(ord(char) < 32 for char in mime_type)
        ):
            raise UploadValidationError("mime_type")
        if type(size_bytes) is not int or size_bytes <= 0:
            raise UploadValidationError("size_bytes")
        if size_bytes > self._resource_limits().max_upload_file_bytes:
            raise QuotaExceededError("upload_file")
        digest = self._validate_upload_sha256(sha256)
        with self._upload_mutation_lock:
            # Deletion takes this lock before the thread lock.  Keeping the
            # validation and durable manifest creation inside it prevents a
            # session being created after its thread has disappeared.
            self.load_thread(thread_id)
            self._reap_terminal_upload_sessions_locked()
            upload_id = f"upl_{uuid4().hex}"
            payload: dict[str, object] = {
                "upload_id": upload_id,
                "thread_id": thread_id,
                "filename": safe_filename,
                "mime_type": mime_type,
                "relative_path": safe_relative,
                "size_bytes": size_bytes,
                "sha256": digest,
                "received": {},
                "status": "active",
            }
            self._home_assistant_uploads_boundary().create_directory(
                self._upload_payload_dir(upload_id)
            )
            try:
                self._write_upload_session_locked(payload)
            except BaseException:
                # If atomic manifest persistence reported failure before a
                # durable manifest exists, reclaim only the exact empty
                # generated directory.  A post-replace failure leaves a
                # valid session recoverable instead of being destroyed.
                try:
                    with self._home_assistant_uploads_boundary().open_regular_file(
                        self._upload_session_path(upload_id)
                    ):
                        pass
                except WorkspaceNotFoundError:
                    self._home_assistant_uploads_boundary().remove_empty_directory(
                        self._upload_payload_dir(upload_id), missing_ok=True
                    )
                raise
            return self._upload_view(payload)

    def get_upload_session(
        self, *, thread_id: str, upload_id: str
    ) -> dict[str, object]:
        with self._upload_mutation_lock:
            self.load_thread(thread_id)
            payload = self._read_upload_session_locked(upload_id)
            if payload["thread_id"] != thread_id:
                raise UploadNotFoundError(upload_id)
            return self._upload_view(payload)

    def begin_upload_chunk(
        self,
        *,
        thread_id: str,
        upload_id: str,
        index: int,
        offset: int,
        content_length: int,
        sha256: str,
    ) -> _UploadChunkWriter | dict[str, object]:
        """Validate and open a private no-follow part file for request streaming."""
        self._upload_mutation_lock.acquire()
        try:
            self.load_thread(thread_id)
            payload = self._read_upload_session_locked(upload_id)
            if payload["thread_id"] != thread_id:
                raise UploadNotFoundError(upload_id)
            if payload["status"] != "active":
                raise UploadConflictError("upload is not active")
            total = int(payload["size_bytes"])
            chunks = (total + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE
            if (
                type(index) is not int
                or index < 0
                or index >= chunks
                or offset != index * _UPLOAD_CHUNK_SIZE
            ):
                raise UploadConflictError("chunk offset")
            expected = min(_UPLOAD_CHUNK_SIZE, total - offset)
            if content_length != expected:
                raise UploadValidationError("content_length")
            digest = self._validate_upload_sha256(sha256)
            received = payload["received"]
            assert isinstance(received, dict)
            old = received.get(str(index))
            if isinstance(old, dict):
                if old.get("sha256") == digest and old.get("size_bytes") == expected:
                    calculated = hashlib.sha256()
                    actual = 0
                    with self._home_assistant_uploads_boundary().open_regular_file(
                        f"{self._upload_payload_dir(upload_id)}/{index}.chunk"
                    ) as source:
                        while block := source.read(1024 * 1024):
                            calculated.update(block)
                            actual += len(block)
                    if actual != expected or calculated.hexdigest() != digest:
                        raise UploadConflictError("stored chunk conflict")
                    return self._upload_view(payload)
                raise UploadConflictError("chunk conflict")
            if {int(value) for value in received} != set(range(index)):
                raise UploadConflictError("chunk order")
            reservation = self._disk_quota().reserve(
                "private",
                amount_bytes=expected,
                item_limit_bytes=self._resource_limits().max_upload_file_bytes,
            )
            try:
                writer = _UploadChunkWriter(
                    self, payload, index, digest, expected, reservation
                )
            except BaseException:
                reservation.release()
                raise
            return writer
        finally:
            self._upload_mutation_lock.release()

    def complete_upload_session(
        self, *, thread_id: str, upload_id: str
    ) -> AttachmentRecord:
        with self._upload_mutation_lock:
            payload = self._read_upload_session_locked(upload_id)
            if payload["thread_id"] != thread_id:
                raise UploadNotFoundError(upload_id)
            attachment = self._upload_attachment_from_payload(payload)
            record = self.load_thread(thread_id)
            for existing in record.attachments:
                if existing.attachment_id == attachment.attachment_id:
                    if existing != attachment:
                        raise UploadConflictError("attachment conflict")
                    try:
                        self._verify_upload_attachment_locked(existing)
                    except WorkspaceBoundaryError as exc:
                        raise UploadConflictError("attachment conflict") from exc
                    payload["status"] = "completed"
                    payload["attachment"] = existing.model_dump(mode="json")
                    self._write_upload_session_locked(payload)
                    received = payload["received"]
                    assert isinstance(received, dict)
                    self._clear_upload_payload_locked(upload_id, received)
                    return existing
            if payload["status"] == "publishing":
                recovered = self._reconcile_published_upload_locked(payload)
                if recovered is not None:
                    return recovered
            if payload["status"] not in {"active", "publishing"}:
                raise UploadConflictError("upload is not active")
            total = int(payload["size_bytes"])
            received = payload["received"]
            assert isinstance(received, dict)
            expected_indices = range(
                (total + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE
            )
            if set(received) != {str(index) for index in expected_indices}:
                raise UploadConflictError("upload incomplete")
            try:
                self._verify_upload_attachment_locked(attachment)
            except WorkspaceNotFoundError:
                self._publish_upload_attachment_locked(
                    payload=payload,
                    attachment=attachment,
                    expected_indices=expected_indices,
                )
            except WorkspaceBoundaryError as exc:
                raise UploadConflictError("published attachment conflict") from exc

            with self._thread_mutation_lock:
                record = self.load_thread(thread_id)
                existing = next(
                    (
                        item
                        for item in record.attachments
                        if item.attachment_id == attachment.attachment_id
                    ),
                    None,
                )
                if existing is not None:
                    if existing != attachment:
                        raise UploadConflictError("attachment conflict")
                else:
                    record.attachments.append(attachment)
                    self._touch_thread(record)
                    self._save_thread_with_events(
                        record,
                        EventDraft(
                            scope="thread",
                            thread_id=thread_id,
                            event_type="attachment.added",
                            payload={
                                "attachment_id": attachment.attachment_id,
                                "filename": attachment.filename,
                                "mime_type": attachment.mime_type,
                                "stored_path": attachment.stored_path,
                                "relative_path": attachment.relative_path,
                                "size_bytes": attachment.size_bytes,
                                "sha256": attachment.sha256,
                            },
                        ),
                    )
            payload["status"] = "completed"
            payload["attachment"] = attachment.model_dump(mode="json")
            self._write_upload_session_locked(payload)
            self._clear_upload_payload_locked(upload_id, received)
            return attachment

    def _publish_upload_attachment_locked(
        self,
        *,
        payload: dict[str, object],
        attachment: AttachmentRecord,
        expected_indices: range,
    ) -> None:
        """Assemble chunks and persist a recovery record before final publish."""

        total = int(payload["size_bytes"])
        received = payload["received"]
        assert isinstance(received, dict)
        boundary = self._home_assistant_uploads_boundary()
        target_parent = str(PurePosixPath(attachment.stored_path).parent)
        boundary.create_directory(target_parent)
        # A final assembly is kept below the session root until it is
        # checksum-verified and atomically published.  A hard process death
        # is therefore recoverable by the session's cancel/retry/delete path.
        target_part = (
            f"{self._upload_payload_dir(str(payload['upload_id']))}/assembly.part"
        )
        boundary.unlink_regular_file(target_part, missing_ok=True)
        reservation = self._disk_quota().reserve(
            "private",
            amount_bytes=total,
            item_limit_bytes=self._resource_limits().max_upload_file_bytes,
        )
        total_digest = hashlib.sha256()
        written = 0
        target_identity: WorkspaceFileIdentity | None = None
        published = False
        try:
            with boundary.create_file_exclusive(target_part) as output:
                target_identity = boundary.identify_open_file(output)
                for index in expected_indices:
                    chunk = (
                        f"{self._upload_payload_dir(str(payload['upload_id']))}"
                        f"/{index}.chunk"
                    )
                    try:
                        with boundary.open_regular_file(chunk) as source:
                            source_identity = boundary.identify_open_file(source)
                            data_digest = hashlib.sha256()
                            chunk_written = 0
                            while block := source.read(1024 * 1024):
                                reservation.consume(len(block))
                                _write_all(output, block)
                                total_digest.update(block)
                                data_digest.update(block)
                                written += len(block)
                                chunk_written += len(block)
                        boundary.validate_regular_file_identity(chunk, source_identity)
                    except WorkspaceBoundaryError as exc:
                        raise UploadConflictError("stored chunk") from exc
                    metadata = received[str(index)]
                    if (
                        not isinstance(metadata, dict)
                        or metadata.get("size_bytes") != chunk_written
                        or metadata.get("sha256") != data_digest.hexdigest()
                    ):
                        raise UploadValidationError("stored_chunk")
                output.flush()
                os.fsync(output.fileno())
                self._validate_uploaded_archive_if_present(
                    boundary,
                    output,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                )
            if written != total or total_digest.hexdigest() != attachment.sha256:
                raise UploadValidationError("sha256")
            payload["status"] = "publishing"
            payload["attachment"] = attachment.model_dump(mode="json")
            self._write_upload_session_locked(payload)
            assert target_identity is not None
            boundary.replace_regular_file(
                target_part,
                attachment.stored_path,
                expected_identity=target_identity,
            )
            published = True
            reservation.commit(persisted_bytes=total)
        except BaseException:
            if published and reservation.active:
                # A normal quota-commit failure is not a crash seam. Remove
                # only the inode we published, then restore an active session
                # so its verified chunks can be retried. A process crash here
                # leaves the durable ``publishing`` marker for recovery.
                try:
                    identity = self._verify_upload_attachment_locked(attachment)
                    boundary.unlink_regular_file(
                        attachment.stored_path,
                        missing_ok=True,
                        expected_identity=identity,
                    )
                    payload["status"] = "active"
                    payload.pop("attachment", None)
                    self._write_upload_session_locked(payload)
                    published = False
                except WorkspaceBoundaryError:
                    pass
            if not published and target_identity is not None:
                try:
                    boundary.unlink_regular_file(
                        target_part,
                        missing_ok=True,
                        expected_identity=target_identity,
                    )
                except WorkspaceBoundaryError:
                    pass
            if reservation.active:
                reservation.release()
            raise

    def cancel_upload_session(
        self, *, thread_id: str, upload_id: str
    ) -> dict[str, object]:
        with self._upload_mutation_lock:
            self.load_thread(thread_id)
            payload = self._read_upload_session_locked(upload_id)
            if payload["thread_id"] != thread_id:
                raise UploadNotFoundError(upload_id)
            if payload["status"] == "publishing":
                recovered = self._reconcile_published_upload_locked(payload)
                if recovered is not None:
                    return self._upload_view(payload)
            if payload["status"] in {"active", "publishing"}:
                payload["status"] = "cancelled"
                self._write_upload_session_locked(payload)
                # The terminal tombstone is durable before cleanup.  A crash
                # can leave private chunks, but never an active manifest that
                # incorrectly claims they are available for completion.
                received = payload["received"]
                assert isinstance(received, dict)
                self._rollback_published_upload_locked(payload)
                self._clear_upload_payload_locked(upload_id, received)
                payload["received"] = {}
                self._write_upload_session_locked(payload)
            elif payload["status"] == "cancelled":
                received = payload["received"]
                assert isinstance(received, dict)
                self._rollback_published_upload_locked(payload)
                if received:
                    self._clear_upload_payload_locked(upload_id, received)
                    payload["received"] = {}
                    self._write_upload_session_locked(payload)
            elif payload["status"] == "completed":
                received = payload["received"]
                assert isinstance(received, dict)
                if received:
                    self._clear_upload_payload_locked(upload_id, received)
                    payload["received"] = {}
                    self._write_upload_session_locked(payload)
            return self._upload_view(payload)

    def _rollback_published_upload_locked(self, payload: dict[str, object]) -> None:
        if "attachment" not in payload:
            return
        attachment = self._upload_attachment_from_payload(payload)
        try:
            identity = self._verify_upload_attachment_locked(attachment)
        except WorkspaceNotFoundError:
            return
        except WorkspaceBoundaryError as exc:
            raise UploadConflictError("published attachment conflict") from exc
        self._home_assistant_uploads_boundary().unlink_regular_file(
            attachment.stored_path,
            missing_ok=True,
            expected_identity=identity,
        )

    def attach_file(
        self,
        *,
        thread_id: str,
        filename: str,
        mime_type: str,
        content: bytes | BinaryIO,
        relative_path: str | None = None,
    ) -> AttachmentRecord:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            return self._attach_file_home_assistant(
                thread_id=thread_id,
                filename=filename,
                mime_type=mime_type,
                content=content,
                relative_path=relative_path,
            )

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
        self._save_thread_with_events(
            record,
            EventDraft(
                scope="thread",
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
            ),
        )
        return attachment

    def _attach_file_home_assistant(
        self,
        *,
        thread_id: str,
        filename: str,
        mime_type: str,
        content: bytes | BinaryIO,
        relative_path: str | None,
    ) -> AttachmentRecord:
        record = self.load_thread(thread_id)
        boundary = self._home_assistant_uploads_boundary()
        thread_locator = boundary.normalize(record.thread_id)
        normalized_filename = boundary.normalize(filename)
        if "/" in normalized_filename or normalized_filename != filename:
            raise WorkspaceInputError()

        target_relative = boundary.normalize(relative_path or normalized_filename)
        target_path = PurePosixPath(target_relative)
        normalized_filename = target_path.name
        stored_locator = f"{thread_locator}/{target_relative}"
        parent_locator = PurePosixPath(stored_locator).parent.as_posix()
        boundary.create_directory(parent_locator)
        limits = self._resource_limits()
        known_size = len(content) if isinstance(content, bytes) else 0
        reservation = self._disk_quota().reserve(
            "private",
            amount_bytes=known_size,
            item_limit_bytes=limits.max_upload_file_bytes,
        )

        output: BinaryIO | None = None
        try:
            for _attempt in range(100):
                try:
                    output = boundary.create_file_exclusive(stored_locator)
                    break
                except WorkspaceExistsError:
                    collision_name = (
                        f"{target_path.stem}-{uuid4().hex[:8]}{target_path.suffix}"
                    )
                    target_path = target_path.with_name(collision_name)
                    target_relative = target_path.as_posix()
                    normalized_filename = target_path.name
                    stored_locator = f"{thread_locator}/{target_relative}"
        except BaseException:
            reservation.release()
            raise
        if output is None:
            reservation.release()
            raise WorkspaceExistsError()

        identity: WorkspaceFileIdentity = boundary.identify_open_file(output)
        size_bytes = 0
        metadata_saved = False
        try:
            if hasattr(content, "read"):
                while True:
                    chunk = content.read(1024 * 1024)
                    if not chunk:
                        break
                    reservation.consume(len(chunk))
                    _write_all(output, chunk)
                    size_bytes += len(chunk)
            else:
                reservation.consume(len(content))
                _write_all(output, content)
                size_bytes = len(content)
            output.flush()
            os.fsync(output.fileno())
            self._validate_uploaded_archive_if_present(
                boundary,
                output,
                filename=normalized_filename,
                mime_type=mime_type,
            )

            attachment = AttachmentRecord(
                attachment_id=f"att_{uuid4().hex[:12]}",
                filename=normalized_filename,
                mime_type=mime_type,
                stored_path=stored_locator,
                relative_path=target_relative,
                size_bytes=size_bytes,
            )
            with self._thread_mutation_lock:
                # Prove the published locator still names the inode created by
                # this upload before committing any public metadata.
                boundary.validate_regular_file_identity(stored_locator, identity)
                reservation.commit(persisted_bytes=size_bytes)
                # Reload only after the potentially long stream has finished.
                # Concurrent uploads then merge into the latest thread record
                # while holding a short metadata critical section.
                record = self.load_thread(thread_id)
                record.attachments.append(attachment)
                self._touch_thread(record)
                self._save_thread_with_events(
                    record,
                    EventDraft(
                        scope="thread",
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
                    ),
                )
                metadata_saved = True
            return attachment
        except BaseException:
            cleanup_succeeded = False
            if not metadata_saved:
                try:
                    boundary.unlink_regular_file(
                        stored_locator,
                        missing_ok=True,
                        expected_identity=identity,
                    )
                    cleanup_succeeded = True
                except WorkspaceBoundaryError:
                    pass
            if cleanup_succeeded and reservation.active:
                reservation.release()
            raise
        finally:
            output.close()

    def get_artifact(self, thread_id: str, artifact_id: str) -> ArtifactRecord:
        record = self.load_thread(thread_id)
        for artifact in record.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        raise ThreadNotFoundError(artifact_id)

    def save_generated_image(
        self,
        *,
        thread_id: str,
        item_id: str,
        result: object,
        mime_type: object = None,
    ) -> ArtifactRecord:
        """Persist one Codex imageGeneration result in the private artifact boundary.

        The item id is part of the deterministic locator, making repeated
        completion notifications idempotent without retaining provider output.
        """
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError("generated images require the home_assistant profile")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", item_id):
            raise WorkspaceInputError()
        normalized_mime, content = _decode_generated_image(result, mime_type)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[normalized_mime]
        digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:24]
        artifact_id = f"art_img_{digest}"
        filename = f"codex-image-{digest}{extension}"
        boundary = self._home_assistant_artifacts_boundary()
        thread_locator = boundary.normalize(thread_id)
        if thread_locator != thread_id or "/" in thread_locator:
            raise WorkspaceInputError()
        relative_path = boundary.normalize(filename)
        stored_locator = f"{thread_locator}/generated/{relative_path}"
        artifact = ArtifactRecord(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=normalized_mime,
            source=ArtifactSource.GENERATED_IMAGE,
            stored_path=stored_locator,
            relative_path=relative_path,
            size_bytes=len(content),
        )

        def _matches_existing_file(identity: WorkspaceFileIdentity) -> bool:
            """Only reconcile a crash orphan after proving its exact bytes.

            The deterministic locator is private and is never exposed as an
            artifact until metadata is committed.  A path left by an earlier
            process is therefore safe to adopt only when it names the same
            regular inode and contains exactly the provider bytes for this
            item; arbitrary pre-existing data is discarded below.
            """

            try:
                with boundary.open_regular_file(stored_locator) as stream:
                    if boundary.identify_open_file(stream) != identity:
                        return False
                    stat_result = os.fstat(stream.fileno())
                    if stat_result.st_size != len(content):
                        return False
                    if stream.read(len(content) + 1) != content:
                        return False
                boundary.validate_regular_file_identity(stored_locator, identity)
                return True
            except (WorkspaceBoundaryError, OSError, ValueError):
                return False

        def _append_metadata_locked(record: ThreadRecord) -> ArtifactRecord:
            for existing in record.artifacts:
                if (
                    existing.source is ArtifactSource.GENERATED_IMAGE
                    and existing.artifact_id == artifact_id
                ):
                    return existing
            record.artifacts.append(artifact)
            self._touch_thread(record)
            self._save_thread_with_events(
                record,
                EventDraft(
                    scope="thread",
                    thread_id=thread_id,
                    event_type="artifact.added",
                    payload={
                        "artifact_id": artifact.artifact_id,
                        "filename": artifact.filename,
                        "mime_type": artifact.mime_type,
                        "source": artifact.source.value,
                        "stored_path": artifact.stored_path,
                        "relative_path": artifact.relative_path,
                        "size_bytes": artifact.size_bytes,
                    },
                ),
            )
            return artifact

        with self._thread_mutation_lock:
            record = self.load_thread(thread_id)
            for existing in record.artifacts:
                if (
                    existing.source is ArtifactSource.GENERATED_IMAGE
                    and existing.artifact_id == artifact_id
                ):
                    return existing

            # A process can die after fsyncing the deterministic image but
            # before the thread metadata/outbox commit.  Reconcile an exact
            # byte-for-byte orphan without charging quota a second time.
            try:
                existing_stat = boundary.regular_file_stat(stored_locator)
            except WorkspaceNotFoundError:
                existing_stat = None
            if existing_stat is not None:
                if _matches_existing_file(existing_stat.identity):
                    return _append_metadata_locked(record)
                # The path is regular but not ours (or was partially written).
                # Delete only the inode we inspected; a concurrent replacement
                # fails closed instead of unlinking arbitrary private data.
                boundary.unlink_regular_file(
                    stored_locator,
                    expected_identity=existing_stat.identity,
                )

        boundary.create_directory(f"{thread_locator}/generated")
        reservation = self._disk_quota().reserve(
            "private",
            amount_bytes=len(content),
            item_limit_bytes=_GENERATED_IMAGE_MAX_BYTES,
        )
        output: BinaryIO | None = None
        identity: WorkspaceFileIdentity | None = None
        created_by_us = False
        metadata_saved = False
        try:
            try:
                output = boundary.create_file_exclusive(stored_locator)
                created_by_us = True
            except WorkspaceExistsError:
                # A concurrent/replayed completion won the reservation race;
                # return its durable record when available and clean ours.
                with self._thread_mutation_lock:
                    record = self.load_thread(thread_id)
                    for existing in record.artifacts:
                        if existing.source is ArtifactSource.GENERATED_IMAGE and existing.artifact_id == artifact_id:
                            reservation.release()
                            return existing
                raise
            identity = boundary.identify_open_file(output)
            reservation.consume(len(content))
            _write_all(output, content)
            output.flush()
            os.fsync(output.fileno())
            boundary.validate_regular_file_identity(stored_locator, identity)
            reservation.commit(persisted_bytes=len(content))
            with self._thread_mutation_lock:
                record = self.load_thread(thread_id)
                existing = next(
                    (
                        candidate
                        for candidate in record.artifacts
                        if candidate.source is ArtifactSource.GENERATED_IMAGE
                        and candidate.artifact_id == artifact_id
                    ),
                    None,
                )
                if existing is not None:
                    metadata_saved = True
                    return existing
                _append_metadata_locked(record)
                metadata_saved = True
            return artifact
        except BaseException:
            if not metadata_saved:
                if created_by_us:
                    try:
                        boundary.unlink_regular_file(
                            stored_locator,
                            missing_ok=True,
                            expected_identity=identity,
                        )
                    except WorkspaceBoundaryError:
                        pass
                if reservation.active:
                    reservation.release()
                boundary.remove_empty_directory(
                    f"{thread_locator}/generated", missing_ok=True
                )
            raise
        finally:
            if output is not None:
                output.close()

    def sync_thread_artifacts(self, thread_id: str) -> list[ArtifactRecord]:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            return self._sync_thread_artifacts_home_assistant(thread_id)

        record = self.load_thread(thread_id)
        workspace_path = Path(record.workspace_path)
        known_by_path = {
            artifact.stored_path: artifact for artifact in record.artifacts
        }
        events: list[EventDraft] = []

        for target in sorted(
            path for path in workspace_path.rglob("*") if path.is_file()
        ):
            stored_path = str(target)
            if stored_path in known_by_path:
                continue

            artifact = ArtifactRecord(
                artifact_id=f"art_{uuid4().hex[:12]}",
                filename=target.name,
                mime_type=mimetypes.guess_type(target.name)[0]
                or "application/octet-stream",
                stored_path=stored_path,
                relative_path=str(target.relative_to(workspace_path)).replace(
                    "\\", "/"
                ),
                size_bytes=target.stat().st_size,
            )
            record.artifacts.append(artifact)
            events.append(
                EventDraft(
                    scope="thread",
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
            )

        if events:
            self._touch_thread(record)
            self._save_thread_with_events(record, *events)
        else:
            self.save_thread(record)
        return record.artifacts

    def _sync_thread_artifacts_home_assistant(
        self,
        thread_id: str,
    ) -> list[ArtifactRecord]:
        snapshot = self.load_thread(thread_id)
        boundary = self._home_assistant_boundary()
        limits = self._resource_limits()
        workspace = boundary.normalize(snapshot.workspace_path, allow_root=True)
        self._enforce_aggregate_workspace_limit(boundary, limits)
        try:
            discovered = boundary.manifest_regular_files(
                workspace,
                reject_unsafe=True,
                max_entries=limits.max_archive_entries,
                max_bytes=limits.max_workspace_bytes,
            ).files
        except WorkspaceResourceLimitError:
            raise QuotaExceededError("workspace") from None
        scanned: list[tuple[str, str, WorkspaceFileIdentity, int]] = []
        workspace_parts = PurePosixPath(workspace)
        for stored in discovered:
            if workspace == ".":
                relative = stored
            else:
                try:
                    relative = (
                        PurePosixPath(stored).relative_to(workspace_parts).as_posix()
                    )
                except ValueError:
                    raise WorkspaceEscapeError() from None
            relative = boundary.normalize(relative)
            file_stat = boundary.regular_file_stat(stored)
            if (
                file_stat.size_bytes
                > self._resource_limits().max_transient_snapshot_bytes
            ):
                raise QuotaExceededError("artifact_snapshot")
            scanned.append((stored, relative, file_stat.identity, file_stat.size_bytes))

        with self._thread_mutation_lock:
            for stored, _relative, identity, size_bytes in scanned:
                current = boundary.regular_file_stat(stored)
                if current.identity != identity or current.size_bytes != size_bytes:
                    raise WorkspaceEscapeError()
            record = self.load_thread(thread_id)
            known = {
                (artifact.source, artifact.stored_path) for artifact in record.artifacts
            }
            added: list[ArtifactRecord] = []
            for stored, relative, _identity, size_bytes in scanned:
                owner = (ArtifactSource.WORKSPACE, stored)
                if owner in known:
                    continue
                artifact = ArtifactRecord(
                    artifact_id=f"art_{uuid4().hex[:12]}",
                    filename=PurePosixPath(relative).name,
                    mime_type=(
                        mimetypes.guess_type(PurePosixPath(relative).name)[0]
                        or "application/octet-stream"
                    ),
                    source=ArtifactSource.WORKSPACE,
                    stored_path=stored,
                    relative_path=relative,
                    size_bytes=size_bytes,
                )
                record.artifacts.append(artifact)
                added.append(artifact)
                known.add(owner)

            if added:
                self._touch_thread(record)
                self._save_thread_with_events(
                    record,
                    *(
                        EventDraft(
                            scope="thread",
                            thread_id=thread_id,
                            event_type="artifact.added",
                            payload={
                                "artifact_id": artifact.artifact_id,
                                "filename": artifact.filename,
                                "mime_type": artifact.mime_type,
                                "source": artifact.source.value,
                                "stored_path": artifact.stored_path,
                                "relative_path": artifact.relative_path,
                                "size_bytes": artifact.size_bytes,
                            },
                        )
                        for artifact in added
                    ),
                )
            return record.artifacts

    def _enforce_aggregate_workspace_limit(
        self,
        boundary: WorkspaceBoundary,
        limits: ResourceLimits,
    ) -> None:
        """Measure every workspace without touching the mutable quota ledger.

        Archive entry limits describe a single user-requested archive, so they
        must not constrain a healthy multi-chat workspace tree.  The separate
        hard ceiling bounds this read-only aggregate traversal while retaining
        the global workspace byte cap.  Callers publish only private metadata
        or private archive output; a second aggregate scan cannot make an
        external workspace mutation atomic and would duplicate this traversal.
        """
        try:
            boundary.measure_regular_files(
                ".",
                reject_unsafe=False,
                max_entries=_WORKSPACE_AGGREGATE_SCAN_MAX_ENTRIES,
                max_bytes=limits.max_workspace_bytes,
            )
        except WorkspaceResourceLimitError:
            raise QuotaExceededError("workspace") from None

    def open_artifact(
        self,
        thread_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactRecord, BinaryIO, int]:
        """Open one HA artifact through its owning retained boundary."""
        if self.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise RuntimeError(
                "descriptor artifact opens require the home_assistant profile"
            )
        artifact = self.get_artifact(thread_id, artifact_id)
        if artifact.source is ArtifactSource.WORKSPACE:
            boundary = self._home_assistant_boundary()
        elif artifact.source in {
            ArtifactSource.WORKSPACE_ARCHIVE,
            ArtifactSource.GENERATED_IMAGE,
        }:
            boundary = self._home_assistant_artifacts_boundary()
        else:
            raise WorkspaceInputError()
        lease = self._lease_transient_snapshot(boundary, artifact.stored_path)
        size_bytes = lease.size_bytes
        file_fd, release = lease.detach_with_close_callback()
        try:
            raw_stream = os.fdopen(file_fd, "rb")
            stream = _ReleasingBinaryStream(raw_stream, release)
            return artifact, stream, size_bytes
        except BaseException:
            os.close(file_fd)
            if release is not None:
                release()
            raise

    def create_workspace_archive(self, thread_id: str) -> ArtifactRecord:
        if self.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            return self._create_workspace_archive_home_assistant(thread_id)
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
                for path in sorted(
                    candidate
                    for candidate in workspace_root.rglob("*")
                    if candidate.is_file()
                ):
                    archive.write(
                        path,
                        arcname=str(
                            Path("workspace") / path.relative_to(workspace_root)
                        ),
                    )
                    included_files += 1

            if uploads_root.exists():
                for path in sorted(
                    candidate
                    for candidate in uploads_root.rglob("*")
                    if candidate.is_file()
                ):
                    archive.write(
                        path,
                        arcname=str(Path("uploads") / path.relative_to(uploads_root)),
                    )
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
            source=ArtifactSource.WORKSPACE_ARCHIVE,
        )
        record.artifacts.append(artifact)
        self._touch_thread(record)
        self._save_thread_with_events(
            record,
            EventDraft(
                scope="thread",
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
            ),
        )
        return artifact

    def _create_workspace_archive_home_assistant(
        self,
        thread_id: str,
    ) -> ArtifactRecord:
        snapshot = self.load_thread(thread_id)
        limits = self._resource_limits()
        disk_quota = self._disk_quota()
        workspace_boundary = self._home_assistant_boundary()
        uploads_boundary = self._home_assistant_uploads_boundary()
        artifacts_boundary = self._home_assistant_artifacts_boundary()
        workspace = workspace_boundary.normalize(
            snapshot.workspace_path,
            allow_root=True,
        )
        self._enforce_aggregate_workspace_limit(workspace_boundary, limits)
        try:
            workspace_manifest = workspace_boundary.manifest_regular_files(
                workspace,
                reject_unsafe=True,
                max_entries=limits.max_archive_entries,
                max_bytes=limits.max_archive_expanded_bytes,
            )
        except WorkspaceResourceLimitError as error:
            resource = (
                "archive_entries" if error.resource == "entries" else "archive_expanded"
            )
            raise QuotaExceededError(resource) from None

        attachment_stats: dict[str, int] = {}
        preflight_expanded = StreamingByteCounter(
            limit_bytes=limits.max_archive_expanded_bytes,
            resource="archive_expanded",
        )
        preflight_expanded.consume(workspace_manifest.usage.logical_bytes)
        for attachment in snapshot.attachments:
            file_stat = uploads_boundary.regular_file_stat(attachment.stored_path)
            preflight_expanded.consume(file_stat.size_bytes)
            attachment_stats[attachment.attachment_id] = file_stat.size_bytes
        entry_count = workspace_manifest.usage.entry_count + len(snapshot.attachments)
        if entry_count > limits.max_archive_entries:
            raise QuotaExceededError("archive_entries")

        thread_locator = artifacts_boundary.normalize(snapshot.thread_id)
        if thread_locator != snapshot.thread_id or "/" in thread_locator:
            raise WorkspaceInputError()
        artifacts_boundary.create_directory(thread_locator)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        title_stem = "".join(
            char.lower() if char.isalnum() else "-"
            for char in (snapshot.title or snapshot.workspace_id)
        ).strip("-")[:120]
        reservation = disk_quota.reserve(
            "private",
            item_limit_bytes=limits.max_transient_snapshot_bytes,
        )
        output: BinaryIO | None = None
        stored_locator: str | None = None
        identity: WorkspaceFileIdentity | None = None
        metadata_saved = False
        try:
            for _attempt in range(100):
                filename = (
                    f"{title_stem or snapshot.workspace_id}-{timestamp}-"
                    f"{uuid4().hex[:8]}.zip"
                )
                relative_path = artifacts_boundary.normalize(filename)
                stored_locator = f"{thread_locator}/{relative_path}"
                try:
                    output = artifacts_boundary.create_file_exclusive(stored_locator)
                    break
                except WorkspaceExistsError:
                    continue
            if output is None or stored_locator is None:
                raise WorkspaceExistsError()
            identity = artifacts_boundary.identify_open_file(output)
            quota_output = _QuotaSequentialWriter(output, reservation)
            included_files = 0
            with zipfile.ZipFile(
                quota_output,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                workspace_parts = PurePosixPath(workspace)
                expanded = StreamingByteCounter(
                    limit_bytes=limits.max_archive_expanded_bytes,
                    resource="archive_expanded",
                )
                for stored in workspace_manifest.files:
                    if workspace == ".":
                        relative = stored
                    else:
                        try:
                            relative = (
                                PurePosixPath(stored)
                                .relative_to(workspace_parts)
                                .as_posix()
                            )
                        except ValueError:
                            raise WorkspaceEscapeError() from None
                    relative = workspace_boundary.normalize(relative)
                    self._copy_boundary_file_to_archive(
                        archive=archive,
                        member=f"workspace/{relative}",
                        boundary=workspace_boundary,
                        stored_locator=stored,
                        expanded_counter=expanded,
                    )
                    included_files += 1

                for attachment in snapshot.attachments:
                    assert attachment.relative_path is not None
                    attachment_relative = uploads_boundary.normalize(
                        attachment.relative_path
                    )
                    self._copy_boundary_file_to_archive(
                        archive=archive,
                        member=f"uploads/{attachment_relative}",
                        boundary=uploads_boundary,
                        stored_locator=attachment.stored_path,
                        expanded_counter=expanded,
                        expected_size=attachment_stats[attachment.attachment_id],
                    )
                    included_files += 1

                if included_files == 0:
                    readme = "This chat did not have any workspace files or uploaded files yet.\n"
                    expanded.consume(len(readme.encode("utf-8")))
                    archive.writestr(
                        "README.txt",
                        readme,
                    )

            output.flush()
            os.fsync(output.fileno())
            artifacts_boundary.validate_regular_file_identity(
                stored_locator,
                identity,
            )
            size_bytes = os.fstat(output.fileno()).st_size
            if size_bytes != reservation.consumed_bytes:
                raise ReservationConflictError("private")
            output.close()
            output = None
            artifacts_boundary.validate_regular_file_identity(
                stored_locator,
                identity,
            )
            with artifacts_boundary.open_regular_file(stored_locator) as archive_stream:
                with open_inspected_archive(
                    archive_stream,
                    limits,
                    max_container_bytes=limits.max_transient_snapshot_bytes,
                ):
                    pass
            reservation.commit(persisted_bytes=size_bytes)
            artifact = ArtifactRecord(
                artifact_id=f"art_{uuid4().hex[:12]}",
                filename=filename,
                mime_type="application/zip",
                source=ArtifactSource.WORKSPACE_ARCHIVE,
                stored_path=stored_locator,
                relative_path=relative_path,
                size_bytes=size_bytes,
            )
            with self._thread_mutation_lock:
                record = self.load_thread(thread_id)
                record.artifacts.append(artifact)
                self._touch_thread(record)
                self._save_thread_with_events(
                    record,
                    EventDraft(
                        scope="thread",
                        thread_id=thread_id,
                        event_type="artifact.added",
                        payload={
                            "artifact_id": artifact.artifact_id,
                            "filename": artifact.filename,
                            "mime_type": artifact.mime_type,
                            "source": artifact.source.value,
                            "stored_path": artifact.stored_path,
                            "relative_path": artifact.relative_path,
                            "size_bytes": artifact.size_bytes,
                        },
                    ),
                )
                metadata_saved = True
            return artifact
        except BaseException:
            cleanup_succeeded = stored_locator is None
            if not metadata_saved:
                if stored_locator is not None:
                    try:
                        artifacts_boundary.unlink_regular_file(
                            stored_locator,
                            missing_ok=True,
                            expected_identity=identity,
                        )
                        cleanup_succeeded = True
                    except WorkspaceBoundaryError:
                        cleanup_succeeded = False
            if cleanup_succeeded and reservation.active:
                reservation.release()
            raise
        finally:
            if output is not None:
                try:
                    output.close()
                except OSError:
                    pass

    def _copy_boundary_file_to_archive(
        self,
        *,
        archive: zipfile.ZipFile,
        member: str,
        boundary: WorkspaceBoundary,
        stored_locator: str,
        expanded_counter: StreamingByteCounter,
        expected_size: int | None = None,
    ) -> None:
        normalized_member = member.replace("\\", "/")
        member_path = PurePosixPath(normalized_member)
        if (
            member_path.is_absolute()
            or not member_path.parts
            or any(part in {"", ".", ".."} for part in member_path.parts)
        ):
            raise WorkspaceInputError()
        lease = self._lease_transient_snapshot(boundary, stored_locator)
        if expected_size is not None and lease.size_bytes != expected_size:
            lease.close()
            raise WorkspaceEscapeError()
        expanded_counter.consume(lease.size_bytes)
        file_fd, release = lease.detach_with_close_callback()
        try:
            source = os.fdopen(file_fd, "rb")
        except BaseException:
            os.close(file_fd)
            if release is not None:
                release()
            raise
        try:
            with source:
                with archive.open(member_path.as_posix(), mode="w") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
        finally:
            if release is not None:
                release()

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

    def update_limits_from_rate_data(
        self, rate_limits: dict[str, object]
    ) -> LimitsStatusRecord:
        status = LimitsStatusRecord(
            available=True,
            blocked=False,
            message=None,
            primary=self._limits_window(rate_limits.get("primary")),
            secondary=self._limits_window(rate_limits.get("secondary")),
            credits=rate_limits.get("credits")
            if isinstance(rate_limits.get("credits"), dict)
            else None,
            plan_type=str(rate_limits.get("plan_type"))
            if rate_limits.get("plan_type") is not None
            else None,
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
        if current.updated_at and (
            snapshot.updated_at is None or current.updated_at > snapshot.updated_at
        ):
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
            window_minutes=int(window_minutes)
            if isinstance(window_minutes, (int, float))
            else None,
            resets_at=int(resets_at) if isinstance(resets_at, (int, float)) else None,
        )
