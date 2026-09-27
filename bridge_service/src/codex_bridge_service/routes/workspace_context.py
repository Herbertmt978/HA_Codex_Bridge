"""Bounded read-only browsing and excerpts for a thread's workspace."""

from __future__ import annotations

import hashlib
import os
import stat
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth import require_bridge_token
from ..models import RuntimeProfile
from ..storage import ThreadNotFoundError
from ..workspace_context import (
    WorkspaceContextReadResponse,
    WorkspaceContextReference,
    read_workspace_context_reference,
)
from ..workspace import (
    WorkspaceBoundary,
    WorkspaceBoundaryError,
    WorkspaceEscapeError,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspaceResourceLimitError,
    WorkspaceTypeError,
    WorkspaceUnsupportedError,
    normalize_portable_relative_path,
)

router = APIRouter()
__all__ = [
    "router",
    "WorkspaceContextReference",
    "WorkspaceContextReadResponse",
    "read_workspace_context_reference",
]

_MAX_DIRECTORY_DEPTH = 16
_MAX_DIRECTORY_ITEMS = 200
_MAX_DIRECTORY_SCAN = 500
_MAX_DIRECTORY_RESPONSE_BYTES = 64 * 1024
_MAX_PATH_BYTES = 2048
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_CONTEXT_BYTES = 32 * 1024
_MAX_CONTEXT_LINES = 200
_MAX_FULL_FILE_LINES = 400


class WorkspaceContextItem(BaseModel):
    """One safe entry in the current directory of the selected workspace."""

    name: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=_MAX_PATH_BYTES)
    kind: Literal["file", "directory"]
    size_bytes: int | None = Field(default=None, ge=0)
    selectable: bool


class WorkspaceContextListing(BaseModel):
    directory: str
    items: list[WorkspaceContextItem] = Field(max_length=_MAX_DIRECTORY_ITEMS)
    truncated: bool


