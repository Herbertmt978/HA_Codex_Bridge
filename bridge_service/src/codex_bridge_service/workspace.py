import errno
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Literal


_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_SUPERSCRIPT_DIGITS = str.maketrans({"¹": "1", "²": "2", "³": "3"})
_ERROR_MESSAGES = {
    "invalid_relative_path": "The path is invalid.",
    "path_escape": "The workspace boundary rejected the path.",
    "not_found": "The workspace entry was not found.",
    "wrong_type": "The workspace entry has an unsupported type.",
    "already_exists": "The workspace entry already exists.",
    "secure_operations_unavailable": "Secure filesystem operations are unavailable.",
    "resource_limit": "The workspace operation exceeds a resource limit.",
}
_HAS_DIR_FD_PRIMITIVES = (
    os.name != "nt"
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.scandir in os.supports_fd
)


def _secure_dir_fd_available() -> bool:
    return (
        _HAS_DIR_FD_PRIMITIVES
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and bool(getattr(os, "O_DIRECTORY", 0))
    )


class WorkspaceBoundaryError(Exception):
    code = "workspace_error"

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGES.get(self.code, "The workspace operation failed."))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class WorkspaceInputError(WorkspaceBoundaryError, ValueError):
    code = "invalid_relative_path"


class WorkspaceEscapeError(WorkspaceBoundaryError, PermissionError):
    code = "path_escape"


class WorkspaceNotFoundError(WorkspaceBoundaryError, FileNotFoundError):
    code = "not_found"


class WorkspaceTypeError(WorkspaceBoundaryError, TypeError):
    code = "wrong_type"


class WorkspaceExistsError(WorkspaceBoundaryError, FileExistsError):
    code = "already_exists"


class WorkspaceUnsupportedError(WorkspaceBoundaryError, RuntimeError):
    code = "secure_operations_unavailable"


class WorkspaceResourceLimitError(WorkspaceBoundaryError, RuntimeError):
    code = "resource_limit"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, resource={self.resource!r})"


def normalize_portable_relative_path(
    relative: Path | str,
    *,
    allow_root: bool = False,
) -> str:
    """Apply the canonical cross-platform workspace name grammar."""

    try:
        value = os.fspath(relative)
    except TypeError:
        raise WorkspaceInputError() from None
    if not isinstance(value, str) or not value or value != value.strip():
        raise WorkspaceInputError()
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise WorkspaceInputError()
    if value == ".":
        if allow_root:
            return "."
        raise WorkspaceInputError()
    if "/" in value and "\\" in value:
        raise WorkspaceInputError()
    if "\\\\" in value:
        raise WorkspaceInputError()

    portable = value.replace("\\", "/")
    if (
        portable.startswith("/")
        or portable.endswith("/")
        or "//" in portable
        or _WINDOWS_DRIVE_PATTERN.match(portable)
        or ":" in portable
    ):
        raise WorkspaceInputError()

    parts = portable.split("/")
    if not parts or any(not _valid_portable_component(part) for part in parts):
        raise WorkspaceInputError()
    return "/".join(parts)


def _valid_portable_component(component: str) -> bool:
    if not component or component in {".", ".."}:
        return False
    if component != component.strip() or component.endswith((".", " ")):
        return False
    if len(component) > 255:
        return False
    if any(character in _WINDOWS_INVALID_CHARS for character in component):
        return False
    base = component.split(".", 1)[0].upper().translate(_WINDOWS_SUPERSCRIPT_DIGITS)
    return base not in _WINDOWS_RESERVED_NAMES


