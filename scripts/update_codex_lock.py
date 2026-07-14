#!/usr/bin/env python3
"""Verify and atomically update the immutable Codex release lock.

The updater intentionally never installs a release.  It accepts only a stable
``openai/codex`` rust-release tag, verifies every Linux musl archive and its
Sigstore bundle, and writes the lock only after all eight assets have passed.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping
from urllib.request import Request, urlopen


REPOSITORY = "openai/codex"
REPOSITORY_URL = "https://github.com/openai/codex"
LATEST_RELEASE_URL = "https://api.github.com/repos/openai/codex/releases/latest"
ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = ".github/workflows/rust-release.yml"
TRANSPARENCY_LOG = "rekor.sigstore.dev"
SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_TAR_OVERHEAD_BYTES = 1024 * 1024
LOCK_TIMEOUT_SECONDS = 30
TAG_PATTERN = re.compile(r"^rust-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
COMMIT_PATTERN = re.compile(r"^[a-f0-9]{40}$")
ARCHITECTURES = {"amd64": "x86_64", "aarch64": "aarch64"}
ELF_MACHINES = {"amd64": 62, "aarch64": 183}
COMPONENTS = ("codex", "bwrap")


class ReleaseLockError(ValueError):
    """The candidate release or lock cannot be trusted."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseLockError(f"{name} must be a JSON object")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseLockError(f"{name} must be a non-empty string")
    return value


def _positive_int(value: object, name: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ReleaseLockError(f"{name} must be between 1 and {maximum}")
    return value


def _sha256(value: object, name: str) -> str:
    digest = _string(value, name)
    if not SHA256_PATTERN.fullmatch(digest):
        raise ReleaseLockError(f"{name} must be a lowercase SHA-256 hex digest")
    return digest


def _commit(value: object, name: str) -> str:
    commit = _string(value, name)
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseLockError(f"{name} must be a lowercase 40-character commit SHA")
    return commit


def _tag_parts(tag: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ReleaseLockError("release tag must be an exact rust-vX.Y.Z semver tag")
    return tuple(int(part) for part in match.groups())


def _identity_for(tag: str) -> str:
    _tag_parts(tag)
    return f"{REPOSITORY_URL}/{WORKFLOW}@refs/tags/{tag}"


def _asset_name(component: str, target: str, suffix: str) -> str:
    return f"{component}-{target}-unknown-linux-musl{suffix}"


def _asset_url(tag: str, name: str) -> str:
    return f"{REPOSITORY_URL}/releases/download/{tag}/{name}"


def _release_url(tag: str) -> str:
    return f"{REPOSITORY_URL}/releases/tag/{tag}"


def _metadata_asset(
    asset: Mapping[str, Any], *, tag: str, name: str, maximum: int
) -> dict[str, Any]:
    if _string(asset.get("name"), "asset.name") != name:
        raise ReleaseLockError("asset name does not match the required target")
    expected_url = _asset_url(tag, name)
    if (
        _string(asset.get("browser_download_url"), "asset.browser_download_url")
        != expected_url
    ):
        raise ReleaseLockError(f"asset {name} has an unexpected download URL")
    digest = _string(asset.get("digest"), f"asset {name} digest")
    if not digest.startswith("sha256:"):
        raise ReleaseLockError(f"asset {name} must publish a SHA-256 digest")
    return {
        "name": name,
        "url": expected_url,
        "sha256": _sha256(digest.removeprefix("sha256:"), f"asset {name} digest"),
        "size": _positive_int(asset.get("size"), f"asset {name} size", maximum),
    }


def _required_assets(
    metadata: Mapping[str, Any], tag: str
) -> dict[str, dict[str, dict[str, Any]]]:
    raw_assets = metadata.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseLockError("release assets must be a list")
    by_name: dict[str, Mapping[str, Any]] = {}
    for raw_asset in raw_assets:
        asset = _mapping(raw_asset, "release asset")
        name = _string(asset.get("name"), "asset.name")
        if name in by_name:
            raise ReleaseLockError(f"duplicate release asset: {name}")
        by_name[name] = asset

    selected: dict[str, dict[str, dict[str, Any]]] = {}
    for arch, target in ARCHITECTURES.items():
        selected[arch] = {}
        for component in COMPONENTS:
            archive_name = _asset_name(component, target, ".tar.gz")
            bundle_name = _asset_name(component, target, ".sigstore")
            if archive_name not in by_name or bundle_name not in by_name:
                raise ReleaseLockError(f"missing required asset for {arch}/{component}")
            selected[arch][component] = {
                "archive": _metadata_asset(
                    by_name[archive_name],
                    tag=tag,
                    name=archive_name,
                    maximum=MAX_ARCHIVE_BYTES,
                ),
                "bundle": _metadata_asset(
                    by_name[bundle_name],
                    tag=tag,
                    name=bundle_name,
                    maximum=MAX_BUNDLE_BYTES,
                ),
            }
    return selected


def _validate_release_metadata(
    metadata: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, dict[str, Any]]]]:
    if _string(metadata.get("repository"), "repository") != REPOSITORY:
        raise ReleaseLockError(
            "release repository identity does not match openai/codex"
        )
    if metadata.get("draft") is not False:
        raise ReleaseLockError("draft releases are not eligible")
    if metadata.get("prerelease") is not False:
        raise ReleaseLockError("prereleases are not eligible")
    tag = _string(metadata.get("tag_name"), "tag_name")
    _tag_parts(tag)
    if _string(metadata.get("html_url"), "html_url") != _release_url(tag):
        raise ReleaseLockError("release URL does not match the exact openai/codex tag")
    _string(metadata.get("published_at"), "published_at")
    return tag, _required_assets(metadata, tag)


