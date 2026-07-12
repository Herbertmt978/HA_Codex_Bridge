import errno
import os
import re
import stat
from pathlib import Path
from typing import BinaryIO, Literal


_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_ERROR_MESSAGES = {
    "invalid_relative_path": "The path is invalid.",
    "path_escape": "The workspace boundary rejected the path.",
    "not_found": "The workspace entry was not found.",
    "wrong_type": "The workspace entry has an unsupported type.",
    "already_exists": "The workspace entry already exists.",
}


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


class WorkspaceBoundary:
    """Confines trusted filesystem operations to one explicit directory root."""

    def __init__(self, root: Path | str, *, create: bool = False) -> None:
        try:
            raw_root = os.fspath(root)
        except TypeError as exc:
            raise WorkspaceInputError() from exc
        if not isinstance(raw_root, str) or raw_root.startswith("~"):
            raise WorkspaceInputError()

        candidate = Path(raw_root)
        if not candidate.is_absolute():
            raise WorkspaceInputError()
        try:
            if create:
                candidate.mkdir(parents=True, exist_ok=True)
            root_stat = candidate.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or self._is_junction(candidate):
                raise WorkspaceEscapeError()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise WorkspaceTypeError()
            self._root = candidate.resolve(strict=True)
        except WorkspaceBoundaryError:
            raise
        except FileNotFoundError as exc:
            raise WorkspaceNotFoundError() from exc
        except OSError as exc:
            raise WorkspaceTypeError() from exc

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return "WorkspaceBoundary()"

    def normalize(self, relative: Path | str, *, allow_root: bool = False) -> str:
        try:
            value = os.fspath(relative)
        except TypeError as exc:
            raise WorkspaceInputError() from exc
        if not isinstance(value, str) or not value or value != value.strip():
            raise WorkspaceInputError()
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
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
        except TypeError as exc:
            raise WorkspaceInputError() from exc
        if not candidate.is_absolute():
            raise WorkspaceInputError()
        try:
            lexical_relative = candidate.relative_to(self._root)
        except ValueError as exc:
            raise WorkspaceEscapeError() from exc
        self._validate_candidate(candidate, must_exist=True, kind=None)
        if not lexical_relative.parts:
            return "."
        return self.normalize(lexical_relative.as_posix())

    def create_directory(self, relative: Path | str) -> str:
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        if self._supports_secure_dir_fd():
            self._create_directory_posix(parts)
        else:
            current = self._root
            for part in parts:
                current = current / part
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise self._translated_os_error(exc) from exc
                self._validate_candidate(current, must_exist=True, kind="directory")
        return normalized

    def list_directories(self, relative: Path | str = ".") -> tuple[str, ...]:
        base = self.resolve_relative(relative, must_exist=True, kind="directory")
        prefix = self.relative_from_path(base)
        discovered: list[str] = []
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    entry_path = Path(entry.path)
                    if self._is_junction(entry_path):
                        continue
                    public = entry.name if prefix == "." else f"{prefix}/{entry.name}"
                    discovered.append(self.normalize(public))
        except WorkspaceBoundaryError:
            raise
        except OSError as exc:
            raise WorkspaceTypeError() from exc
        return tuple(sorted(discovered, key=str.casefold))

    def walk_regular_files(self, relative: Path | str = ".") -> tuple[str, ...]:
        base = self.resolve_relative(relative, must_exist=True, kind="directory")
        files: list[str] = []
        self._walk_regular_files(base, files)
        return tuple(sorted(files, key=str.casefold))

    def open_regular_file(self, relative: Path | str) -> BinaryIO:
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        self.resolve_relative(normalized, must_exist=True, kind="file")
        if self._supports_secure_dir_fd():
            parent_fd = self._open_parent_fd(parts[:-1])
            try:
                flags = os.O_RDONLY | self._nofollow_flag() | self._cloexec_flag()
                try:
                    file_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
                except OSError as exc:
                    raise self._translated_os_error(exc, symlink_is_escape=True) from exc
            finally:
                os.close(parent_fd)
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise WorkspaceTypeError()
                return os.fdopen(file_fd, "rb")
            except Exception:
                os.close(file_fd)
                raise

        candidate = self.resolve_relative(normalized, must_exist=True, kind="file")
        try:
            file_fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise WorkspaceTypeError()
            if candidate.resolve(strict=True).is_relative_to(self._root) is False:
                raise WorkspaceEscapeError()
            return os.fdopen(file_fd, "rb")
        except WorkspaceBoundaryError:
            if "file_fd" in locals():
                os.close(file_fd)
            raise
        except OSError as exc:
            if "file_fd" in locals():
                os.close(file_fd)
            raise self._translated_os_error(exc, symlink_is_escape=True) from exc

    def create_file_exclusive(self, relative: Path | str) -> BinaryIO:
        normalized = self.normalize(relative)
        parts = tuple(normalized.split("/"))
        parent_relative = "/".join(parts[:-1]) or "."
        self.resolve_relative(parent_relative, must_exist=True, kind="directory")
        candidate = self._root.joinpath(*parts)
        self._validate_candidate(candidate, must_exist=False, kind=None)

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | self._nofollow_flag()
            | self._cloexec_flag()
            | getattr(os, "O_BINARY", 0)
        )
        parent_fd: int | None = None
        try:
            if self._supports_secure_dir_fd():
                parent_fd = self._open_parent_fd(parts[:-1])
                file_fd = os.open(parts[-1], flags, 0o600, dir_fd=parent_fd)
            else:
                file_fd = os.open(candidate, flags, 0o600)
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise WorkspaceTypeError()
            return os.fdopen(file_fd, "wb")
        except WorkspaceBoundaryError:
            if "file_fd" in locals():
                os.close(file_fd)
            raise
        except OSError as exc:
            if "file_fd" in locals():
                os.close(file_fd)
            raise self._translated_os_error(exc, symlink_is_escape=True) from exc
        finally:
            if parent_fd is not None:
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
        except ValueError as exc:
            raise WorkspaceEscapeError() from exc

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
            except OSError as exc:
                raise WorkspaceTypeError() from exc
            if stat.S_ISLNK(current_stat.st_mode) or self._is_junction(current):
                raise WorkspaceEscapeError()
            if index < len(relative_parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
                raise WorkspaceTypeError()
            final_stat = current_stat

        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            if must_exist:
                raise WorkspaceNotFoundError() from exc
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise WorkspaceTypeError() from exc
        if not resolved.is_relative_to(self._root):
            raise WorkspaceEscapeError()

        if must_exist and final_stat is None and candidate != self._root:
            raise WorkspaceNotFoundError()
        if candidate == self._root:
            try:
                final_stat = self._root.lstat()
            except OSError as exc:
                raise WorkspaceTypeError() from exc
        if final_stat is not None and not (
            stat.S_ISREG(final_stat.st_mode) or stat.S_ISDIR(final_stat.st_mode)
        ):
            raise WorkspaceTypeError()
        if kind == "file" and (final_stat is None or not stat.S_ISREG(final_stat.st_mode)):
            raise WorkspaceTypeError()
        if kind == "directory" and (final_stat is None or not stat.S_ISDIR(final_stat.st_mode)):
            raise WorkspaceTypeError()
        return candidate

    def _walk_regular_files(self, directory: Path, files: list[str]) -> None:
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name.casefold())
            for entry in ordered:
                entry_path = Path(entry.path)
                if entry.is_symlink() or self._is_junction(entry_path):
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise WorkspaceTypeError() from exc
                if stat.S_ISDIR(entry_stat.st_mode):
                    self._walk_regular_files(entry_path, files)
                elif stat.S_ISREG(entry_stat.st_mode):
                    files.append(self.relative_from_path(entry_path))
        except WorkspaceBoundaryError:
            raise
        except OSError as exc:
            raise WorkspaceTypeError() from exc

    def _create_directory_posix(self, parts: tuple[str, ...]) -> None:
        flags = os.O_RDONLY | self._directory_flag() | self._nofollow_flag() | self._cloexec_flag()
        try:
            current_fd = os.open(self._root, flags)
        except OSError as exc:
            raise self._translated_os_error(exc, symlink_is_escape=True) from exc
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise self._translated_os_error(exc, symlink_is_escape=True) from exc
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise self._translated_os_error(exc, symlink_is_escape=True) from exc
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

    def _open_parent_fd(self, parts: tuple[str, ...]) -> int:
        flags = os.O_RDONLY | self._directory_flag() | self._nofollow_flag() | self._cloexec_flag()
        try:
            current_fd = os.open(self._root, flags)
            for part in parts:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError as exc:
            if "current_fd" in locals():
                os.close(current_fd)
            raise self._translated_os_error(exc, symlink_is_escape=True) from exc

    def _supports_secure_dir_fd(self) -> bool:
        return os.name != "nt" and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd

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
        base = component.split(".", 1)[0].upper()
        return base not in _WINDOWS_RESERVED_NAMES

    @staticmethod
    def _is_junction(path: Path) -> bool:
        checker = getattr(path, "is_junction", None)
        if checker is None:
            return False
        try:
            return bool(checker())
        except OSError as exc:
            raise WorkspaceTypeError() from exc

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
        if symlink_is_escape and error.errno in {errno.ELOOP, errno.ENOTDIR}:
            return WorkspaceEscapeError()
        return WorkspaceTypeError()