@dataclass(frozen=True, slots=True)
class WorkspaceFileIdentity:
    """Stable identity captured from an already-open regular file."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class WorkspaceTreeUsage:
    entry_count: int
    logical_bytes: int
    allocated_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceFileManifest:
    files: tuple[str, ...]
    usage: WorkspaceTreeUsage


@dataclass(frozen=True, slots=True)
class WorkspaceFilesystemSpace:
    filesystem_id: str
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceRegularFileStat:
    identity: WorkspaceFileIdentity
    size_bytes: int
    allocated_bytes: int


class WorkspaceAnonymousFileLease:
    """Read-only sealed anonymous file inherited by one child process."""

    def __init__(self, file_fd: int, size_bytes: int) -> None:
        self._file_fd: int | None = file_fd
        self._close_callback: Callable[[], None] | None = None
        self.size_bytes = size_bytes

    def fileno(self) -> int:
        if self._file_fd is None:
            raise WorkspaceNotFoundError()
        return self._file_fd

    @property
    def process_path(self) -> str:
        return f"/proc/self/fd/{self.fileno()}"

    def detach(self) -> int:
        """Transfer descriptor ownership to a file object or another caller."""
        if self._close_callback is not None:
            raise WorkspaceResourceLimitError("snapshot_lease")
        file_fd = self.fileno()
        self._file_fd = None
        return file_fd

    def set_close_callback(self, callback: Callable[[], None]) -> None:
        if self._close_callback is not None or self._file_fd is None:
            raise WorkspaceResourceLimitError("snapshot_lease")
        self._close_callback = callback

    def detach_with_close_callback(
        self,
    ) -> tuple[int, Callable[[], None] | None]:
        file_fd = self.fileno()
        callback = self._close_callback
        self._file_fd = None
        self._close_callback = None
        return file_fd, callback

    def close(self) -> None:
        file_fd = self._file_fd
        callback = self._close_callback
        self._file_fd = None
        self._close_callback = None
        try:
            if file_fd is not None:
                try:
                    os.close(file_fd)
                except OSError:
                    pass
        finally:
            if callback is not None:
                callback()

    def __enter__(self) -> "WorkspaceAnonymousFileLease":
        self.fileno()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class WorkspaceBoundary:
    """Confines filesystem operations to one explicit directory root.

    Race-safe I/O requires POSIX ``dir_fd`` and no-follow primitives. Platforms
    without them may validate names and snapshots, but mutating, reading, and
    enumerating operations fail closed with ``WorkspaceUnsupportedError``.
    """

    def __init__(self, root: Path | str, *, create: bool = False) -> None:
        self._root_fd: int | None = None
        try:
            raw_root = os.fspath(root)
        except TypeError:
            raise WorkspaceInputError() from None
        if not isinstance(raw_root, str) or raw_root.startswith("~"):
            raise WorkspaceInputError()

        candidate = Path(raw_root)
        if not candidate.is_absolute():
            raise WorkspaceInputError()
        candidate = Path(os.path.abspath(candidate))
        root_fd: int | None = None
        try:
            if _secure_dir_fd_available():
                if create and not candidate.exists():
                    root_fd = self._create_absolute_directory(candidate)
                else:
                    root_fd = self._open_absolute_directory(candidate)
            elif create and not candidate.exists():
                raise WorkspaceUnsupportedError()
            self._reject_link_components(candidate)
            root_stat = candidate.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or self._is_junction(candidate):
                raise WorkspaceEscapeError()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise WorkspaceTypeError()
            if root_fd is not None and not os.path.samestat(os.fstat(root_fd), root_stat):
                raise WorkspaceEscapeError()
            self._root = candidate
            self._root_fd = root_fd
        except WorkspaceBoundaryError:
            if root_fd is not None:
                os.close(root_fd)
            raise
        except FileNotFoundError:
            if root_fd is not None:
                os.close(root_fd)
            raise WorkspaceNotFoundError() from None
        except OSError:
            if root_fd is not None:
                os.close(root_fd)
            raise WorkspaceTypeError() from None

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return "WorkspaceBoundary()"

    def close(self) -> None:
        root_fd = self._root_fd
        self._root_fd = None
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def normalize(self, relative: Path | str, *, allow_root: bool = False) -> str:
        return normalize_portable_relative_path(relative, allow_root=allow_root)

    def resolve_relative(
        self,
        relative: Path | str,
        *,
        must_exist: bool = False,
        kind: Literal["file", "directory"] | None = None,
    ) -> Path:
        if kind not in (None, "file", "directory"):
            raise WorkspaceInputError()
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        candidate = self._root.joinpath(*parts)
        return self._validate_candidate(candidate, must_exist=must_exist, kind=kind)

    def open_directory_fd(self, relative: Path | str) -> int:
        """Lease a no-follow directory descriptor anchored below this root.

        The caller owns the returned descriptor and must close it. Unlike a
        resolved pathname, the descriptor continues to identify the opened
        directory if an ancestor or the final entry is replaced later.
        """
        self._require_secure_operations()
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        return self._open_parent_fd(parts)

    def relative_from_path(self, path: Path | str) -> str:
        try:
            candidate = Path(path)
        except TypeError:
            raise WorkspaceInputError() from None
        if not candidate.is_absolute():
            raise WorkspaceInputError()
        try:
            lexical_relative = candidate.relative_to(self._root)
        except ValueError:
            raise WorkspaceEscapeError() from None
        self._validate_candidate(candidate, must_exist=True, kind=None)
        if not lexical_relative.parts:
            return "."
        return self.normalize(lexical_relative.as_posix())

    def create_directory(self, relative: Path | str) -> str:
        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        self._create_directory_posix(parts)
        return normalized

    def remove_empty_directory(
        self,
        relative: Path | str,
        *,
        missing_ok: bool = False,
    ) -> None:
        """Remove one empty directory without following a replacement link."""

        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        try:
            parent_fd = self._open_parent_fd(parts[:-1])
        except WorkspaceNotFoundError:
            if missing_ok:
                return
            raise
        try:
            try:
                entry_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing_ok:
                    return
                raise WorkspaceNotFoundError() from None
            if stat.S_ISLNK(entry_stat.st_mode):
                raise WorkspaceEscapeError()
            if not stat.S_ISDIR(entry_stat.st_mode):
                raise WorkspaceTypeError()
            try:
                os.rmdir(parts[-1], dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileNotFoundError:
                if not missing_ok:
                    raise WorkspaceNotFoundError() from None
            except OSError as error:
                raise self._translated_entry_error(error, parts[-1], parent_fd) from None
        finally:
            os.close(parent_fd)

    def list_directories(self, relative: Path | str = ".") -> tuple[str, ...]:
        self._require_secure_operations()
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        directory_fd = self._open_parent_fd(parts)
        discovered: list[str] = []
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    child_fd: int | None = None
                    try:
                        child_fd = os.open(
                            entry.name,
                            self._directory_open_flags(),
                            dir_fd=directory_fd,
                        )
                        public = entry.name if normalized == "." else f"{normalized}/{entry.name}"
                        discovered.append(self.normalize(public))
                    except WorkspaceInputError:
                        continue
                    except OSError as error:
                        raise self._translated_entry_error(
                            error,
                            entry.name,
                            directory_fd,
                        ) from None
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
        except WorkspaceBoundaryError:
            raise
        except OSError:
            raise WorkspaceTypeError() from None
        finally:
            os.close(directory_fd)
        return tuple(sorted(discovered, key=str.casefold))

    def walk_regular_files(
        self,
        relative: Path | str = ".",
        *,
        reject_unsafe: bool = False,
    ) -> tuple[str, ...]:
        self._require_secure_operations()
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        directory_fd = self._open_parent_fd(parts)
        files: list[str] = []
        try:
            self._walk_regular_files_fd(
                directory_fd,
                normalized,
                files,
                reject_unsafe=reject_unsafe,
            )
        finally:
            os.close(directory_fd)
        return tuple(sorted(files, key=str.casefold))

    def manifest_regular_files(
        self,
        relative: Path | str = ".",
        *,
        reject_unsafe: bool = False,
        max_entries: int | None = None,
        max_bytes: int | None = None,
    ) -> WorkspaceFileManifest:
        return self._scan_regular_files(
            relative,
            reject_unsafe=reject_unsafe,
            max_entries=max_entries,
            max_bytes=max_bytes,
            collect_files=True,
        )

    def measure_regular_files(
        self,
        relative: Path | str = ".",
        *,
        reject_unsafe: bool = False,
        max_entries: int | None = None,
        max_bytes: int | None = None,
    ) -> WorkspaceTreeUsage:
        return self._scan_regular_files(
            relative,
            reject_unsafe=reject_unsafe,
            max_entries=max_entries,
            max_bytes=max_bytes,
            collect_files=False,
        ).usage

    def regular_file_stat(self, relative: Path | str) -> WorkspaceRegularFileStat:
        with self.open_regular_file(relative) as stream:
            file_stat = os.fstat(stream.fileno())
        return WorkspaceRegularFileStat(
            identity=WorkspaceFileIdentity(
                device=int(file_stat.st_dev),
                inode=int(file_stat.st_ino),
            ),
            size_bytes=int(file_stat.st_size),
            allocated_bytes=self._allocated_bytes(file_stat),
        )

    def open_readonly_duplicate(self, open_file: BinaryIO) -> BinaryIO:
        """Reopen one already-held regular inode read-only without its locator."""

        self._require_secure_operations()
        read_fd: int | None = None
        try:
            source_fd = open_file.fileno()
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise WorkspaceTypeError()
            read_fd = os.open(
                f"/proc/self/fd/{source_fd}",
                os.O_RDONLY | self._cloexec_flag(),
            )
            if not os.path.samestat(source_stat, os.fstat(read_fd)):
                raise WorkspaceEscapeError()
            return os.fdopen(read_fd, "rb")
        except WorkspaceBoundaryError:
            if read_fd is not None:
                os.close(read_fd)
            raise
        except (AttributeError, OSError, TypeError, ValueError):
            if read_fd is not None:
                os.close(read_fd)
            raise WorkspaceTypeError() from None

    def filesystem_space(self) -> WorkspaceFilesystemSpace:
        self._require_secure_operations()
        assert self._root_fd is not None
        root_fd = os.dup(self._root_fd)
        try:
            root_stat = os.fstat(root_fd)
            filesystem = os.fstatvfs(root_fd)
        except OSError:
            raise WorkspaceTypeError() from None
        finally:
            os.close(root_fd)
        fragment_size = int(filesystem.f_frsize or filesystem.f_bsize)
        return WorkspaceFilesystemSpace(
            filesystem_id=f"device:{int(root_stat.st_dev)}",
            total_bytes=int(filesystem.f_blocks) * fragment_size,
            free_bytes=int(filesystem.f_bavail) * fragment_size,
        )

    def _scan_regular_files(
        self,
        relative: Path | str,
        *,
        reject_unsafe: bool,
        max_entries: int | None,
        max_bytes: int | None,
        collect_files: bool,
    ) -> WorkspaceFileManifest:
        self._require_secure_operations()
        for value, resource in (
            (max_entries, "entries"),
            (max_bytes, "bytes"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise WorkspaceResourceLimitError(resource)
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        directory_fd = self._open_parent_fd(parts)
        files: list[str] | None = [] if collect_files else None
        state = [0, 0, 0]
        try:
            self._scan_regular_files_fd(
                directory_fd,
                normalized,
                files,
                state,
                reject_unsafe=reject_unsafe,
                max_entries=max_entries,
                max_bytes=max_bytes,
                depth=0,
            )
        finally:
            os.close(directory_fd)
        return WorkspaceFileManifest(
            files=tuple(sorted(files or (), key=str.casefold)),
            usage=WorkspaceTreeUsage(
                entry_count=state[0],
                logical_bytes=state[1],
                allocated_bytes=state[2],
            ),
        )

    def open_regular_file(self, relative: Path | str) -> BinaryIO:
        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        parent_fd = self._open_parent_fd(parts[:-1])
        try:
            try:
                file_fd = os.open(
                    parts[-1],
                    os.O_RDONLY
                    | self._nofollow_flag()
                    | self._cloexec_flag()
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise self._translated_entry_error(
                    error,
                    parts[-1],
                    parent_fd,
                ) from None
        finally:
            os.close(parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise WorkspaceTypeError()
            return os.fdopen(file_fd, "rb")
        except WorkspaceBoundaryError:
            os.close(file_fd)
            raise
        except (OSError, ValueError):
            os.close(file_fd)
            raise WorkspaceTypeError() from None

    def copy_regular_file_to_anonymous_lease(
        self,
        relative: Path | str,
        *,
        max_bytes: int | None = None,
    ) -> WorkspaceAnonymousFileLease:
        """Copy one confined file into a sealed pathless Linux file.

        The returned descriptor is reopened read-only after sealing. Its
        ``/proc/self/fd`` path has no traversable parent and does not reveal the
        private source locator. The caller owns and must close the lease.
        """
        self._require_secure_operations()
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
            raise WorkspaceResourceLimitError("bytes")
        creator = getattr(os, "memfd_create", None)
        cloexec = getattr(os, "MFD_CLOEXEC", 0)
        allow_sealing = getattr(os, "MFD_ALLOW_SEALING", 0)
        if creator is None or not cloexec or not allow_sealing:
            raise WorkspaceUnsupportedError()

        write_fd: int | None = None
        read_fd: int | None = None
        try:
            import fcntl

            seal_values = (
                getattr(fcntl, "F_ADD_SEALS", None),
                getattr(fcntl, "F_SEAL_SEAL", None),
                getattr(fcntl, "F_SEAL_SHRINK", None),
                getattr(fcntl, "F_SEAL_GROW", None),
                getattr(fcntl, "F_SEAL_WRITE", None),
            )
            if any(value is None for value in seal_values):
                raise WorkspaceUnsupportedError()
            add_seals, seal_seal, seal_shrink, seal_grow, seal_write = seal_values
            write_fd = creator(
                "codex-bridge-input",
                cloexec | allow_sealing,
            )
            size_bytes = 0
            with self.open_regular_file(relative) as source:
                source_stat = os.fstat(source.fileno())
                if max_bytes is not None and source_stat.st_size > max_bytes:
                    raise WorkspaceResourceLimitError("bytes")
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    if max_bytes is not None and size_bytes + len(chunk) > max_bytes:
                        raise WorkspaceResourceLimitError("bytes")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(write_fd, view)
                        if written <= 0:
                            raise WorkspaceTypeError()
                        view = view[written:]
                    size_bytes += len(chunk)
            os.lseek(write_fd, 0, os.SEEK_SET)
            fcntl.fcntl(
                write_fd,
                add_seals,
                seal_seal | seal_shrink | seal_grow | seal_write,
            )
            read_fd = os.open(
                f"/proc/self/fd/{write_fd}",
                os.O_RDONLY | self._cloexec_flag(),
            )
            if not stat.S_ISREG(os.fstat(read_fd).st_mode):
                raise WorkspaceTypeError()
            os.close(write_fd)
            write_fd = None
            return WorkspaceAnonymousFileLease(read_fd, size_bytes)
        except WorkspaceBoundaryError:
            raise
        except (ImportError, OSError, TypeError, ValueError):
            raise WorkspaceUnsupportedError() from None
        finally:
            if write_fd is not None:
                os.close(write_fd)
            if read_fd is not None and write_fd is not None:
                os.close(read_fd)

    def create_file_exclusive(self, relative: Path | str) -> BinaryIO:
        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | self._nofollow_flag()
            | self._cloexec_flag()
            | getattr(os, "O_BINARY", 0)
        )
        parent_fd = self._open_parent_fd(parts[:-1])
        try:
            try:
                file_fd = os.open(parts[-1], flags, 0o600, dir_fd=parent_fd)
            except OSError as error:
                raise self._translated_entry_error(
                    error,
                    parts[-1],
                    parent_fd,
                ) from None
        finally:
            os.close(parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise WorkspaceTypeError()
            return os.fdopen(file_fd, "wb")
        except WorkspaceBoundaryError:
            os.close(file_fd)
            raise
        except (OSError, ValueError):
            os.close(file_fd)
            raise WorkspaceTypeError() from None

    def atomic_write_bytes(self, relative: Path | str, content: bytes) -> None:
        """Durably replace one regular file without resolving outside root."""
        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        parent_fd = self._open_parent_fd(parts[:-1])
        temporary = f".{parts[-1]}.{os.urandom(12).hex()}.tmp"
        fd: int | None = None
        try:
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._cloexec_flag(),
                    0o600,
                    dir_fd=parent_fd,
                )
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write")
                    view = view[written:]
                os.fsync(fd)
                try:
                    existing = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISREG(existing.st_mode):
                        raise WorkspaceTypeError()
                except FileNotFoundError:
                    pass
                os.replace(temporary, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            except WorkspaceBoundaryError:
                raise
            except OSError as error:
                raise self._translated_entry_error(error, parts[-1], parent_fd) from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)

    def replace_regular_file(
        self,
        source: Path | str,
        target: Path | str,
        *,
        expected_identity: WorkspaceFileIdentity | None = None,
    ) -> None:
        """Atomically publish a caller-created private regular file."""
        self._require_secure_operations()
        source_name = self.normalize(source)
        target_name = self.normalize(target)
        source_parts = tuple(source_name.split("/"))
        target_parts = tuple(target_name.split("/"))
        source_parent = self._open_parent_fd(source_parts[:-1])
        target_parent = self._open_parent_fd(target_parts[:-1])
        try:
            source_stat = os.stat(source_parts[-1], dir_fd=source_parent, follow_symlinks=False)
            if not stat.S_ISREG(source_stat.st_mode):
                raise WorkspaceTypeError()
            if expected_identity is not None and self._identity_from_stat(source_stat) != expected_identity:
                raise WorkspaceEscapeError()
            try:
                target_stat = os.stat(target_parts[-1], dir_fd=target_parent, follow_symlinks=False)
                if not stat.S_ISREG(target_stat.st_mode):
                    raise WorkspaceTypeError()
            except FileNotFoundError:
                pass
            os.replace(source_parts[-1], target_parts[-1], src_dir_fd=source_parent, dst_dir_fd=target_parent)
            os.fsync(target_parent)
        except WorkspaceBoundaryError:
            raise
        except OSError as error:
            raise self._translated_entry_error(error, source_parts[-1], source_parent) from None
        finally:
            os.close(source_parent)
            os.close(target_parent)

    def identify_open_file(self, stream: BinaryIO) -> WorkspaceFileIdentity:
        """Capture the inode identity of a regular file lease."""
        self._require_secure_operations()
        try:
            entry_stat = os.fstat(stream.fileno())
        except (AttributeError, OSError, ValueError):
            raise WorkspaceTypeError() from None
        if not stat.S_ISREG(entry_stat.st_mode):
            raise WorkspaceTypeError()
        return WorkspaceFileIdentity(
            device=entry_stat.st_dev,
            inode=entry_stat.st_ino,
        )

    def validate_regular_file_identity(
        self,
        relative: Path | str,
        identity: WorkspaceFileIdentity,
    ) -> str:
        """Require a locator to still name the exact opened regular file."""
        normalized = self.normalize(relative)
        with self.open_regular_file(normalized) as stream:
            current = self.identify_open_file(stream)
        if current != identity:
            raise WorkspaceEscapeError()
        return normalized

    def validate_file_locator(
        self,
        relative: Path | str,
        *,
        must_exist: bool = False,
    ) -> str:
        """Validate a portable file locator without exposing the private root.

        Missing entries are permitted unless ``must_exist`` is requested. Any
        existing ancestor is still checked as a directory and the final entry,
        when present, must be a regular file. POSIX checks stay anchored to the
        retained root descriptor.
        """
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        if _secure_dir_fd_available() and self._root_fd is not None:
            self._validate_file_locator_fd(
                self._root_fd,
                parts,
                must_exist=must_exist,
            )
            return normalized

        current = self._root
        for index, part in enumerate(parts):
            current = current / part
            try:
                entry_stat = current.lstat()
            except FileNotFoundError:
                if must_exist:
                    raise WorkspaceNotFoundError() from None
                return normalized
            except OSError:
                raise WorkspaceTypeError() from None
            if stat.S_ISLNK(entry_stat.st_mode) or self._is_junction(current):
                raise WorkspaceEscapeError()
            if index < len(parts) - 1:
                if not stat.S_ISDIR(entry_stat.st_mode):
                    raise WorkspaceTypeError()
            elif not stat.S_ISREG(entry_stat.st_mode):
                raise WorkspaceTypeError()
        return normalized

    def validate_regular_file_at(self, directory_fd: int, relative: Path | str) -> str:
        """Validate a regular file below a caller-owned directory lease."""
        self._require_secure_operations()
        normalized = self.normalize(relative)
        self._validate_file_locator_fd(
            directory_fd,
            tuple(normalized.split("/")),
            must_exist=True,
        )
        return normalized

    def unlink_regular_file(
        self,
        relative: Path | str,
        *,
        missing_ok: bool = False,
        expected_identity: WorkspaceFileIdentity | None = None,
    ) -> None:
        """Unlink one regular file through the retained no-follow root.

        When an identity is supplied, a renamed or replaced entry is rejected
        instead of deleting a file the caller did not create.
        """
        self._require_secure_operations()
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        try:
            parent_fd = self._open_parent_fd(parts[:-1])
        except WorkspaceNotFoundError:
            if missing_ok:
                return
            raise
        file_fd: int | None = None
        try:
            try:
                entry_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing_ok:
                    return
                raise WorkspaceNotFoundError() from None
            except OSError as error:
                raise self._translated_entry_error(error, parts[-1], parent_fd) from None
            if stat.S_ISLNK(entry_stat.st_mode):
                raise WorkspaceEscapeError()
            if not stat.S_ISREG(entry_stat.st_mode):
                raise WorkspaceTypeError()
            try:
                file_fd = os.open(
                    parts[-1],
                    os.O_RDONLY
                    | self._nofollow_flag()
                    | self._cloexec_flag()
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise self._translated_entry_error(error, parts[-1], parent_fd) from None
            current_identity = self._identity_from_stat(os.fstat(file_fd))
            if expected_identity is not None and current_identity != expected_identity:
                raise WorkspaceEscapeError()
            try:
                os.unlink(parts[-1], dir_fd=parent_fd)
            except FileNotFoundError:
                if not missing_ok:
                    raise WorkspaceNotFoundError() from None
            except OSError as error:
                raise self._translated_entry_error(error, parts[-1], parent_fd) from None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(parent_fd)

    def _validate_candidate(
        self,
        candidate: Path,
        *,
        must_exist: bool,
        kind: Literal["file", "directory"] | None,
    ) -> Path:
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise WorkspaceEscapeError() from None

        relative_parts = candidate.relative_to(self._root).parts
        current = self._root
        final_stat: os.stat_result | None = None
        for index, part in enumerate(relative_parts):
            current = current / part
            try:
                current_stat = current.lstat()
            except FileNotFoundError:
                if must_exist or index < len(relative_parts) - 1:
                    raise WorkspaceNotFoundError() from None
                final_stat = None
                break
            except OSError:
                raise WorkspaceTypeError() from None
            if stat.S_ISLNK(current_stat.st_mode) or self._is_junction(current):
                raise WorkspaceEscapeError()
            if index < len(relative_parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
                raise WorkspaceTypeError()
            final_stat = current_stat

        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            if must_exist:
                raise WorkspaceNotFoundError() from None
            resolved = candidate.resolve(strict=False)
        except OSError:
            raise WorkspaceTypeError() from None
        if not resolved.is_relative_to(self._root):
            raise WorkspaceEscapeError()

        if must_exist and final_stat is None and candidate != self._root:
            raise WorkspaceNotFoundError()
        if candidate == self._root:
            try:
                final_stat = self._root.lstat()
            except OSError:
                raise WorkspaceTypeError() from None
        if final_stat is not None and not (
            stat.S_ISREG(final_stat.st_mode) or stat.S_ISDIR(final_stat.st_mode)
        ):
            raise WorkspaceTypeError()
        if kind == "file" and (final_stat is None or not stat.S_ISREG(final_stat.st_mode)):
            raise WorkspaceTypeError()
        if kind == "directory" and (final_stat is None or not stat.S_ISDIR(final_stat.st_mode)):
            raise WorkspaceTypeError()
        return candidate

    def _walk_regular_files_fd(
        self,
        directory_fd: int,
        prefix: str,
        files: list[str],
        *,
        reject_unsafe: bool,
    ) -> None:
        try:
            with os.scandir(directory_fd) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name.casefold())
            for entry in ordered:
                if entry.is_symlink():
                    if reject_unsafe:
                        raise WorkspaceEscapeError()
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    raise WorkspaceTypeError() from None
                public = entry.name if prefix == "." else f"{prefix}/{entry.name}"
                try:
                    public = self.normalize(public)
                except WorkspaceInputError:
                    if reject_unsafe:
                        raise
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    child_fd: int | None = None
                    try:
                        child_fd = os.open(
                            entry.name,
                            self._directory_open_flags(),
                            dir_fd=directory_fd,
                        )
                        self._walk_regular_files_fd(
                            child_fd,
                            public,
                            files,
                            reject_unsafe=reject_unsafe,
                        )
                    except OSError as error:
                        raise self._translated_entry_error(
                            error,
                            entry.name,
                            directory_fd,
                        ) from None
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
                elif stat.S_ISREG(entry_stat.st_mode):
                    file_fd: int | None = None
                    try:
                        file_fd = os.open(
                            entry.name,
                            os.O_RDONLY
                            | self._nofollow_flag()
                            | self._cloexec_flag()
                            | getattr(os, "O_NONBLOCK", 0),
                            dir_fd=directory_fd,
                        )
                        if stat.S_ISREG(os.fstat(file_fd).st_mode):
                            files.append(public)
                        elif reject_unsafe:
                            raise WorkspaceTypeError()
                    except OSError as error:
                        raise self._translated_entry_error(
                            error,
                            entry.name,
                            directory_fd,
                        ) from None
                    finally:
                        if file_fd is not None:
                            os.close(file_fd)
                elif reject_unsafe:
                    raise WorkspaceTypeError()
        except WorkspaceBoundaryError:
            raise
        except OSError:
            raise WorkspaceTypeError() from None

    def _scan_regular_files_fd(
        self,
        directory_fd: int,
        prefix: str,
        files: list[str] | None,
        state: list[int],
        *,
        reject_unsafe: bool,
        max_entries: int | None,
        max_bytes: int | None,
        depth: int,
    ) -> None:
        if depth > 64:
            raise WorkspaceResourceLimitError("depth")
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        if reject_unsafe:
                            raise WorkspaceEscapeError()
                        continue
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        raise WorkspaceTypeError() from None
                    public = entry.name if prefix == "." else f"{prefix}/{entry.name}"
                    try:
                        public = self.normalize(public)
                    except WorkspaceInputError:
                        if reject_unsafe:
                            raise
                        continue
                    if stat.S_ISDIR(entry_stat.st_mode):
                        child_fd: int | None = None
                        try:
                            child_fd = os.open(
                                entry.name,
                                self._directory_open_flags(),
                                dir_fd=directory_fd,
                            )
                            self._scan_regular_files_fd(
                                child_fd,
                                public,
                                files,
                                state,
                                reject_unsafe=reject_unsafe,
                                max_entries=max_entries,
                                max_bytes=max_bytes,
                                depth=depth + 1,
                            )
                        except OSError as error:
                            raise self._translated_entry_error(
                                error,
                                entry.name,
                                directory_fd,
                            ) from None
                        finally:
                            if child_fd is not None:
                                os.close(child_fd)
                    elif stat.S_ISREG(entry_stat.st_mode):
                        file_fd: int | None = None
                        try:
                            file_fd = os.open(
                                entry.name,
                                os.O_RDONLY
                                | self._nofollow_flag()
                                | self._cloexec_flag()
                                | getattr(os, "O_NONBLOCK", 0),
                                dir_fd=directory_fd,
                            )
                            opened_stat = os.fstat(file_fd)
                            if not stat.S_ISREG(opened_stat.st_mode):
                                if reject_unsafe:
                                    raise WorkspaceTypeError()
                                continue
                            next_entries = state[0] + 1
                            next_bytes = state[1] + int(opened_stat.st_size)
                            if max_entries is not None and next_entries > max_entries:
                                raise WorkspaceResourceLimitError("entries")
                            if max_bytes is not None and next_bytes > max_bytes:
                                raise WorkspaceResourceLimitError("bytes")
                            state[0] = next_entries
                            state[1] = next_bytes
                            state[2] += self._allocated_bytes(opened_stat)
                            if files is not None:
                                files.append(public)
                        except OSError as error:
                            raise self._translated_entry_error(
                                error,
                                entry.name,
                                directory_fd,
                            ) from None
                        finally:
                            if file_fd is not None:
                                os.close(file_fd)
                    elif reject_unsafe:
                        raise WorkspaceTypeError()
        except WorkspaceBoundaryError:
            raise
        except OSError:
            raise WorkspaceTypeError() from None

    @staticmethod
    def _allocated_bytes(file_stat: os.stat_result) -> int:
        blocks = getattr(file_stat, "st_blocks", None)
        if isinstance(blocks, int) and blocks >= 0:
            return blocks * 512
        return int(file_stat.st_size)

    @staticmethod
    def _identity_from_stat(entry_stat: os.stat_result) -> WorkspaceFileIdentity:
        return WorkspaceFileIdentity(
            device=entry_stat.st_dev,
            inode=entry_stat.st_ino,
        )

    def _validate_file_locator_fd(
        self,
        base_fd: int,
        parts: tuple[str, ...],
        *,
        must_exist: bool,
    ) -> None:
        try:
            current_fd = os.dup(base_fd)
        except OSError:
            raise WorkspaceTypeError() from None
        try:
            for index, part in enumerate(parts):
                try:
                    entry_stat = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                except FileNotFoundError:
                    if must_exist:
                        raise WorkspaceNotFoundError() from None
                    return
                except OSError as error:
                    raise self._translated_entry_error(error, part, current_fd) from None
                if stat.S_ISLNK(entry_stat.st_mode):
                    raise WorkspaceEscapeError()
                if index == len(parts) - 1:
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise WorkspaceTypeError()
                    try:
                        file_fd = os.open(
                            part,
                            os.O_RDONLY
                            | self._nofollow_flag()
                            | self._cloexec_flag()
                            | getattr(os, "O_NONBLOCK", 0),
                            dir_fd=current_fd,
                        )
                    except OSError as error:
                        raise self._translated_entry_error(error, part, current_fd) from None
                    try:
                        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                            raise WorkspaceTypeError()
                    finally:
                        os.close(file_fd)
                    return
                if not stat.S_ISDIR(entry_stat.st_mode):
                    raise WorkspaceTypeError()
                try:
                    next_fd = os.open(part, self._directory_open_flags(), dir_fd=current_fd)
                except OSError as error:
                    raise self._translated_entry_error(error, part, current_fd) from None
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

    def _create_absolute_directory(self, target: Path) -> int:
        anchor = Path(target.anchor)
        try:
            parts = target.relative_to(anchor).parts
            current_fd = os.open(anchor, self._directory_open_flags())
        except (OSError, ValueError):
            raise WorkspaceTypeError() from None
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise self._translated_entry_error(
                        error,
                        part,
                        current_fd,
                    ) from None
                try:
                    next_fd = os.open(
                        part,
                        self._directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as error:
                    raise self._translated_entry_error(
                        error,
                        part,
                        current_fd,
                    ) from None
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except WorkspaceBoundaryError:
            os.close(current_fd)
            raise

    def _open_absolute_directory(self, target: Path) -> int:
        anchor = Path(target.anchor)
        try:
            parts = target.relative_to(anchor).parts
            current_fd = os.open(anchor, self._directory_open_flags())
        except (OSError, ValueError):
            raise WorkspaceTypeError() from None
        try:
            for part in parts:
                try:
                    next_fd = os.open(
                        part,
                        self._directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as error:
                    raise self._translated_entry_error(
                        error,
                        part,
                        current_fd,
                    ) from None
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except WorkspaceBoundaryError:
            os.close(current_fd)
            raise

    def _reject_link_components(self, target: Path) -> None:
        anchor = Path(target.anchor)
        current = anchor
        try:
            parts = target.relative_to(anchor).parts
        except ValueError:
            raise WorkspaceInputError() from None
        for part in parts:
            current = current / part
            try:
                current_stat = current.lstat()
            except FileNotFoundError:
                raise WorkspaceNotFoundError() from None
            except OSError:
                raise WorkspaceTypeError() from None
            if stat.S_ISLNK(current_stat.st_mode) or self._is_junction(current):
                raise WorkspaceEscapeError()

    def _create_directory_posix(self, parts: tuple[str, ...]) -> None:
        flags = self._directory_open_flags()
        try:
            if self._root_fd is None:
                raise WorkspaceUnsupportedError()
            current_fd = os.dup(self._root_fd)
        except WorkspaceBoundaryError:
            raise
        except OSError:
            raise WorkspaceTypeError() from None
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise self._translated_entry_error(
                        error,
                        part,
                        current_fd,
                    ) from None
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as error:
                    raise self._translated_entry_error(
                        error,
                        part,
                        current_fd,
                    ) from None
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

    def _open_parent_fd(self, parts: tuple[str, ...]) -> int:
        flags = self._directory_open_flags()
        try:
            if self._root_fd is None:
                raise WorkspaceUnsupportedError()
            current_fd = os.dup(self._root_fd)
            for part in parts:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except WorkspaceBoundaryError:
            raise
        except OSError as error:
            translated = self._translated_os_error(error, symlink_is_escape=True)
            if "current_fd" in locals() and "part" in locals():
                translated = self._translated_entry_error(error, part, current_fd)
            if "current_fd" in locals():
                os.close(current_fd)
            raise translated from None

    def _require_secure_operations(self) -> None:
        if not _secure_dir_fd_available() or self._root_fd is None:
            raise WorkspaceUnsupportedError()

    def _directory_open_flags(self) -> int:
        return os.O_RDONLY | self._directory_flag() | self._nofollow_flag() | self._cloexec_flag()

    @staticmethod
    def _nofollow_flag() -> int:
        return getattr(os, "O_NOFOLLOW", 0)

    @staticmethod
    def _directory_flag() -> int:
        return getattr(os, "O_DIRECTORY", 0)

    @staticmethod
    def _cloexec_flag() -> int:
        return getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _valid_component(component: str) -> bool:
        return _valid_portable_component(component)

    @staticmethod
    def _is_junction(path: Path) -> bool:
        checker = getattr(path, "is_junction", None)
        if checker is None:
            return False
        try:
            return bool(checker())
        except OSError:
            raise WorkspaceTypeError() from None

    @classmethod
    def _translated_entry_error(
        cls,
        error: OSError,
        name: str,
        parent_fd: int,
    ) -> WorkspaceBoundaryError:
        translated = cls._translated_os_error(error, symlink_is_escape=True)
        if error.errno not in {errno.ENOTDIR, errno.EEXIST}:
            return translated
        try:
            entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            return translated
        if stat.S_ISLNK(entry_stat.st_mode):
            return WorkspaceEscapeError()
        return translated

    @staticmethod
    def _translated_os_error(
        error: OSError,
        *,
        symlink_is_escape: bool = False,
    ) -> WorkspaceBoundaryError:
        if isinstance(error, FileExistsError) or error.errno == errno.EEXIST:
            return WorkspaceExistsError()
        if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
            return WorkspaceNotFoundError()
        if symlink_is_escape and error.errno == errno.ELOOP:
            return WorkspaceEscapeError()
        return WorkspaceTypeError()