def _details_for(
    details: Mapping[str, Any], arch: str, component: str, name: str
) -> Mapping[str, Any]:
    by_arch = _mapping(details.get(arch), f"details.{arch}")
    detail = _mapping(by_arch.get(component), f"details.{arch}.{component}")
    if _string(detail.get("name"), f"details.{arch}.{component}.name") != name:
        raise ReleaseLockError(
            f"details for {arch}/{component} do not match the selected asset"
        )
    return detail


def build_lock_from_metadata(
    metadata: Mapping[str, Any],
    *,
    archive_details: Mapping[str, Any],
    bundle_details: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a complete lock after archive and Sigstore verification results exist."""
    tag, selected = _validate_release_metadata(metadata)
    commit = _commit(metadata.get("verified_tag_commit"), "verified release tag commit")
    release_id = _positive_int(metadata.get("id"), "release id", 2**63 - 1)
    identity = _identity_for(tag)
    assets: dict[str, Any] = {}
    for arch in ARCHITECTURES:
        assets[arch] = {}
        for component in COMPONENTS:
            archive = selected[arch][component]["archive"]
            bundle = selected[arch][component]["bundle"]
            archive_detail = _details_for(
                archive_details, arch, component, archive["name"]
            )
            bundle_detail = _details_for(
                bundle_details, arch, component, bundle["name"]
            )
            assets[arch][component] = {
                **archive,
                "decompressed_sha256": _sha256(
                    archive_detail.get("decompressed_sha256"),
                    f"{arch}/{component} decompressed digest",
                ),
                "decompressed_size": _positive_int(
                    archive_detail.get("decompressed_size"),
                    f"{arch}/{component} decompressed size",
                    MAX_DECOMPRESSED_BYTES,
                ),
                "sigstore": {
                    **bundle,
                    "format": _string(bundle_detail.get("format"), "bundle format"),
                    "issuer": _string(bundle_detail.get("issuer"), "bundle issuer"),
                    "identity": _string(
                        bundle_detail.get("identity"), "bundle identity"
                    ),
                    "transparency_log": _string(
                        bundle_detail.get("transparency_log"), "bundle transparency log"
                    ),
                    "log_id": _sha256(
                        bundle_detail.get("log_id"), "bundle transparency log ID"
                    ),
                    "log_index": _positive_int(
                        bundle_detail.get("log_index"),
                        "bundle transparency log index",
                        2**63 - 1,
                    ),
                    "integrated_time": _positive_int(
                        bundle_detail.get("integrated_time"),
                        "bundle transparency integrated time",
                        2**63 - 1,
                    ),
                    "signed_sha256": _sha256(
                        bundle_detail.get("signed_sha256"),
                        "bundle signed binary digest",
                    ),
                },
            }
    lock = {
        "schema_version": SCHEMA_VERSION,
        "repository": REPOSITORY,
        "release": {
            "tag": tag,
            "version": tag.removeprefix("rust-v"),
            "id": release_id,
            "commit": commit,
            "channel": "stable",
            "url": _release_url(tag),
            "published_at": metadata["published_at"],
        },
        "sigstore": {
            "issuer": ISSUER,
            "identity": identity,
            "transparency_log": TRANSPARENCY_LOG,
        },
        "assets": assets,
    }
    validate_lock(lock)
    return lock


def validate_lock(lock: Mapping[str, Any]) -> None:
    """Reject any lock that is incomplete, oversized, or identity-inconsistent."""
    if lock.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseLockError("unsupported lock schema version")
    if _string(lock.get("repository"), "repository") != REPOSITORY:
        raise ReleaseLockError("lock repository identity does not match openai/codex")
    release = _mapping(lock.get("release"), "release")
    tag = _string(release.get("tag"), "release.tag")
    _tag_parts(tag)
    if _string(release.get("version"), "release.version") != tag.removeprefix("rust-v"):
        raise ReleaseLockError("release version does not match the exact release tag")
    _positive_int(release.get("id"), "release.id", 2**63 - 1)
    _commit(release.get("commit"), "release.commit")
    if _string(release.get("channel"), "release.channel") != "stable":
        raise ReleaseLockError("release channel must be stable")
    if _string(release.get("url"), "release.url") != _release_url(tag):
        raise ReleaseLockError("release URL does not match release tag")
    _string(release.get("published_at"), "release.published_at")
    sigstore = _mapping(lock.get("sigstore"), "sigstore")
    if _string(sigstore.get("issuer"), "sigstore.issuer") != ISSUER:
        raise ReleaseLockError("Sigstore issuer is not GitHub Actions")
    if _string(sigstore.get("identity"), "sigstore.identity") != _identity_for(tag):
        raise ReleaseLockError("Sigstore workflow identity does not match release tag")
    if (
        _string(sigstore.get("transparency_log"), "sigstore.transparency_log")
        != TRANSPARENCY_LOG
    ):
        raise ReleaseLockError("Sigstore transparency log is not Rekor")
    assets = _mapping(lock.get("assets"), "assets")
    if set(assets) != set(ARCHITECTURES):
        raise ReleaseLockError("lock must contain exactly amd64 and aarch64 assets")
    for arch, target in ARCHITECTURES.items():
        by_component = _mapping(assets[arch], f"assets.{arch}")
        if set(by_component) != set(COMPONENTS):
            raise ReleaseLockError(f"lock must contain codex and bwrap for {arch}")
        for component in COMPONENTS:
            asset = _mapping(by_component[component], f"assets.{arch}.{component}")
            archive_name = _asset_name(component, target, ".tar.gz")
            if _string(asset.get("name"), "asset.name") != archive_name:
                raise ReleaseLockError("archive name does not match target")
            if _string(asset.get("url"), "asset.url") != _asset_url(tag, archive_name):
                raise ReleaseLockError("archive URL does not match target")
            _sha256(asset.get("sha256"), "asset.sha256")
            _positive_int(asset.get("size"), "asset.size", MAX_ARCHIVE_BYTES)
            _sha256(asset.get("decompressed_sha256"), "asset.decompressed_sha256")
            _positive_int(
                asset.get("decompressed_size"),
                "asset.decompressed_size",
                MAX_DECOMPRESSED_BYTES,
            )
            bundle = _mapping(asset.get("sigstore"), "asset.sigstore")
            bundle_name = _asset_name(component, target, ".sigstore")
            if _string(bundle.get("name"), "bundle.name") != bundle_name:
                raise ReleaseLockError("Sigstore bundle name does not match target")
            if _string(bundle.get("url"), "bundle.url") != _asset_url(tag, bundle_name):
                raise ReleaseLockError("Sigstore bundle URL does not match target")
            _sha256(bundle.get("sha256"), "bundle.sha256")
            _positive_int(bundle.get("size"), "bundle.size", MAX_BUNDLE_BYTES)
            if _string(bundle.get("format"), "bundle.format") != "cosign-legacy":
                raise ReleaseLockError("unsupported Sigstore bundle format")
            for key, expected in sigstore.items():
                if _string(bundle.get(key), f"bundle.{key}") != expected:
                    raise ReleaseLockError(
                        f"Sigstore bundle {key} does not match the release identity"
                    )
            _sha256(bundle.get("log_id"), "Sigstore transparency log ID")
            _positive_int(
                bundle.get("log_index"), "Sigstore transparency log index", 2**63 - 1
            )
            _positive_int(
                bundle.get("integrated_time"),
                "Sigstore transparency integrated time",
                2**63 - 1,
            )
            if (
                _sha256(bundle.get("signed_sha256"), "Sigstore signed binary digest")
                != asset["decompressed_sha256"]
            ):
                raise ReleaseLockError(
                    "Sigstore signed binary digest does not match extracted binary"
                )


def require_monotonic_upgrade(
    candidate: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> bool:
    if previous is None or previous.get("status") == "pending_verification":
        return True
    validate_lock(previous)
    validate_lock(candidate)
    current = _tag_parts(_mapping(previous["release"], "previous.release")["tag"])
    next_version = _tag_parts(
        _mapping(candidate["release"], "candidate.release")["tag"]
    )
    if next_version < current:
        raise ReleaseLockError("candidate release must be newer than the current lock")
    if next_version == current:
        if candidate != previous:
            raise ReleaseLockError(
                "same-version release does not exactly match the current lock"
            )
        return False
    return True


def _lock_path(path: Path) -> Path:
    """Return a stable OS-temp lock path without leaving repository sidecars."""
    canonical = os.path.normcase(str(path.expanduser().resolve()))
    identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "ha-codex-release-locks" / f"{identity}.lock"


@contextmanager
def _exclusive_lock(path: Path) -> Any:
    """Coordinate lock updates across processes on Windows and POSIX hosts."""
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ReleaseLockError(
                            "timed out waiting for release lock"
                        ) from exc
                    time.sleep(0.05)

            def release() -> None:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise ReleaseLockError(
                            "timed out waiting for release lock"
                        ) from exc
                    time.sleep(0.05)

            def release() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        try:
            yield
        finally:
            handle.seek(0)
            release()


def write_lock_atomically(path: Path, lock: Mapping[str, Any]) -> None:
    validate_lock(lock)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(lock, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def commit_lock_candidate(path: Path, candidate: Mapping[str, Any]) -> bool:
    """Re-read and compare under the process lock before replacing ``path``."""
    with _exclusive_lock(path):
        previous = _load_json(path) if path.exists() else None
        if require_monotonic_upgrade(candidate, previous):
            write_lock_atomically(path, candidate)
            return True
    return False


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_url(url: str, maximum: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ha-codex-bridge",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL is validated from GitHub metadata.
        chunks: list[bytes] = []
        size = 0
        while chunk := response.read(min(1024 * 1024, maximum + 1 - size)):
            size += len(chunk)
            if size > maximum:
                raise ReleaseLockError(f"download exceeds {maximum} byte limit")
            chunks.append(chunk)
        return b"".join(chunks)


class _BoundedReader:
    """Count decompressed gzip bytes before the USTAR parser can accept them."""

    def __init__(self, source: Any, maximum: int) -> None:
        self._source = source
        self._maximum = maximum
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._maximum - self.size
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        chunk = self._source.read(requested)
        self.size += len(chunk)
        if self.size > self._maximum:
            raise ReleaseLockError(
                "archive decompressed data exceeds the allowed bound"
            )
        return chunk


def _read_exact(source: _BoundedReader, size: int, error: str) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = source.read(remaining)
        if not chunk:
            raise ReleaseLockError(error)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _tar_octal(field: bytes, name: str) -> int:
    value = field.strip(b"\0 ")
    if not value or any(character not in b"01234567" for character in value):
        raise ReleaseLockError(f"archive {name} is not strict USTAR octal")
    return int(value, 8)


def _strict_ustar_member(header: bytes, expected_name: str) -> int:
    """Parse only the single plain USTAR entry published by openai/codex."""
    if len(header) != 512 or header == b"\0" * 512:
        raise ReleaseLockError(
            "archive must contain exactly its expected regular binary"
        )
    stored_checksum = _tar_octal(header[148:156], "checksum")
    calculated_checksum = sum(header[:148]) + (8 * ord(" ")) + sum(header[156:])
    if stored_checksum != calculated_checksum:
        raise ReleaseLockError("archive USTAR header checksum is invalid")
    if header[257:265] not in {b"ustar\x0000", b"ustar  \x00"}:
        raise ReleaseLockError("archive must use the expected USTAR format")
    if header[156:157] != b"0" or header[157:257].strip(b"\0"):
        raise ReleaseLockError(
            "archive must contain exactly its expected regular binary"
        )
    if header[345:500].strip(b"\0"):
        raise ReleaseLockError("archive USTAR path prefixes are not allowed")
    raw_name = header[:100].split(b"\0", 1)[0]
    try:
        member_name = raw_name.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ReleaseLockError("archive USTAR member name must be ASCII") from exc
    if member_name != expected_name:
        raise ReleaseLockError(
            "archive must contain exactly its expected regular binary"
        )
    member_size = _tar_octal(header[124:136], "member size")
    if member_size <= 0 or member_size > MAX_DECOMPRESSED_BYTES:
        raise ReleaseLockError("archive decompressed size is outside the allowed bound")
    return member_size


def _validate_elf_header(header: bytes, arch: str) -> None:
    expected_machine = ELF_MACHINES[arch]
    if (
        len(header) < 20
        or header[:4] != b"\x7fELF"
        or header[4] != 2
        or header[5] != 1
        or header[6] != 1
        or int.from_bytes(header[18:20], "little") != expected_machine
    ):
        raise ReleaseLockError(f"archive binary is not the expected {arch} ELF64")


def _archive_details(
    payload: bytes, expected_name: str, arch: str, destination: Path
) -> dict[str, Any]:
    """Stream one bounded expected ELF binary to ``destination`` and hash it."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ReleaseLockError("archive exceeds compressed size limit")
    if arch not in ARCHITECTURES:
        raise ReleaseLockError("archive architecture is not supported")
    if not expected_name.endswith(f"-{ARCHITECTURES[arch]}-unknown-linux-musl.tar.gz"):
        raise ReleaseLockError("archive name does not match its required architecture")
    expected_binary_name = expected_name.removesuffix(".tar.gz")
    digest = hashlib.sha256()
    extracted_size = 0
    header = bytearray()
    destination_created = False
    try:
        with (
            io.BytesIO(payload) as compressed,
            gzip.GzipFile(fileobj=compressed, mode="rb") as gzip_file,
        ):
            bounded_gzip = _BoundedReader(
                gzip_file, MAX_DECOMPRESSED_BYTES + MAX_TAR_OVERHEAD_BYTES
            )
            tar_header = _read_exact(
                bounded_gzip, 512, "archive USTAR header is incomplete"
            )
            member_size = _strict_ustar_member(tar_header, expected_binary_name)
            with destination.open("xb") as output:
                destination_created = True
                remaining = member_size
                while remaining:
                    chunk = bounded_gzip.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ReleaseLockError(
                            "archive binary size changed while reading"
                        )
                    remaining -= len(chunk)
                    extracted_size += len(chunk)
                    if len(header) < 20:
                        header.extend(chunk[: 20 - len(header)])
                    digest.update(chunk)
                    output.write(chunk)
            _validate_elf_header(bytes(header), arch)
            padding_size = (-member_size) % 512
            padding = _read_exact(
                bounded_gzip, padding_size, "archive USTAR padding is incomplete"
            )
            if any(padding):
                raise ReleaseLockError("archive USTAR padding must be zero-filled")
            trailer_size = 0
            while trailer := bounded_gzip.read(1024 * 1024):
                trailer_size += len(trailer)
                if any(trailer):
                    raise ReleaseLockError(
                        "archive must contain exactly one regular binary; "
                        "unexpected trailing data"
                    )
            if trailer_size < 1024:
                raise ReleaseLockError("archive USTAR trailer is incomplete")
    except (OSError, EOFError) as exc:
        if destination_created:
            destination.unlink(missing_ok=True)
        raise ReleaseLockError("invalid gzip tar archive") from exc
    except BaseException:
        if destination_created:
            destination.unlink(missing_ok=True)
        raise
    return {
        "name": expected_name,
        "decompressed_sha256": digest.hexdigest(),
        "decompressed_size": extracted_size,
    }


def parse_legacy_cosign_bundle(
    payload: bytes, expected_name: str, tag: str
) -> dict[str, Any]:
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ReleaseLockError("Sigstore bundle exceeds size limit")
    try:
        bundle = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReleaseLockError("Sigstore bundle is not JSON") from exc
    root = _mapping(bundle, "legacy Cosign bundle")
    _string(root.get("base64Signature"), "legacy Cosign signature")
    _string(root.get("cert"), "legacy Cosign certificate")
    rekor_bundle = _mapping(root.get("rekorBundle"), "legacy Cosign Rekor bundle")
    _string(
        rekor_bundle.get("SignedEntryTimestamp"), "legacy Cosign signed entry timestamp"
    )
    entry = _mapping(rekor_bundle.get("Payload"), "legacy Cosign Rekor payload")
    encoded_body = _string(entry.get("body"), "legacy Cosign Rekor body")
    try:
        body = _mapping(
            json.loads(base64.b64decode(encoded_body, validate=True)),
            "legacy Cosign Rekor body",
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise ReleaseLockError("legacy Cosign Rekor body is not base64 JSON") from exc
    spec = _mapping(body.get("spec"), "legacy Cosign Rekor spec")
    hash_value = _mapping(
        _mapping(spec.get("data"), "legacy Cosign Rekor data").get("hash"),
        "legacy Cosign Rekor hash",
    )
    if (
        _string(
            hash_value.get("algorithm"), "legacy Cosign Rekor hash algorithm"
        ).lower()
        != "sha256"
    ):
        raise ReleaseLockError("legacy Cosign Rekor body must sign a SHA-256 digest")
    signed_sha256 = _sha256(
        hash_value.get("value"), "legacy Cosign signed binary digest"
    )
    signature = _mapping(spec.get("signature"), "legacy Cosign Rekor signature")
    if (
        _string(signature.get("content"), "legacy Cosign Rekor signature content")
        != root["base64Signature"]
    ):
        raise ReleaseLockError(
            "legacy Cosign Rekor signature does not match bundle signature"
        )
    public_key = _mapping(signature.get("publicKey"), "legacy Cosign Rekor public key")
    if (
        _string(public_key.get("content"), "legacy Cosign Rekor public key content")
        != root["cert"]
    ):
        raise ReleaseLockError(
            "legacy Cosign Rekor public key does not match bundle certificate"
        )
    log_id = _sha256(entry.get("logID"), "Sigstore transparency log ID")
    # Cryptographic certificate, issuer, workflow identity, and Rekor inclusion
    # are verified by cosign below; this parser rejects malformed/empty bundles.
    return {
        "name": expected_name,
        "format": "cosign-legacy",
        "issuer": ISSUER,
        "identity": _identity_for(tag),
        "transparency_log": TRANSPARENCY_LOG,
        "log_id": log_id,
        "log_index": _positive_int(
            entry.get("logIndex"), "Sigstore transparency log index", 2**63 - 1
        ),
        "integrated_time": _positive_int(
            entry.get("integratedTime"),
            "Sigstore transparency integrated time",
            2**63 - 1,
        ),
        "signed_sha256": signed_sha256,
    }


def verify_sigstore_blob(
    cosign: str,
    binary_path: Path,
    bundle_path: Path,
    tag: str,
    commit: str,
    *,
    runner: Any | None = None,
) -> None:
    command = [
        cosign,
        "verify-blob",
        "--bundle",
        str(bundle_path),
        "--certificate-identity",
        _identity_for(tag),
        "--certificate-oidc-issuer",
        ISSUER,
        "--certificate-github-workflow-name",
        "rust-release",
        "--certificate-github-workflow-repository",
        REPOSITORY,
        "--certificate-github-workflow-ref",
        f"refs/tags/{tag}",
        "--certificate-github-workflow-sha",
        _commit(commit, "verified release tag commit"),
        "--certificate-github-workflow-trigger",
        "push",
        str(binary_path),
    ]
    if runner is not None:
        success = runner(command) == 0
    else:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=60
        )
        success = completed.returncode == 0
    if not success:
        raise ReleaseLockError(
            "cosign rejected the Sigstore bundle or its transparency-log evidence"
        )


def _verified_detail_sets(
    metadata: Mapping[str, Any], cosign: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    tag, selected = _validate_release_metadata(metadata)
    commit = _commit(metadata.get("verified_tag_commit"), "verified release tag commit")
    archive_details: dict[str, Any] = {}
    bundle_details: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="codex-release-lock-") as temporary:
        root = Path(temporary)
        for arch in ARCHITECTURES:
            archive_details[arch] = {}
            bundle_details[arch] = {}
            for component in COMPONENTS:
                archive = selected[arch][component]["archive"]
                bundle = selected[arch][component]["bundle"]
                archive_payload = _read_url(archive["url"], MAX_ARCHIVE_BYTES)
                bundle_payload = _read_url(bundle["url"], MAX_BUNDLE_BYTES)
                if (
                    len(archive_payload) != archive["size"]
                    or _sha256_bytes(archive_payload) != archive["sha256"]
                ):
                    raise ReleaseLockError(
                        f"downloaded archive digest or size mismatch for {archive['name']}"
                    )
                if (
                    len(bundle_payload) != bundle["size"]
                    or _sha256_bytes(bundle_payload) != bundle["sha256"]
                ):
                    raise ReleaseLockError(
                        f"downloaded Sigstore digest or size mismatch for {bundle['name']}"
                    )
                archive_path = root / archive["name"]
                bundle_path = root / bundle["name"]
                archive_path.write_bytes(archive_payload)
                bundle_path.write_bytes(bundle_payload)
                binary_path = root / f"{arch}-{archive['name'].removesuffix('.tar.gz')}"
                detail = _archive_details(
                    archive_payload, archive["name"], arch, binary_path
                )
                archive_details[arch][component] = detail
                bundle_details[arch][component] = parse_legacy_cosign_bundle(
                    bundle_payload, bundle["name"], tag
                )
                if (
                    bundle_details[arch][component]["signed_sha256"]
                    != detail["decompressed_sha256"]
                ):
                    raise ReleaseLockError(
                        "legacy Cosign bundle does not sign the extracted binary"
                    )
                verify_sigstore_blob(cosign, binary_path, bundle_path, tag, commit)
    return archive_details, bundle_details


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseLockError(f"cannot read {path}") from exc
    return _mapping(value, str(path))


def normalize_github_release(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Bind release JSON from the fixed GitHub API endpoint to openai/codex."""
    normalized = dict(metadata)
    supplied_repository = normalized.get("repository")
    if supplied_repository not in (None, REPOSITORY):
        raise ReleaseLockError(
            "release repository identity does not match openai/codex"
        )
    normalized["repository"] = REPOSITORY
    return normalized


def _read_json_url(url: str) -> Mapping[str, Any]:
    return _mapping(json.loads(_read_url(url, 4 * 1024 * 1024)), url)


def resolve_tag_commit(tag: str) -> str:
    """Peel the verified annotated rust-release tag to its immutable commit."""
    _tag_parts(tag)
    reference = _read_json_url(
        f"https://api.github.com/repos/{REPOSITORY}/git/ref/tags/{tag}"
    )
    target = _mapping(reference.get("object"), "release tag reference object")
    target_type = _string(target.get("type"), "release tag reference type")
    target_sha = _commit(target.get("sha"), "release tag reference SHA")
    if target_type == "commit":
        return target_sha
    if target_type != "tag":
        raise ReleaseLockError(
            "release tag does not resolve to an annotated tag or commit"
        )
    annotated = _read_json_url(
        f"https://api.github.com/repos/{REPOSITORY}/git/tags/{target_sha}"
    )
    peeled = _mapping(annotated.get("object"), "annotated release tag object")
    if _string(peeled.get("type"), "annotated release tag target type") != "commit":
        raise ReleaseLockError("annotated release tag does not point to a commit")
    return _commit(peeled.get("sha"), "annotated release tag commit")


def update_lock(path: Path, *, cosign: str) -> dict[str, Any]:
    """Fetch, verify, and atomically replace a lock only after all checks pass."""
    metadata = normalize_github_release(
        _mapping(
            json.loads(_read_url(LATEST_RELEASE_URL, 4 * 1024 * 1024)),
            "release metadata",
        )
    )
    metadata["verified_tag_commit"] = resolve_tag_commit(
        _string(metadata.get("tag_name"), "tag_name")
    )
    archive_details, bundle_details = _verified_detail_sets(metadata, cosign)
    candidate = build_lock_from_metadata(
        metadata, archive_details=archive_details, bundle_details=bundle_details
    )
    commit_lock_candidate(path, candidate)
    return candidate


def fixture_archive_details() -> dict[str, Any]:
    """Deterministic details used only by offline unit fixtures."""
    return {
        arch: {
            component: {
                "name": _asset_name(component, target, ".tar.gz"),
                "decompressed_sha256": hashlib.sha256(
                    f"{arch}/{component}".encode()
                ).hexdigest(),
                "decompressed_size": 2048,
            }
            for component in COMPONENTS
        }
        for arch, target in ARCHITECTURES.items()
    }


def fixture_bundle_details() -> dict[str, Any]:
    """Deterministic Sigstore claims used only by offline unit fixtures."""
    tag = "rust-v1.2.3"
    return {
        arch: {
            component: {
                "name": _asset_name(component, target, ".sigstore"),
                "format": "cosign-legacy",
                "issuer": ISSUER,
                "identity": _identity_for(tag),
                "transparency_log": TRANSPARENCY_LOG,
                "log_id": "a" * 64,
                "log_index": 1,
                "integrated_time": 1_784_003_665,
                "signed_sha256": hashlib.sha256(
                    f"{arch}/{component}".encode()
                ).hexdigest(),
            }
            for component in COMPONENTS
        }
        for arch, target in ARCHITECTURES.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", type=Path, metavar="LOCK", help="validate an existing lock offline"
    )
    group.add_argument(
        "--update",
        type=Path,
        metavar="LOCK",
        help="verify latest release then atomically write LOCK",
    )
    parser.add_argument(
        "--cosign", default="cosign", help="pinned cosign executable used for --update"
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.check is not None:
            validate_lock(_load_json(arguments.check))
            print(f"Verified lock structure: {arguments.check}")
        else:
            lock = update_lock(arguments.update, cosign=arguments.cosign)
            print(f"Updated verified Codex lock to {lock['release']['tag']}")
    except (ReleaseLockError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
