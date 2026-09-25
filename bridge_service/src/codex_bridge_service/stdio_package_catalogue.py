"""Immutable, image-owned stdio MCP package catalogue.

The catalogue is compiled into the Bridge wheel. Only the fixed image path is
accepted at runtime; administrator input selects an approved ID and revision,
never a filesystem path, command or package URL.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


PACKAGE_ROOT = Path("/opt/codex-stdio/packages")
REQUIRED_OWNER_UID = 0


class PackageVerificationError(RuntimeError):
    """The packaged revision cannot be trusted or used."""


@dataclass(frozen=True)
class PackageSpec:
    package_id: str
    revision: str
    title: str
    source: str
    licence: str
    python: str
    entrypoint: tuple[str, ...]
    tools: tuple[str, ...]
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class VerifiedPackage:
    package_id: str
    revision: str
    title: str
    source: str
    licence: str
    python: str
    entrypoint: tuple[str, ...]
    tools: tuple[str, ...]
    package_path: Path


_CATALOGUE: tuple[PackageSpec, ...] = (
    PackageSpec(
        package_id="bridge-time",
        revision="1.0.0",
        title="Time and timezone",
        source="https://github.com/Herbertmt978/HA_Codex_Bridge/tree/main/codex_bridge_app/stdio_packages/bridge-time",
        licence="MIT",
        python="3.14",
        entrypoint=("python", "-m", "codex_bridge_time"),
        tools=("get_current_time", "convert_time"),
        files=(
            ("codex_bridge_time/__init__.py", "6011dc190e49cb867ab008fcfb685114229abd5152a750be654d3b4c8645e6e0"),
            ("codex_bridge_time/__main__.py", "90e091e9913f5093fca88e8307d53913f9e874288618bbaef26ca31aa409d5fc"),
        ),
    ),
)


def catalogue() -> tuple[PackageSpec, ...]:
    """Return the complete set of image-approved package revisions."""

    return _CATALOGUE


def package_spec(package_id: str, revision: str) -> PackageSpec:
    if not isinstance(package_id, str) or not isinstance(revision, str):
        raise PackageVerificationError("unknown package revision")
    for spec in _CATALOGUE:
        if spec.package_id == package_id and spec.revision == revision:
            return spec
    raise PackageVerificationError("unknown package revision")


def packaged_revisions(package_id: str) -> tuple[str, ...]:
    """A later signed image can ship several revisions for safe rollback."""

    return tuple(spec.revision for spec in _CATALOGUE if spec.package_id == package_id)


def previous_revision(package_id: str, revision: str) -> str | None:
    revisions = packaged_revisions(package_id)
    try:
        index = revisions.index(revision)
    except ValueError as exc:
        raise PackageVerificationError("unknown package revision") from exc
    return revisions[index - 1] if index > 0 else None


def list_packages() -> list[dict[str, Any]]:
    """Safe administrator projection; no private image paths are disclosed."""

    for spec in _CATALOGUE:
        verify_package(spec.package_id, spec.revision)

    return [
        {
            "package_id": spec.package_id,
            "revision": spec.revision,
            "title": spec.title,
            "source": spec.source,
            "licence": spec.licence,
            "python": spec.python,
            "tools": list(spec.tools),
            "digest": hashlib.sha256(
                (json.dumps(manifest_for(spec), sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest(),
            "entrypoint": list(spec.entrypoint),
            "network": "none",
            "files": "none",
            "environment": [],
            "rollback_available": previous_revision(spec.package_id, spec.revision) is not None,
        }
        for spec in _CATALOGUE
    ]


def manifest_for(spec: PackageSpec) -> dict[str, Any]:
    """Canonical on-image manifest, entirely determined by the wheel lock."""

    return {
        "schema_version": 1,
        "package_id": spec.package_id,
        "revision": spec.revision,
        "title": spec.title,
        "source": spec.source,
        "licence": spec.licence,
        "python": spec.python,
        "entrypoint": list(spec.entrypoint),
        "tools": list(spec.tools),
        "files": [{"path": path, "sha256": digest} for path, digest in spec.files],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PackageVerificationError("package directory is unavailable") from exc
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise PackageVerificationError("package directory is unsafe")
    if details.st_uid != REQUIRED_OWNER_UID or (
        os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o555
    ):
        raise PackageVerificationError("package directory ownership or mode is unsafe")


def _check_file(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise PackageVerificationError("package file is unavailable") from exc
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise PackageVerificationError("package contains a non-regular file")
    if details.st_uid != REQUIRED_OWNER_UID or (
        os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o444
    ):
        raise PackageVerificationError("package file ownership or mode is unsafe")
    if details.st_size > 512 * 1024:
        raise PackageVerificationError("package file exceeds its size limit")


def verify_package(package_id: str, revision: str) -> VerifiedPackage:
    """Check every byte and entry before a worker can mount an image package."""

    spec = package_spec(package_id, revision)
    root = PACKAGE_ROOT
    # All descendants are fixed by a compiled-in package ID/revision. Check
    # each component before resolving to avoid following a swapped symlink.
    for directory in (root, root / spec.package_id, root / spec.package_id / spec.revision):
        _check_directory(directory)
    package_path = root / spec.package_id / spec.revision
    expected = {path: digest for path, digest in spec.files}
    expected["manifest.json"] = None
    expected_directories = {
        str(parent).replace("\\", "/")
        for path in expected
        for parent in Path(path).parents
        if str(parent) != "."
    }
    found: set[str] = set()
    try:
        for current, directories, files in os.walk(package_path, followlinks=False):
            current_path = Path(current)
            _check_directory(current_path)
            for name in directories:
                directory = current_path / name
                if directory.relative_to(package_path).as_posix() not in expected_directories:
                    raise PackageVerificationError("package contains an undeclared directory")
                _check_directory(directory)
            for name in files:
                candidate = current_path / name
                _check_file(candidate)
                relative = candidate.relative_to(package_path).as_posix()
                if relative not in expected:
                    raise PackageVerificationError("package contains an undeclared file")
                found.add(relative)
                if relative == "manifest.json":
                    try:
                        actual = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise PackageVerificationError("package manifest is invalid") from exc
                    if actual != manifest_for(spec):
                        raise PackageVerificationError("package manifest differs from the image lock")
                elif _sha256_file(candidate) != expected[relative]:
                    raise PackageVerificationError("package file digest differs from the image lock")
    except OSError as exc:
        raise PackageVerificationError("package cannot be inspected") from exc
    if found != set(expected):
        raise PackageVerificationError("package is incomplete")
    try:
        resolved = package_path.resolve(strict=True)
    except OSError as exc:
        raise PackageVerificationError("package directory is unavailable") from exc
    if resolved != package_path:
        raise PackageVerificationError("package path is not fixed")
    return VerifiedPackage(
        package_id=spec.package_id,
        revision=spec.revision,
        title=spec.title,
        source=spec.source,
        licence=spec.licence,
        python=spec.python,
        entrypoint=spec.entrypoint,
        tools=spec.tools,
        package_path=resolved,
    )
