#!/usr/bin/env python3
"""Validate offline, redacted evidence for cold restore or retained-image recovery.

This collector deliberately has no Home Assistant, Supervisor, container, or
network client.  It only reads two local JSON snapshots and, after all checks
pass, atomically writes a canonical redacted result manifest.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid
from typing import Any, Mapping, NamedTuple


SNAPSHOT_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 256 * 1024
COMPONENTS = ("app", "integration", "bridge", "codex")
FINGERPRINTS = ("workspace", "chat", "artifact", "automation")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
RFC3339_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


class RecoveryEvidenceError(ValueError):
    """Snapshots are incomplete, unsafe, or do not prove the requested recovery."""


class SnapshotFile(NamedTuple):
    """Strict JSON loaded from one stable regular-file descriptor."""

    value: dict[str, Any]
    identity: tuple[int, int]


def _file_stability(metadata: os.stat_result) -> tuple[int, int, int, int]:
    """Return fields that change for an in-place rewrite on supported hosts."""

    mtime_ns = metadata.st_mtime_ns
    ctime_ns = metadata.st_ctime_ns
    return (
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime * 1_000_000_000) if mtime_ns is None else int(mtime_ns),
        int(metadata.st_ctime * 1_000_000_000) if ctime_ns is None else int(ctime_ns),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryEvidenceError("snapshot contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise RecoveryEvidenceError("snapshot contains an unsupported JSON constant")


def _file_identity(metadata: os.stat_result, label: str) -> tuple[int, int]:
    identity = (int(metadata.st_dev), int(metadata.st_ino))
    if identity == (0, 0):
        raise RecoveryEvidenceError(f"{label} file identity is unavailable")
    return identity


def _read_snapshot_file(path: Path, label: str) -> SnapshotFile:
    """Open once, refuse links, and read through the verified descriptor."""
    try:
        path_metadata = os.lstat(path)
    except OSError as exc:
        raise RecoveryEvidenceError(f"{label} is not readable") from exc
    if not stat.S_ISREG(path_metadata.st_mode) or stat.S_ISLNK(path_metadata.st_mode):
        raise RecoveryEvidenceError(f"{label} must be a regular, non-symlink file")
    path_identity = _file_identity(path_metadata, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise RecoveryEvidenceError(f"{label} descriptor is not a regular file")
        opened_identity = _file_identity(opened_metadata, label)
        if opened_identity != path_identity:
            raise RecoveryEvidenceError(f"{label} changed while it was opened")
        opened_stability = _file_stability(opened_metadata)
        if opened_metadata.st_size > MAX_SNAPSHOT_BYTES:
            raise RecoveryEvidenceError(f"{label} exceeds the size limit")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
            final_metadata = os.fstat(handle.fileno())
            if (
                _file_identity(final_metadata, label) != opened_identity
                or _file_stability(final_metadata) != opened_stability
                or len(raw) != opened_metadata.st_size
            ):
                raise RecoveryEvidenceError(f"{label} changed while it was read")
            handle.seek(0)
            verification = handle.read(MAX_SNAPSHOT_BYTES + 1)
            verified_metadata = os.fstat(handle.fileno())
            if (
                verification != raw
                or _file_identity(verified_metadata, label) != opened_identity
                or _file_stability(verified_metadata) != opened_stability
            ):
                raise RecoveryEvidenceError(f"{label} changed while it was read")
    except OSError as exc:
        raise RecoveryEvidenceError(f"{label} is not readable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise RecoveryEvidenceError(f"{label} exceeds the size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecoveryEvidenceError) as exc:
        if isinstance(exc, RecoveryEvidenceError):
            raise
        raise RecoveryEvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RecoveryEvidenceError(f"{label} must contain a JSON object")
    return SnapshotFile(value=value, identity=opened_identity)


def read_snapshot(path: Path, label: str) -> dict[str, Any]:
    """Read a bounded, strict JSON object without echoing its sensitive content."""
    return _read_snapshot_file(path, label).value


def _object(value: object, label: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RecoveryEvidenceError(f"{label} has unsupported or missing fields")
    return value


def _safe_string(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RecoveryEvidenceError(f"{label} is not a safe value")
    return value


def _components(value: object, label: str) -> dict[str, dict[str, str]]:
    raw = _object(value, label, set(COMPONENTS))
    result: dict[str, dict[str, str]] = {}
    for name in COMPONENTS:
        component = _object(raw[name], f"{label} {name}", {"version", "digest"})
        result[name] = {
            "version": _safe_string(component["version"], f"{label} {name} version", SAFE_VERSION),
            "digest": _safe_string(component["digest"], f"{label} {name} digest", SHA256),
        }
    return result


def _capture(value: object, label: str) -> dict[str, Any]:
    raw = _object(value, label, {"id", "phase", "captured_at"})
    capture_id_value = raw["id"]
    if not isinstance(capture_id_value, str):
        raise RecoveryEvidenceError(f"{label} id is not a UUID")
    try:
        capture_id = str(uuid.UUID(capture_id_value))
    except (ValueError, AttributeError) as exc:
        raise RecoveryEvidenceError(f"{label} id is not a UUID") from exc
    if capture_id != capture_id_value:
        raise RecoveryEvidenceError(f"{label} id is not a canonical UUID")
    phase = raw["phase"]
    if not isinstance(phase, str) or phase not in {"pre", "post"}:
        raise RecoveryEvidenceError(f"{label} phase is invalid")
    captured_at = raw["captured_at"]
    if not isinstance(captured_at, str) or RFC3339_TIMESTAMP.fullmatch(captured_at) is None:
        raise RecoveryEvidenceError(f"{label} timestamp is not bounded timezone-aware RFC3339")
    try:
        instant = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryEvidenceError(f"{label} timestamp is invalid") from exc
    if instant.utcoffset() is None:
        raise RecoveryEvidenceError(f"{label} timestamp is not timezone-aware")
    return {"id": capture_id, "phase": phase, "captured_at": captured_at, "instant": instant}


def _recovery(value: object, label: str) -> dict[str, Any]:
    raw = _object(value, label, {"retained_image", "rollback"})
    retained = _object(raw["retained_image"], f"{label} retained image", {"healthy", "components"})
    if retained["healthy"] is not True:
        raise RecoveryEvidenceError("retained image is not verified healthy")
    rollback_value = raw["rollback"]
    rollback: dict[str, Any] | None
    if rollback_value is None:
        rollback = None
    else:
        rollback_raw = _object(
            rollback_value,
            f"{label} rollback",
            {"verified", "from_components", "target_components"},
        )
        if rollback_raw["verified"] is not True:
            raise RecoveryEvidenceError("rollback evidence is not verified")
        rollback = {
            "from_components": _components(rollback_raw["from_components"], f"{label} rollback source"),
            "target_components": _components(rollback_raw["target_components"], f"{label} rollback target"),
        }
    return {
        "retained_components": _components(retained["components"], f"{label} retained image"),
        "rollback": rollback,
    }


def validate_snapshot(value: object, label: str) -> dict[str, Any]:
    """Validate the narrow snapshot allowlist and return only safe normalized data."""
    raw = _object(
        value,
        label,
        {
            "schema_version",
            "capture",
            "test_ha",
            "supervisor_uuid",
            "components",
            "backup",
            "readiness",
            "sandbox",
            "account",
            "fingerprints",
            "recovery",
        },
    )
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != SNAPSHOT_SCHEMA_VERSION
    ):
        raise RecoveryEvidenceError(f"{label} has an unsupported schema version")
    test_ha = _safe_string(raw["test_ha"], f"{label} test HA identity", SAFE_IDENTIFIER)
    try:
        supervisor_uuid = str(uuid.UUID(str(raw["supervisor_uuid"])))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RecoveryEvidenceError(f"{label} Supervisor UUID is invalid") from exc
    backup = _object(raw["backup"], f"{label} backup", {"id", "verified"})
    if backup["verified"] is not True:
        raise RecoveryEvidenceError("backup evidence is missing or unverified")
    readiness = _object(raw["readiness"], f"{label} readiness", {"home_assistant", "app", "bridge"})
    sandbox = _object(raw["sandbox"], f"{label} sandbox", {"status"})
    account = _object(raw["account"], f"{label} account", {"state"})
    fingerprints = _object(raw["fingerprints"], f"{label} fingerprints", set(FINGERPRINTS))
    normalized_readiness = {
        name: _safe_string(readiness[name], f"{label} readiness {name}", SAFE_IDENTIFIER)
        for name in sorted(readiness)
    }
    if any(value != "ready" for value in normalized_readiness.values()):
        raise RecoveryEvidenceError(f"{label} readiness is not fully ready")
    sandbox_status = _safe_string(sandbox["status"], f"{label} sandbox status", SAFE_IDENTIFIER)
    if sandbox_status != "passed":
        raise RecoveryEvidenceError(f"{label} sandbox has not passed")
    account_state = _safe_string(account["state"], f"{label} account state", SAFE_IDENTIFIER)
    if account_state != "authenticated":
        raise RecoveryEvidenceError(f"{label} account is not authenticated")
    return {
        "capture": _capture(raw["capture"], f"{label} capture"),
        "test_ha": test_ha,
        "supervisor_uuid": supervisor_uuid,
        "components": _components(raw["components"], f"{label} components"),
        "backup_id": _safe_string(backup["id"], f"{label} backup identifier", SAFE_IDENTIFIER),
        "readiness": normalized_readiness,
        "sandbox": sandbox_status,
        "account": account_state,
        "fingerprints": {
            name: _safe_string(fingerprints[name], f"{label} {name} fingerprint", FINGERPRINT)
            for name in FINGERPRINTS
        },
        "recovery": _recovery(raw["recovery"], f"{label} recovery"),
    }


def _equal(left: object, right: object, label: str) -> None:
    if left != right:
        raise RecoveryEvidenceError(f"{label} does not match between snapshots")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collect(pre: Mapping[str, Any], post: Mapping[str, Any], mode: str) -> dict[str, Any]:
    """Compare normalized snapshots and build a deterministic redacted result."""
    if pre["capture"]["phase"] != "pre":
        raise RecoveryEvidenceError("pre capture phase must be pre")
    if post["capture"]["phase"] != "post":
        raise RecoveryEvidenceError("post capture phase must be post")
    if pre["capture"]["id"] == post["capture"]["id"]:
        raise RecoveryEvidenceError("capture evidence is replayed")
    if post["capture"]["instant"] <= pre["capture"]["instant"]:
        raise RecoveryEvidenceError("capture timestamps are not strictly ordered")
    if pre["components"]["app"] == pre["recovery"]["retained_components"]["app"]:
        raise RecoveryEvidenceError("preflight retained App image is not distinct from current")
    _equal(pre["test_ha"], post["test_ha"], "test HA identity")
    _equal(pre["supervisor_uuid"], post["supervisor_uuid"], "Supervisor UUID")
    _equal(pre["backup_id"], post["backup_id"], "backup evidence")
    _equal(pre["readiness"], post["readiness"], "readiness categories")
    _equal(pre["sandbox"], post["sandbox"], "sandbox category")
    _equal(pre["account"], post["account"], "account category")
    _equal(pre["fingerprints"], post["fingerprints"], "workspace/chat/artifact/automation fingerprints")
    _equal(
        pre["recovery"]["retained_components"],
        post["recovery"]["retained_components"],
        "retained-image evidence",
    )
    if mode == "cold-restore":
        _equal(pre["components"], post["components"], "App/Integration/Bridge/Codex versions and digests")
        checks = ["cold_restore", "backup", "capture", "identity", "components", "categories", "fingerprints"]
    elif mode == "retained-image":
        rollback = post["recovery"]["rollback"]
        if rollback is None:
            raise RecoveryEvidenceError("retained-image recovery lacks rollback evidence")
        _equal(rollback["from_components"], pre["components"], "rollback source components")
        _equal(rollback["target_components"], pre["recovery"]["retained_components"], "rollback target components")
        _equal(post["components"], pre["recovery"]["retained_components"], "retained-image components")
        checks = ["retained_image", "backup", "capture", "identity", "rollback", "categories", "fingerprints"]
    else:  # argparse prevents this, but collect is also imported by tests.
        raise RecoveryEvidenceError("unsupported recovery mode")
    return {
        "capture": {
            "post_captured_at": post["capture"]["captured_at"],
            "post_id_fingerprint": _fingerprint(post["capture"]["id"]),
            "pre_captured_at": pre["capture"]["captured_at"],
            "pre_id_fingerprint": _fingerprint(pre["capture"]["id"]),
        },
        "checks": checks,
        "collector_version": 1,
        "components": post["components"],
        "fingerprints": post["fingerprints"],
        "identity": {
            "supervisor_uuid_fingerprint": _fingerprint(post["supervisor_uuid"]),
            "test_ha_fingerprint": _fingerprint(post["test_ha"]),
        },
        "mode": mode,
        "recovery": {"backup_id_fingerprint": _fingerprint(post["backup_id"])},
        "schema_version": RESULT_SCHEMA_VERSION,
        "evidence_scope": "offline_snapshot_consistency",
        "status": "evidence_format_validated",
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Atomically write the sole collector output after every validation passes."""
    parent = path.parent
    if not parent.is_dir():
        raise RecoveryEvidenceError("output directory does not exist")
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _absolute_path(path: Path) -> Path:
    """Make a path absolute without resolving symlinks at an input boundary."""
    return Path(os.path.abspath(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre", type=Path, required=True, help="redacted pre-recovery snapshot")
    parser.add_argument("--post", type=Path, required=True, help="redacted post-recovery snapshot")
    parser.add_argument("--output", type=Path, required=True, help="result manifest to create atomically")
    parser.add_argument("--mode", choices=("cold-restore", "retained-image"), default="cold-restore")
    args = parser.parse_args(argv)
    try:
        pre_path, post_path, output_path = (
            _absolute_path(args.pre),
            _absolute_path(args.post),
            _absolute_path(args.output),
        )
        if output_path in {pre_path, post_path}:
            raise RecoveryEvidenceError("output must not overwrite an input snapshot")
        pre_file = _read_snapshot_file(pre_path, "pre snapshot")
        post_file = _read_snapshot_file(post_path, "post snapshot")
        if pre_file.identity == post_file.identity:
            raise RecoveryEvidenceError("pre and post snapshots are the same or hard-linked evidence")
        pre = validate_snapshot(pre_file.value, "pre snapshot")
        post = validate_snapshot(post_file.value, "post snapshot")
        write_manifest(output_path, collect(pre, post, args.mode))
    except (OSError, RecoveryEvidenceError) as exc:
        print(f"recovery acceptance failed: {exc}", file=sys.stderr)
        return 1
    print("recovery evidence format and snapshot consistency validated")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