class WorkspaceContextReadRequest(BaseModel):
    """Preview or revalidate one full-file / inclusive line-range selection."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=_MAX_PATH_BYTES)
    start_line: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    end_line: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_range(self) -> "WorkspaceContextReadRequest":
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("line range must include both endpoints")
        if self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("line range is reversed")
        if (
            self.start_line is not None
            and self.end_line - self.start_line + 1 > _MAX_CONTEXT_LINES
        ):
            raise ValueError("line range exceeds its limit")
        return self


class _ContextLimitError(Exception):
    pass


class _NotTextError(Exception):
    pass


class _ContextRangeError(Exception):
    pass


def _safe_path(value: str) -> str:
    """Normalise a portable path without accepting an absolute/root path."""
    try:
        normalized = normalize_portable_relative_path(value)
    except WorkspaceBoundaryError:
        raise WorkspaceInputError() from None
    if len(normalized.encode("utf-8")) > _MAX_PATH_BYTES:
        raise WorkspaceInputError()
    return normalized


def _workspace_locator(boundary: WorkspaceBoundary, workspace_path: str, relative: str) -> str:
    """Join a client path under the trusted workspace locator from ThreadRecord."""
    workspace = boundary.normalize(workspace_path, allow_root=True)
    client_path = boundary.normalize(relative, allow_root=True)
    if workspace == ".":
        result = client_path
    elif client_path == ".":
        result = workspace
    else:
        result = boundary.normalize(f"{workspace}/{client_path}")
    if result != "." and len(result.encode("utf-8")) > _MAX_PATH_BYTES + 512:
        raise WorkspaceInputError()
    return result


def _revision(path: str, file_stat: os.stat_result, content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(path.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str((file_stat.st_dev, file_stat.st_ino, file_stat.st_size,
                       file_stat.st_mtime_ns, file_stat.st_ctime_ns)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)
    return digest.hexdigest()


def _read_snapshot(
    boundary: WorkspaceBoundary, locator: str, display_path: str
) -> tuple[bytes, str]:
    """Read one no-follow regular file and detect mutation during the read."""
    try:
        with boundary.open_regular_file(locator) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise WorkspaceTypeError()
            if before.st_size > _MAX_SOURCE_BYTES:
                raise _ContextLimitError()
            content = stream.read(_MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
    except WorkspaceBoundaryError:
        raise
    except OSError:
        raise WorkspaceTypeError() from None
    if len(content) > _MAX_SOURCE_BYTES:
        raise _ContextLimitError()
    before_key = (before.st_dev, before.st_ino, before.st_size,
                  before.st_mtime_ns, before.st_ctime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)
    if before_key != after_key or len(content) != after.st_size:
        raise _ContextLimitError()
    return content, _revision(f"{locator}\0{display_path}", after, content)


def _format_read(
    path: str,
    content: bytes,
    revision: str,
    start_line: int | None,
    end_line: int | None,
) -> WorkspaceContextReadResponse:
    if b"\0" in content:
        raise _NotTextError()
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        raise _NotTextError() from None
    lines = decoded.splitlines(keepends=True)
    total_lines = len(lines)
    if start_line is None:
        if total_lines > _MAX_FULL_FILE_LINES or len(content) > _MAX_CONTEXT_BYTES:
            raise _ContextLimitError()
        selected = decoded
        resolved_start = 1 if total_lines else None
        resolved_end = total_lines or None
    else:
        assert end_line is not None
        if end_line > total_lines:
            raise _ContextRangeError()
        selected = "".join(lines[start_line - 1:end_line])
        if len(selected.encode("utf-8")) > _MAX_CONTEXT_BYTES:
            raise _ContextLimitError()
        resolved_start, resolved_end = start_line, end_line
    return WorkspaceContextReadResponse(
        status="ready",
        path=path,
        start_line=resolved_start,
        end_line=resolved_end,
        total_lines=total_lines,
        text=selected,
        content_revision=revision,
    )


def _raise_http(error: Exception) -> None:
    if isinstance(error, WorkspaceInputError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_workspace_context_path"}
        ) from None
    if isinstance(error, WorkspaceNotFoundError):
        raise HTTPException(
            status_code=404, detail={"code": "workspace_context_not_found"}
        ) from None
    if isinstance(error, WorkspaceEscapeError):
        raise HTTPException(
            status_code=400, detail={"code": "unsafe_workspace_context_entry"}
        ) from None
    if isinstance(error, (WorkspaceTypeError, WorkspaceUnsupportedError)):
        raise HTTPException(
            status_code=503, detail={"code": "workspace_context_unavailable"}
        ) from None
    if isinstance(error, WorkspaceResourceLimitError):
        raise HTTPException(
            status_code=413, detail={"code": "workspace_context_limit_exceeded"}
        ) from None
    if isinstance(error, _NotTextError):
        raise HTTPException(
            status_code=415, detail={"code": "workspace_context_not_text"}
        ) from None
    if isinstance(error, _ContextRangeError):
        raise HTTPException(
            status_code=422, detail={"code": "workspace_context_range_unavailable"}
        ) from None
    if isinstance(error, _ContextLimitError):
        raise HTTPException(
            status_code=413, detail={"code": "workspace_context_limit_exceeded"}
        ) from None
    raise error


def _thread_workspace(request: Request, thread_id: str) -> tuple[WorkspaceBoundary, str]:
    storage = request.app.state.storage
    if storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
        raise HTTPException(
            status_code=404, detail={"code": "workspace_context_unavailable"}
        )
    boundary = storage.workspace_boundary
    if not isinstance(boundary, WorkspaceBoundary):
        raise HTTPException(
            status_code=503, detail={"code": "workspace_context_unavailable"}
        )
    try:
        thread = storage.load_thread(thread_id)
        # This locator is persisted by Bridge for the thread; the caller never
        # supplies or selects a workspace root.
        boundary.normalize(thread.workspace_path, allow_root=True)
        return boundary, thread.workspace_path
    except ThreadNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "thread_not_found"}) from None
    except WorkspaceBoundaryError:
        raise HTTPException(
            status_code=503, detail={"code": "workspace_context_unavailable"}
        ) from None


@router.get(
    "/threads/{thread_id}/workspace-context",
    response_model=WorkspaceContextListing,
)
def list_workspace_context(
    thread_id: str,
    request: Request,
    response: Response,
    directory: str = Query(default=".", min_length=1, max_length=_MAX_PATH_BYTES),
    authorization: str | None = Header(default=None),
) -> WorkspaceContextListing:
    """List one bounded directory, relative to this thread's workspace."""
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    boundary, workspace_path = _thread_workspace(request, thread_id)
    try:
        safe_directory = "." if directory == "." else _safe_path(directory)
        if safe_directory != "." and len(safe_directory.split("/")) > _MAX_DIRECTORY_DEPTH:
            raise WorkspaceResourceLimitError("depth")
        locator = _workspace_locator(boundary, workspace_path, safe_directory)
        directory_fd = boundary.open_directory_fd(locator)
        items: list[WorkspaceContextItem] = []
        response_bytes = 0
        truncated = False
        scanned = 0
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > _MAX_DIRECTORY_SCAN or len(items) >= _MAX_DIRECTORY_ITEMS:
                        truncated = True
                        break
                    if entry.is_symlink():
                        raise WorkspaceEscapeError()
                    entry_stat = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(entry_stat.st_mode):
                        kind: Literal["file", "directory"] = "directory"
                        size_bytes = None
                    elif stat.S_ISREG(entry_stat.st_mode):
                        kind = "file"
                        size_bytes = int(entry_stat.st_size)
                    else:
                        raise WorkspaceTypeError()
                    child_path = entry.name if safe_directory == "." else f"{safe_directory}/{entry.name}"
                    child_path = _safe_path(child_path)
                    item_cost = len(child_path.encode("utf-8")) + len(kind) + 32
                    if response_bytes + item_cost > _MAX_DIRECTORY_RESPONSE_BYTES:
                        truncated = True
                        break
                    response_bytes += item_cost
                    items.append(WorkspaceContextItem(
                        name=entry.name,
                        path=child_path,
                        kind=kind,
                        size_bytes=size_bytes,
                        selectable=(
                            kind == "file" and size_bytes is not None
                            and size_bytes <= _MAX_SOURCE_BYTES
                        ),
                    ))
        finally:
            os.close(directory_fd)
        items.sort(key=lambda item: item.name.casefold())
        return WorkspaceContextListing(
            directory=safe_directory,
            items=items,
            truncated=truncated,
        )
    except HTTPException:
        raise
    except WorkspaceBoundaryError as error:
        _raise_http(error)
    except OSError:
        raise HTTPException(
            status_code=503, detail={"code": "workspace_context_unavailable"}
        ) from None


