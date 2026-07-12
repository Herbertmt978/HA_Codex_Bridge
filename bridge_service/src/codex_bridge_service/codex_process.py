import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path


_PLATFORM_PATH_VARIABLES = ("SYSTEMROOT", "WINDIR", "COMSPEC")
_TEMPORARY_DIRECTORY_VARIABLES = ("TMPDIR", "TMP", "TEMP")
_LOCALE_VARIABLES = ("LANG", "LC_ALL", "LC_CTYPE")
_LOCALE_PATTERN = re.compile(
    r"(?:(?:C|POSIX)(?:\.[A-Za-z0-9][A-Za-z0-9_-]{0,15})?|"
    r"[A-Za-z]{2,3}(?:_(?:[A-Za-z]{2}|[0-9]{3}))?"
    r"(?:\.[A-Za-z0-9][A-Za-z0-9_-]{0,15})?"
    r"(?:@[A-Za-z0-9][A-Za-z0-9_-]{0,15})?)\Z"
)
_PATHEXT_PATTERN = re.compile(r"(?:\.[A-Za-z0-9]+)(?:;(?:\.[A-Za-z0-9]+))*\Z")
_URL_SCHEME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")
_CREDENTIAL_CARRIER_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_=-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])sk-(?:proj-)?[-A-Za-z0-9._~]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_-])eyJ[-A-Za-z0-9_]{5,}"
        r"\.[-A-Za-z0-9_]{5,}\.[-A-Za-z0-9_]{5,}(?![-A-Za-z0-9_])",
    ),
    re.compile(
        r"(?<![A-Za-z0-9])bearer(?:\s+|\s*[:=]\s*)[-A-Za-z0-9._~]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])pat[_-][-A-Za-z0-9._~]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?:"
        r"codex[_-]bridge|home[_-]?assistant|github|gitlab|openai|"
        r"bridge|hassio|gh|ha|ci|codex|supervisor"
        r")[_-](?:(?:auth|access|refresh|api|job)[_-])?"
        r"(?:token|secret|key|pat|password)\s*[:=]\s*[-A-Za-z0-9._~]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?:"
        r"api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|"
        r"cookie|session(?:id)?|password|passwd|client[_-]secret|"
        r"private[_-]key|pat"
        r")\s*[:=]\s*[-A-Za-z0-9._~]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
        re.IGNORECASE,
    ),
)


def resolve_codex_home(
    configured_home: Path | str | None,
    codex_command: str,
) -> Path:
    if configured_home:
        return Path(configured_home)

    inherited_home = os.environ.get("CODEX_HOME")
    if inherited_home:
        return Path(inherited_home)

    command_path = Path(codex_command)
    if command_path.suffix:
        for parent in command_path.parents:
            if parent.name == ".codex":
                return parent
    return Path.home() / ".codex"


def codex_command_prefix(codex_command: str) -> list[str]:
    target = Path(codex_command)
    suffix = target.suffix.lower()
    if suffix == ".ps1":
        return ["powershell", "-File", str(target)]
    if suffix == ".py":
        return [sys.executable, str(target)]
    return [str(target)]


def _is_plain_value(value: object, *, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and not any(pattern.search(value) for pattern in _CREDENTIAL_CARRIER_PATTERNS)
    )


def _is_safe_path(value: object, *, expected_kind: str | None = None) -> bool:
    if not _is_plain_value(value, maximum_length=4096):
        return False
    assert isinstance(value, str)
    if "://" in value:
        return False
    try:
        path = Path(value)
        if not path.is_absolute():
            return False
        if expected_kind == "file":
            return path.is_file()
        if expected_kind == "directory":
            return path.is_dir()
        return True
    except OSError:
        return False


def _safe_path_entries(value: object) -> list[str]:
    if not isinstance(value, str) or not value or len(value) > 32768:
        return []

    entries = value.split(os.pathsep)
    url_fragments: set[int] = set()
    if os.pathsep == ":":
        for index, entry in enumerate(entries[:-1]):
            if _URL_SCHEME_PATTERN.fullmatch(entry) and entries[index + 1].startswith("/"):
                url_fragments.update((index, index + 1))

    return [
        entry
        for index, entry in enumerate(entries)
        if index not in url_fragments and _is_safe_path(entry)
    ]


def codex_subprocess_environment(
    codex_home: Path | str | None = None,
    source_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if source_environment is None else source_environment
    environment: dict[str, str] = {}

    path_entries = _safe_path_entries(source.get("PATH"))
    if path_entries:
        environment["PATH"] = os.pathsep.join(path_entries)

    for name in _PLATFORM_PATH_VARIABLES:
        value = source.get(name)
        if _is_safe_path(value):
            environment[name] = value

    pathext = source.get("PATHEXT")
    if (
        _is_plain_value(pathext, maximum_length=256)
        and isinstance(pathext, str)
        and _PATHEXT_PATTERN.fullmatch(pathext)
    ):
        environment["PATHEXT"] = pathext

    dedicated_home = str(codex_home) if codex_home is not None else source.get("CODEX_HOME")
    if _is_safe_path(dedicated_home):
        environment["HOME"] = dedicated_home
        environment["CODEX_HOME"] = dedicated_home

    for name in _TEMPORARY_DIRECTORY_VARIABLES:
        value = source.get(name)
        if _is_safe_path(value):
            environment[name] = value

    for name in _LOCALE_VARIABLES:
        value = source.get(name)
        if (
            _is_plain_value(value, maximum_length=64)
            and isinstance(value, str)
            and _LOCALE_PATTERN.fullmatch(value)
        ):
            environment[name] = value

    certificate_file = source.get("SSL_CERT_FILE")
    if _is_safe_path(certificate_file, expected_kind="file"):
        environment["SSL_CERT_FILE"] = certificate_file

    certificate_directory = source.get("SSL_CERT_DIR")
    if _is_safe_path(certificate_directory, expected_kind="directory"):
        environment["SSL_CERT_DIR"] = certificate_directory

    return environment
