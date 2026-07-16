"""Per-project AGENTS.md persistence with private rollback snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from contextlib import contextmanager
import os
from pathlib import Path
import re
from typing import Any, Iterator
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..runtime_gate import RuntimeGateError
from ..storage import ProjectNotFoundError
from ..workspace import (
    WorkspaceBoundaryError,
    WorkspaceNotFoundError,
)

router = APIRouter()

MAX_AGENTS_BYTES = 256 * 1024
MAX_BACKUPS = 16
MAX_BACKUP_BYTES = 8 * 1024 * 1024
_PROJECT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)


class AgentsWriteRequest(BaseModel):
    content: str = Field(max_length=MAX_AGENTS_BYTES)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_AGENTS_BYTES:
            raise ValueError("AGENTS.md is too large")
        if "\x00" in value or any(
            ord(char) < 32 and char not in "\r\n\t" for char in value
        ):
            raise ValueError("AGENTS.md contains unsupported control characters")
        return value


class AgentsManagerError(RuntimeError):
    code = "agents_error"


class AgentsMutationConflictError(AgentsManagerError):
    code = "capabilities_conflict"


class AgentsUnavailableError(AgentsManagerError):
    code = "agents_unavailable"


class WorkspaceAgentsManager:
    def __init__(
        self,
        storage: Any,
        runtime_gate: Any = None,
        private_backup_root: Path | str | None = None,
        codex_home: Path | str | None = None,
    ) -> None:
        self.storage = storage
        self.runtime_gate = runtime_gate
        self.private_backup_root = Path(
            private_backup_root or (storage.root / "agent-backups")
        )
        self.codex_home = (
            Path(codex_home).absolute() if codex_home is not None else None
        )

    def read_global(self) -> dict[str, Any]:
        try:
            raw = self._read_global_bytes()
        except AgentsUnavailableError:
            raise
        if raw is None:
            return self._record(
                "global", exists=False, content="", backups=self._backups("global")
            )
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise AgentsUnavailableError() from None
        return self._record(
            "global", exists=True, content=content, backups=self._backups("global")
        )

    def write_global(self, content: str) -> dict[str, Any]:
        self._validate_content(content)
        raw = content.encode("utf-8")
        with self._mutation():
            previous = self._read_global_bytes()
            if previous is not None:
                self._backup("global", previous)
            self._atomic_write_global(raw)
        return self._record(
            "global", exists=True, content=content, backups=self._backups("global")
        )

    def delete_global(self) -> None:
        with self._mutation():
            previous = self._read_global_bytes()
            if previous is None:
                return
            self._backup("global", previous)
            try:
                self._global_path().unlink()
            except FileNotFoundError:
                return
            except OSError:
                raise AgentsUnavailableError() from None

    def read(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        locator = self._locator(project.root_path)
        try:
            with self.storage.workspace_boundary.open_regular_file(locator) as stream:
                raw = stream.read(MAX_AGENTS_BYTES + 1)
            if len(raw) > MAX_AGENTS_BYTES:
                raise AgentsUnavailableError()
            content = raw.decode("utf-8", errors="strict")
        except WorkspaceNotFoundError:
            return self._record(
                project_id, exists=False, content="", backups=self._backups(project_id)
            )
        except (UnicodeDecodeError, WorkspaceBoundaryError, OSError):
            raise AgentsUnavailableError() from None
        return self._record(
            project_id, exists=True, content=content, backups=self._backups(project_id)
        )

    def write(self, project_id: str, content: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._validate_content(content)
        raw = content.encode("utf-8")
        locator = self._locator(project.root_path)
        with self._mutation():
            previous = self._read_bytes(locator)
            if previous is not None:
                self._backup(project_id, previous)
            try:
                self.storage.workspace_boundary.atomic_write_bytes(locator, raw)
            except (WorkspaceBoundaryError, OSError):
                raise AgentsUnavailableError() from None
        return self._record(
            project_id, exists=True, content=content, backups=self._backups(project_id)
        )

    def delete(self, project_id: str) -> None:
        project = self._project(project_id)
        locator = self._locator(project.root_path)
        with self._mutation():
            previous = self._read_bytes(locator)
            if previous is None:
                return
            self._backup(project_id, previous)
            try:
                self.storage.workspace_boundary.unlink_regular_file(locator)
            except (WorkspaceBoundaryError, OSError):
                raise AgentsUnavailableError() from None

    def _project(self, project_id: str) -> Any:
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise AgentsUnavailableError()
        try:
            project = self.storage.load_project(project_id)
            if self.storage.workspace_boundary is None:
                raise AgentsUnavailableError()
            self.storage.workspace_boundary.resolve_relative(
                project.root_path,
                must_exist=True,
                kind="directory",
            )
            return project
        except ProjectNotFoundError:
            raise
        except (WorkspaceBoundaryError, OSError, ValueError):
            raise AgentsUnavailableError() from None

    def _locator(self, root_path: str) -> str:
        boundary = self.storage.workspace_boundary
        if boundary is None:
            raise AgentsUnavailableError()
        try:
            root = boundary.normalize(root_path, allow_root=True)
            return (
                "AGENTS.md" if root == "." else boundary.normalize(f"{root}/AGENTS.md")
            )
        except WorkspaceBoundaryError:
            raise AgentsUnavailableError() from None

    def _read_bytes(self, locator: str) -> bytes | None:
        try:
            with self.storage.workspace_boundary.open_regular_file(locator) as stream:
                raw = stream.read(MAX_AGENTS_BYTES + 1)
            if len(raw) > MAX_AGENTS_BYTES:
                raise AgentsUnavailableError()
            return raw
        except WorkspaceNotFoundError:
            return None
        except (WorkspaceBoundaryError, OSError):
            raise AgentsUnavailableError() from None

    @staticmethod
    def _validate_content(content: str) -> None:
        if not isinstance(content, str):
            raise AgentsUnavailableError()
        raw = content.encode("utf-8")
        if (
            len(raw) > MAX_AGENTS_BYTES
            or b"\x00" in raw
            or any(ord(char) < 32 and char not in "\r\n\t" for char in content)
        ):
            raise AgentsUnavailableError()

    def _global_path(self) -> Path:
        root = self.codex_home
        if root is None:
            raise AgentsUnavailableError()
        try:
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise OSError()
        except OSError:
            raise AgentsUnavailableError() from None
        return root / "AGENTS.md"

    def _read_global_bytes(self) -> bytes | None:
        path = self._global_path()
        try:
            info = path.lstat()
            if not info or path.is_symlink() or not path.is_file():
                raise OSError()
            flags = (
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            )
            descriptor = os.open(path, flags)
            try:
                chunks: list[bytes] = []
                total = 0
                while total <= MAX_AGENTS_BYTES:
                    chunk = os.read(
                        descriptor, min(64 * 1024, MAX_AGENTS_BYTES + 1 - total)
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
            finally:
                os.close(descriptor)
            if len(raw) > MAX_AGENTS_BYTES:
                raise OSError()
            return raw
        except FileNotFoundError:
            return None
        except OSError:
            raise AgentsUnavailableError() from None

    def _atomic_write_global(self, content: bytes) -> None:
        target = self._global_path()
        temporary = target.with_name(".AGENTS.md." + uuid4().hex + ".tmp")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            try:
                target.lstat()
                if target.is_symlink() or not target.is_file():
                    raise OSError()
            except FileNotFoundError:
                pass
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        except OSError:
            raise AgentsUnavailableError() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _record(
        self,
        project_id: str,
        *,
        exists: bool,
        content: str,
        backups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw = content.encode("utf-8")
        return {
            "project_id": project_id,
            "exists": exists,
            "content": content,
            "size_bytes": len(raw),
            "backups": backups,
        }

    def _private_root(self) -> Path:
        root = self.private_backup_root
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink() or not root.is_dir():
                raise OSError()
            os.chmod(root, 0o700)
        except OSError:
            raise AgentsUnavailableError() from None
        return root

    def _backup(self, project_id: str, content: bytes) -> None:
        if len(content) > MAX_AGENTS_BYTES:
            raise AgentsUnavailableError()
        root = self._private_root() / project_id
        temporary: Path | None = None
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            if root.is_symlink() or not root.is_dir():
                raise OSError()
            os.chmod(root, 0o700)
            name = (
                datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                + "-"
                + uuid4().hex[:10]
                + ".bak"
            )
            target = root / name
            temporary = root / ("." + name + ".tmp")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                offset = 0
                while offset < len(content):
                    offset += os.write(descriptor, content[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise AgentsUnavailableError() from None
        self._reap_backups(root)

    def _backups(self, project_id: str) -> list[dict[str, Any]]:
        try:
            root = self.private_backup_root / project_id
            if not root.is_dir() or root.is_symlink():
                return []
            files = [
                entry
                for entry in root.iterdir()
                if entry.is_file() and not entry.is_symlink() and entry.suffix == ".bak"
            ]
            files.sort(key=lambda entry: entry.name, reverse=True)
            return [
                {"backup_id": entry.name, "size_bytes": entry.stat().st_size}
                for entry in files[:MAX_BACKUPS]
            ]
        except OSError:
            return []

    def _reap_backups(self, root: Path) -> None:
        files = [
            entry
            for entry in root.iterdir()
            if entry.is_file() and not entry.is_symlink() and entry.suffix == ".bak"
        ]
        files.sort(key=lambda entry: entry.name, reverse=True)
        total = 0
        for index, entry in enumerate(files):
            try:
                size = entry.stat().st_size
                if index >= MAX_BACKUPS or total + size > MAX_BACKUP_BYTES:
                    entry.unlink(missing_ok=True)
                else:
                    total += size
            except OSError:
                continue

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        lease = None
        if self.runtime_gate is not None:
            try:
                lease = self.runtime_gate.acquire_config_mutation()
            except RuntimeGateError:
                raise AgentsMutationConflictError() from None
        try:
            yield
        finally:
            if lease is not None:
                lease.release()


def _manager(request: Request) -> WorkspaceAgentsManager:
    manager = getattr(request.app.state, "agents_manager", None)
    if not isinstance(manager, WorkspaceAgentsManager):
        raise AgentsUnavailableError()
    return manager


def _auth(request: Request, authorization: str | None) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )


def _project_error(error: Exception) -> HTTPException:
    if isinstance(error, ProjectNotFoundError):
        return HTTPException(status_code=404, detail={"code": "not_found"})
    if isinstance(error, AgentsMutationConflictError):
        return HTTPException(status_code=409, detail={"code": error.code})
    return HTTPException(
        status_code=503, detail={"code": getattr(error, "code", "agents_unavailable")}
    )


@router.get("/agents/global")
def get_global_agents(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    try:
        return _manager(request).read_global()
    except AgentsManagerError as error:
        raise _project_error(error) from error


@router.put("/agents/global")
def put_global_agents(
    payload: AgentsWriteRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    try:
        return _manager(request).write_global(payload.content)
    except AgentsManagerError as error:
        raise _project_error(error) from error


@router.delete("/agents/global", status_code=status.HTTP_204_NO_CONTENT)
def delete_global_agents(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    _auth(request, authorization)
    try:
        _manager(request).delete_global()
    except AgentsManagerError as error:
        raise _project_error(error) from error


@router.get("/projects/{project_id}/agents")
def get_agents(
    project_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    try:
        return _manager(request).read(project_id)
    except (ProjectNotFoundError, AgentsManagerError) as error:
        raise _project_error(error) from error


@router.put("/projects/{project_id}/agents")
def put_agents(
    project_id: str,
    payload: AgentsWriteRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    try:
        return _manager(request).write(project_id, payload.content)
    except (ProjectNotFoundError, AgentsManagerError) as error:
        raise _project_error(error) from error


@router.delete("/projects/{project_id}/agents", status_code=status.HTTP_204_NO_CONTENT)
def delete_agents(
    project_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    _auth(request, authorization)
    try:
        _manager(request).delete(project_id)
    except (ProjectNotFoundError, AgentsManagerError) as error:
        raise _project_error(error) from error
