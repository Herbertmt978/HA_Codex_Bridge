import errno
import os
import re
import stat
from pathlib import Path
from typing import BinaryIO, Literal


_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_ERROR_MESSAGES = {
    "invalid_relative_path": "The path is invalid.",
    "path_escape": "The workspace boundary rejected the path.",
    "not_found": "The workspace entry was not found.",
    "wrong_type": "The workspace entry has an unsupported type.",
    "already_exists": "The workspace entry already exists.",
    "secure_operations_unavailable": "Secure filesystem operations are unavailable.",
}
_HAS_DIR_FD_PRIMITIVES = (
    os.name != "nt"
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
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


class WorkspaceBoundary:
    """Confines filesystem operations to one explicit directory root.

    Race-safe I/O requires POSIX ``dir_fd`` and no-follow primitives. Platforms
    without them may validate names and snapshots, but mutating, reading, and
    enumerating operations fail closed with ``WorkspaceUnsupportedError``.
    """

    def __init__(self, root: Path | str, *, create: bool = False) -> None:
        try:
            raw_root = os.fspath(root)
        except TypeError:
            raise WorkspaceInputError() from None
        if not isinstance(raw_root, str) or raw_root.startswith("~"):
            raise WorkspaceInputError()

        candidate = Path(raw_root)
        if not candidate.is_absolute():
            raise WorkspaceInputError()
        try:
            if create and not candidate.exists():
                if not _secure_dir_fd_available():
                    raise WorkspaceUnsupportedError()
                self._create_absolute_directory(candidate)
            self._reject_link_components(candidate)
            root_stat = candidate.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or self._is_junction(candidate):
                raise WorkspaceEscapeError()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise WorkspaceTypeError()
            self._root = candidate.resolve(strict=True)
        except WorkspaceBoundaryError:
            raise
        except FileNotFoundError:
            raise WorkspaceNotFoundError() from None
        except OSError:
            raise WorkspaceTypeError() from None

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return "WorkspaceBoundary()"

    def normalize(self, relative: Path | str, *, allow_root: bool = False) -> str:
        try:
            value = os.fspath(relative)
        except TypeError:
            raise WorkspaceInputError() from None
        if not isinstance(value, str) or not value or value != value.strip():
            raise WorkspaceInputError()
        if any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        ):
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
        if not parts or any(not self._valid_component(part) for part in parts):
            raise WorkspaceInputError()
        return "/".join(parts)

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
                        raise self._translated_os_error(error, symlink_is_escape=True) from None
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

    def walk_regular_files(self, relative: Path | str = ".") -> tuple[str, ...]:
        self._require_secure_operations()
        normalized = self.normalize(relative, allow_root=True)
        parts = () if normalized == "." else tuple(normalized.split("/"))
        directory_fd = self._open_parent_fd(parts)
        files: list[str] = []
        try:
            self._walk_regular_files_fd(directory_fd, normalized, files)
        finally:
            os.close(directory_fd)
        return tuple(sorted(files, key=str.casefold))

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
                raise self._translated_os_error(error, symlink_is_escape=True) from None
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
                raise self._translated_os_error(error, symlink_is_escape=True) from None
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
                    raise WorkspaceNotFoundError()
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
    ) -> None:
        try:
            with os.scandir(directory_fd) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name.casefold())
            for entry in ordered:
                if entry.is_symlink():
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    raise WorkspaceTypeError() from None
                public = entry.name if prefix == "." else f"{prefix}/{entry.name}"
                try:
                    public = self.normalize(public)
                except WorkspaceInputError:
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    child_fd: int | None = None
                    try:
                        child_fd = os.open(
                            entry.name,
                            self._directory_open_flags(),
                            dir_fd=directory_fd,
                        )
                        self._walk_regular_files_fd(child_fd, public, files)
                    except OSError as error:
                        raise self._translated_os_error(
                            error,
                            symlink_is_escape=True,
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
                    except OSError as error:
                        raise self._translated_os_error(
                            error,
                            symlink_is_escape=True,
                        ) from None
                    finally:
                        if file_fd is not None:
                            os.close(file_fd)
        except WorkspaceBoundaryError:
            raise
        except OSError:
            raise WorkspaceTypeError() from None

    def _create_absolute_directory(self, target: Path) -> None:
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
                    raise self._translated_os_error(
                        error,
                        symlink_is_escape=True,
                    ) from None
                try:
                    next_fd = os.open(
                        part,
                        self._directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as error:
                    raise self._translated_os_error(
                        error,
                        symlink_is_escape=True,
                    ) from None
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

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
            current_fd = os.open(self._root, flags)
        except OSError as error:
            raise self._translated_os_error(error, symlink_is_escape=True) from None
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise self._translated_os_error(error, symlink_is_escape=True) from None
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as error:
                    raise self._translated_os_error(error, symlink_is_escape=True) from None
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

    def _open_parent_fd(self, parts: tuple[str, ...]) -> int:
        flags = self._directory_open_flags()
        try:
            current_fd = os.open(self._root, flags)
            for part in parts:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError as error:
            if "current_fd" in locals():
                os.close(current_fd)
            raise self._translated_os_error(error, symlink_is_escape=True) from None

    def _require_secure_operations(self) -> None:
        if not _secure_dir_fd_available():
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
        if not component or component in {".", ".."}:
            return False
        if component != component.strip() or component.endswith((".", " ")):
            return False
        if len(component) > 255:
            return False
        if any(character in _WINDOWS_INVALID_CHARS for character in component):
            return False
        base = component.split(".", 1)[0].upper()
        return base not in _WINDOWS_RESERVED_NAMES

    @staticmethod
    def _is_junction(path: Path) -> bool:
        checker = getattr(path, "is_junction", None)
        if checker is None:
            return False
        try:
            return bool(checker())
        except OSError:
            raise WorkspaceTypeError() from None

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
