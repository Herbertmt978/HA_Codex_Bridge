#!/usr/bin/env python3
"""Keep the Home Assistant App release projections in sync.

``config.yaml`` is the authority for the App version.  The verified Codex
release lock is the authority for the Codex version and lock digest, while the
Bridge project metadata is the authority for the bundled Bridge version.  This
script projects those values into App release files; it never edits the
Integration or Bridge package metadata.

The default operation is a read-only check suitable for CI.  ``--bump-patch``
(``--update`` is an alias) increments the App patch version and atomically
replaces all four release projections after every input has passed validation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
APP_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReleaseSyncError(ValueError):
    """The release metadata is malformed or projections are out of sync."""


@dataclass(frozen=True)
class ManagedFile:
    path: Path
    raw: bytes
    text: str
    newline: str


@dataclass(frozen=True)
class ReleaseMetadata:
    app_version: str
    bridge_version: str
    codex_version: str
    lock_digest: str


def _regular_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseSyncError(f"{label} is not readable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseSyncError(f"{label} must be a regular, non-symlink file: {path}")
    return path


def _read_utf8(path: Path, label: str) -> tuple[bytes, str]:
    _regular_file(path, label)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseSyncError(f"{label} is not valid UTF-8: {path}") from exc
    if text.startswith("\ufeff") or "\x00" in text:
        raise ReleaseSyncError(f"{label} contains unsupported text data: {path}")
    return raw, text


def _read_text(path: Path, label: str, *, canonical_lf: bool = False) -> ManagedFile:
    raw, text = _read_utf8(path, label)
    has_crlf = "\r\n" in text
    without_crlf = text.replace("\r\n", "")
    has_lone_cr = "\r" in without_crlf
    has_lf = "\n" in text
    if has_lone_cr:
        raise ReleaseSyncError(f"{label} contains unsupported carriage returns: {path}")
    if has_crlf and has_lf and "\n" in without_crlf:
        raise ReleaseSyncError(f"{label} mixes newline styles: {path}")
    newline = "\r\n" if has_crlf else "\n"
    if canonical_lf and has_crlf:
        raise ReleaseSyncError(f"{label} must use canonical LF line endings: {path}")
    return ManagedFile(path, raw, text, newline)


def _validate_app_version(value: object, *, label: str = "App version") -> str:
    if not isinstance(value, str) or APP_VERSION_PATTERN.fullmatch(value) is None:
        raise ReleaseSyncError(f"{label} must be a stable X.Y.Z semver (prereleases are not allowed)")
    return value


def _version_line(text: str) -> tuple[int, re.Match[str]]:
    matches: list[tuple[int, re.Match[str]]] = []
    offset = 0
    pattern = re.compile(
        r"^(?P<prefix>version[ \t]*:[ \t]*)(?P<quote>['\"]?)(?P<value>[^#\r\n]*?)(?P=quote)(?P<suffix>[ \t]*(?:#.*)?)$"
    )
    for line in text.splitlines(keepends=True):
        body = line.removesuffix("\r\n").removesuffix("\n")
        match = pattern.fullmatch(body)
        if match:
            matches.append((offset, match))
        offset += len(line)
    if len(matches) != 1:
        raise ReleaseSyncError("App config must contain exactly one root version field")
    return matches[0]


def _app_version_from_config(path: Path) -> tuple[str, ManagedFile]:
    source = _read_text(path, "App config")
    _, match = _version_line(source.text)
    line_value = match.group("value").strip()
    configured = _validate_app_version(line_value)
    return configured, source


def _bridge_version_from_project(path: Path) -> str:
    _, text = _read_utf8(path, "Bridge project metadata")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseSyncError("Bridge project metadata is malformed TOML") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        raise ReleaseSyncError("Bridge project metadata is missing [project]")
    return _validate_app_version(
        project.get("version"), label="Bridge release version"
    )


def _load_release_lock(path: Path) -> tuple[str, str, ManagedFile]:
    source = _read_text(path, "Codex release lock", canonical_lf=True)
    try:
        data = json.loads(
            source.text,
            object_pairs_hook=dict,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseSyncError("Codex release lock is malformed JSON") from exc
    if not isinstance(data, dict):
        raise ReleaseSyncError("Codex release lock must contain a JSON object")
    # The lock writer uses this exact representation.  Comparing bytes makes
    # whitespace, key ordering, duplicate keys, and trailing data unambiguous.
    canonical = json.dumps(data, indent=2) + "\n"
    if source.text != canonical:
        raise ReleaseSyncError("Codex release lock is not canonical JSON")
    release = data.get("release")
    if not isinstance(release, dict):
        raise ReleaseSyncError("Codex release lock release metadata is missing")
    codex_version = _validate_app_version(release.get("version"), label="Codex release version")
    tag = release.get("tag")
    if tag != f"rust-v{codex_version}":
        raise ReleaseSyncError("Codex release lock tag does not match its version")
    # Reuse the existing strict release-lock validator when present.  This is
    # intentionally local and offline; no network or signature verification is
    # performed by the synchronizer.
    validator = ROOT / "scripts" / "update_codex_lock.py"
    if validator.is_file() and not validator.is_symlink():
        spec = importlib.util.spec_from_file_location("sync_app_release_lock", validator)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            try:
                module.validate_lock(data)
            except Exception as exc:  # validator exposes its own error type
                raise ReleaseSyncError("Codex release lock failed strict validation") from exc
    digest = hashlib.sha256(source.raw).hexdigest()
    if SHA256_PATTERN.fullmatch(digest) is None:  # defensive, hashlib is fixed-width
        raise ReleaseSyncError("Codex release lock digest is invalid")
    return codex_version, digest, source


def _paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    app = root / "codex_bridge_app"
    return (
        app / "config.yaml",
        app / "codex-release.json",
        app / "Dockerfile",
        app / "rootfs" / "etc" / "s6-overlay" / "s6-rc.d" / "codex-bridge" / "run",
        app / "CHANGELOG.md",
    )


def _changelog_version(text: str) -> str:
    headings = re.findall(r"(?m)^## ([0-9]+\.[0-9]+\.[0-9]+)\s*$", text)
    if not headings:
        raise ReleaseSyncError("App changelog must contain a stable release heading")
    return headings[0]


def _changelog_projected(
    source: ManagedFile,
    *,
    app_version: str,
    bridge_version: str,
    codex_version: str,
) -> str:
    current = _changelog_version(source.text)
    if current == app_version:
        raise ReleaseSyncError("App changelog already contains the next App version")
    marker = f"## {current}"
    if source.text.count(marker) != 1:
        raise ReleaseSyncError("App changelog release heading is ambiguous")
    entry = (
        f"## {app_version}{source.newline}{source.newline}"
        f"- Updates the Sigstore-verified bundled Codex runtime to "
        f"`{codex_version}`.{source.newline}"
        f"- Keeps model and reasoning-level choices dynamically discovered from "
        f"that runtime.{source.newline}"
        f"- Bundles Bridge `{bridge_version}` without changing its Integration "
        f"API compatibility."
        f"{source.newline}{source.newline}"
    )
    return source.text.replace(marker, entry + marker, 1)


def _replace_once(
    text: str,
    pattern: re.Pattern[str],
    value: str,
    label: str,
    *,
    check: bool = False,
) -> str:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ReleaseSyncError(f"{label} must occur exactly once (found {len(matches)})")
    match = matches[0]
    if check and match.group("value") != value:
        raise ReleaseSyncError(
            f"release projection drift in {label}: expected {value!r}, found {match.group('value')!r}"
        )
    if check:
        return text
    return text[: match.start("value")] + value + text[match.end("value") :]


def _projection_patterns(*, quoted: bool) -> tuple[tuple[str, re.Pattern[str]], ...]:
    fields = (
        ("App version", "CODEX_BRIDGE_APP_VERSION"),
        ("Bridge version", "CODEX_BRIDGE_VERSION"),
        ("Codex version", "CODEX_BRIDGE_CODEX_VERSION"),
        ("release lock digest", "CODEX_BRIDGE_RELEASE_LOCK_DIGEST"),
    )
    suffix = r'"' if quoted else r"(?=\s|\\|$)"
    value = r'[^"\r\n]*' if quoted else r"[^\s\\\r\n]+"
    return tuple(
        (
            label,
            re.compile(
                rf"(?P<prefix>\b{re.escape(key)}[ \t]*=[ \t]*){('(?P<open>\")' if quoted else '')}(?P<value>{value}){suffix}"
            ),
        )
        for label, key in fields
    )


def _projected(
    text: str,
    *,
    app_version: str,
    bridge_version: str,
    codex_version: str,
    lock_digest: str,
    dockerfile: bool,
    check: bool = False,
) -> str:
    expected = (app_version, bridge_version, codex_version, lock_digest)
    for (label, pattern), value in zip(_projection_patterns(quoted=dockerfile), expected):
        text = _replace_once(text, pattern, value, label, check=check)
    if dockerfile:
        text = _replace_once(
            text,
            re.compile(r"(?m)^[ \t]*--lock-digest[ \t]+(?P<value>[0-9a-f]+)(?=[ \t\\]*(?:\r?$))"),
            lock_digest,
            "Dockerfile --lock-digest",
            check=check,
        )
        text = _replace_once(
            text,
            re.compile(r'(?m)^[ \t]*io\.hass\.version[ \t]*=[ \t]*"(?P<value>[^"]*)"'),
            app_version,
            "Dockerfile io.hass.version",
            check=check,
        )
    return text


def _config_projected(source: ManagedFile, app_version: str) -> str:
    offset, match = _version_line(source.text)
    start = offset + match.start("value")
    end = offset + match.end("value")
    return source.text[:start] + app_version + source.text[end:]


def _atomic_replace(changes: Mapping[Path, bytes]) -> None:
    temporaries: dict[Path, Path] = {}
    try:
        for path, payload in changes.items():
            _regular_file(path, "managed release file")
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temporary = Path(name)
            temporaries[path] = temporary
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        for path, temporary in temporaries.items():
            os.replace(temporary, path)
        for directory in {path.parent for path in changes}:
            try:
                fd = os.open(directory, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except BaseException:
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)
        raise


def synchronize(root: Path, *, mode: str) -> ReleaseMetadata:
    config_path, lock_path, docker_path, run_path, changelog_path = _paths(root)
    app_version, config = _app_version_from_config(config_path)
    bridge_version = _bridge_version_from_project(root / "bridge_service" / "pyproject.toml")
    codex_version, lock_digest, lock = _load_release_lock(lock_path)
    docker = _read_text(docker_path, "App Dockerfile")
    run = _read_text(run_path, "App service run script")
    changelog = _read_text(changelog_path, "App changelog")
    if mode == "check":
        _projected(docker.text, app_version=app_version, bridge_version=bridge_version, codex_version=codex_version, lock_digest=lock_digest, dockerfile=True, check=True)
        _projected(run.text, app_version=app_version, bridge_version=bridge_version, codex_version=codex_version, lock_digest=lock_digest, dockerfile=False, check=True)
        if _changelog_version(changelog.text) != app_version:
            raise ReleaseSyncError("release projection drift in App changelog")
        return ReleaseMetadata(app_version, bridge_version, codex_version, lock_digest)
    match = APP_VERSION_PATTERN.fullmatch(app_version)
    assert match is not None
    next_version = f"{match.group(1)}.{match.group(2)}.{int(match.group(3)) + 1}"
    docker_text = _projected(docker.text, app_version=next_version, bridge_version=bridge_version, codex_version=codex_version, lock_digest=lock_digest, dockerfile=True)
    run_text = _projected(run.text, app_version=next_version, bridge_version=bridge_version, codex_version=codex_version, lock_digest=lock_digest, dockerfile=False)
    config_text = _config_projected(config, next_version)
    changelog_text = _changelog_projected(
        changelog,
        app_version=next_version,
        bridge_version=bridge_version,
        codex_version=codex_version,
    )
    _atomic_replace(
        {
            config.path: config_text.encode("utf-8"),
            docker.path: docker_text.encode("utf-8"),
            run.path: run_text.encode("utf-8"),
            changelog.path: changelog_text.encode("utf-8"),
        }
    )
    return ReleaseMetadata(next_version, bridge_version, codex_version, lock_digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: %(default)s)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_const", const="check", dest="mode", help="verify projections without writing (default)")
    modes.add_argument("--bump-patch", "--update", action="store_const", const="bump-patch", dest="mode", help="increment the App patch version and update projections")
    parser.set_defaults(mode="check")
    args = parser.parse_args(argv)
    try:
        metadata = synchronize(args.root.resolve(), mode=args.mode)
    except (OSError, ReleaseSyncError) as exc:
        print(f"release sync failed: {exc}", file=sys.stderr)
        return 1
    if args.mode == "check":
        print(
            "release projections are synchronized "
            f"(App {metadata.app_version}, Bridge {metadata.bridge_version}, "
            f"Codex {metadata.codex_version})"
        )
    else:
        print(
            f"updated App release to {metadata.app_version} "
            f"(Bridge {metadata.bridge_version}, Codex {metadata.codex_version})"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