@router.post(
    "/threads/{thread_id}/workspace-context/read",
    response_model=WorkspaceContextReadResponse,
)
def read_workspace_context(
    thread_id: str,
    payload: WorkspaceContextReadRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> WorkspaceContextReadResponse:
    """Return a bounded visible preview and opaque revision for a selection."""
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    boundary, workspace_path = _thread_workspace(request, thread_id)
    try:
        path = _safe_path(payload.path)
        locator = _workspace_locator(boundary, workspace_path, path)
        content, revision = _read_snapshot(boundary, locator, path)
        if payload.expected_revision is not None and payload.expected_revision != revision:
            return WorkspaceContextReadResponse(
                status="stale", code="stale_context", path=path
            )
        return _format_read(
            path, content, revision, payload.start_line, payload.end_line
        )
    except WorkspaceEscapeError as error:
        if payload.expected_revision is not None:
            return WorkspaceContextReadResponse(
                status="stale", code="stale_context", path=payload.path
            )
        _raise_http(error)
    except (WorkspaceNotFoundError, WorkspaceTypeError):
        if payload.expected_revision is not None:
            return WorkspaceContextReadResponse(
                status="stale", code="stale_context", path=payload.path
            )
        raise HTTPException(
            status_code=404, detail={"code": "workspace_context_not_found"}
        ) from None
    except HTTPException:
        raise
    except _ContextLimitError:
        if payload.expected_revision is not None:
            return WorkspaceContextReadResponse(
                status="stale", code="stale_context", path=payload.path
            )
        raise HTTPException(
            status_code=413, detail={"code": "workspace_context_limit_exceeded"}
        ) from None
    except (WorkspaceBoundaryError, _NotTextError, _ContextRangeError) as error:
        _raise_http(error)
