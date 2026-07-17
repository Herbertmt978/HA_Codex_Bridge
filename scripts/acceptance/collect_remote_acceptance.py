#!/usr/bin/env python3
"""Validate offline, redacted remote-path evidence and write one local manifest.

The collector has no Home Assistant or network client. It reads one bounded
local JSON bundle, validates provider-neutral evidence summaries, and writes a
canonical manifest atomically only after every check passes.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any
import uuid


INPUT_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 256 * 1024
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
ROUTE_PROFILES = ("lan", "nabu-shaped", "cloudflare-shaped")
PROFILE_ORDER = {name: index for index, name in enumerate(ROUTE_PROFILES)}
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
RFC3339_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
UPLOAD_CREATE_PATH = re.compile(
    rf"^/api/codex_bridge/threads/(?P<thread>{IDENTIFIER})/uploads$"
)
UPLOAD_STATUS_PATH = re.compile(
    rf"^/api/codex_bridge/threads/(?P<thread>{IDENTIFIER})/uploads/"
    rf"(?P<upload>{IDENTIFIER})$"
)
UPLOAD_CHUNK_PATH = re.compile(
    rf"^/api/codex_bridge/threads/(?P<thread>{IDENTIFIER})/uploads/"
    rf"(?P<upload>{IDENTIFIER})/chunks/0$"
)
UPLOAD_COMPLETE_PATH = re.compile(
    rf"^/api/codex_bridge/threads/(?P<thread>{IDENTIFIER})/uploads/"
    rf"(?P<upload>{IDENTIFIER})/complete$"
)
ARTIFACT_PATH = re.compile(
    rf"^/api/codex_bridge/threads/(?P<thread>{IDENTIFIER})/artifacts/"
    rf"(?P<artifact>{IDENTIFIER})$"
)
REQUEST_FIELDS = {
    "method",
    "path",
    "status",
    "origin_fingerprint",
    "redirects",
    "cross_origin_requests",
}


class RemoteEvidenceError(ValueError):
    """Evidence is incomplete, unsafe, replayed, or not provider neutral."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RemoteEvidenceError("evidence contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise RemoteEvidenceError("evidence contains an unsupported JSON constant")


def _file_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _read_input(path: Path) -> dict[str, Any]:
    """Read one bounded regular file through a stable, no-follow descriptor."""
    try:
        path_metadata = os.lstat(path)
    except OSError as exc:
        raise RemoteEvidenceError("input evidence is not readable") from exc
    if not stat.S_ISREG(path_metadata.st_mode) or stat.S_ISLNK(path_metadata.st_mode):
        raise RemoteEvidenceError("input evidence must be a regular non-symlink file")
    path_signature = _file_signature(path_metadata)
    path_identity = path_signature[:2]
    if path_identity == (0, 0):
        raise RemoteEvidenceError("input evidence identity is unavailable")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RemoteEvidenceError("input evidence descriptor is not a regular file")
        opened_signature = _file_signature(opened)
        if opened_signature[:2] != path_identity:
            raise RemoteEvidenceError("input evidence changed while it was opened")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(MAX_INPUT_BYTES + 1)
            final = os.fstat(handle.fileno())
            if _file_signature(final) != opened_signature:
                raise RemoteEvidenceError("input evidence changed while it was read")
    except OSError as exc:
        raise RemoteEvidenceError("input evidence is not readable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(raw) > MAX_INPUT_BYTES:
        raise RemoteEvidenceError("input evidence exceeds the size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteEvidenceError("input evidence is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RemoteEvidenceError("input evidence must contain a JSON object")
    return value


def _object(value: object, label: str, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise RemoteEvidenceError(f"{label} has unsupported or missing fields")
    return value


def _fingerprint(value: object, label: str) -> str:
    if not isinstance(value, str) or FINGERPRINT.fullmatch(value) is None:
        raise RemoteEvidenceError(f"{label} is not a redacted SHA-256 fingerprint")
    return value


def _capture_metadata(value: object, label: str) -> dict[str, Any]:
    raw = _object(
        value,
        label,
        {"id", "captured_at", "evidence_kind", "origin_fingerprint"},
    )
    capture_id_value = raw["id"]
    if not isinstance(capture_id_value, str):
        raise RemoteEvidenceError(f"{label} id is not a canonical UUID")
    try:
        capture_id = str(uuid.UUID(capture_id_value))
    except (AttributeError, ValueError) as exc:
        raise RemoteEvidenceError(f"{label} id is not a canonical UUID") from exc
    if capture_id != capture_id_value:
        raise RemoteEvidenceError(f"{label} id is not a canonical UUID")

    captured_at = raw["captured_at"]
    if (
        not isinstance(captured_at, str)
        or RFC3339_TIMESTAMP.fullmatch(captured_at) is None
    ):
        raise RemoteEvidenceError(f"{label} timestamp is not timezone-aware RFC3339")
    try:
        instant = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteEvidenceError(f"{label} timestamp is invalid") from exc
    if instant.utcoffset() is None:
        raise RemoteEvidenceError(f"{label} timestamp is not timezone-aware")

    evidence_kind = raw["evidence_kind"]
    if not isinstance(evidence_kind, str) or evidence_kind not in {"synthetic", "real"}:
        raise RemoteEvidenceError(f"{label} evidence kind is invalid")
    return {
        "id": capture_id,
        "captured_at": captured_at,
        "instant": instant,
        "evidence_kind": evidence_kind,
        "origin_fingerprint": _fingerprint(
            raw["origin_fingerprint"], f"{label} origin"
        ),
    }


def _network(value: object, profile: str, label: str) -> dict[str, str]:
    raw = _object(value, label, {"classification", "observed_from"})
    expected = (
        {"classification": "lan", "observed_from": "home-network"}
        if profile == "lan"
        else {
            "classification": "external",
            "observed_from": "external-network",
        }
    )
    if dict(raw) != expected:
        raise RemoteEvidenceError(
            f"{label} does not prove the required external network classification"
        )
    return expected


def _relative_path(
    value: object,
    label: str,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, re.Match[str] | None]:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "?" in value
        or "#" in value
        or len(value) > 512
    ):
        raise RemoteEvidenceError(f"{label} must be a bounded relative HA path")
    match = pattern.fullmatch(value) if pattern is not None else None
    if pattern is not None and match is None:
        raise RemoteEvidenceError(f"{label} must be a bounded relative HA path")
    return value, match


def _request(
    value: object,
    label: str,
    *,
    method: str,
    status: int,
    origin: str,
    path: str | None = None,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, re.Match[str] | None]:
    raw = _object(value, label, REQUEST_FIELDS)
    if raw["method"] != method or type(raw["status"]) is not int:
        raise RemoteEvidenceError(f"{label} method or status is invalid")
    if raw["status"] != status:
        raise RemoteEvidenceError(f"{label} method or status is invalid")
    if raw["origin_fingerprint"] != origin:
        raise RemoteEvidenceError(f"{label} is cross-origin")
    if type(raw["redirects"]) is not int or raw["redirects"] != 0:
        raise RemoteEvidenceError(f"{label} contains a redirect")
    if (
        type(raw["cross_origin_requests"]) is not int
        or raw["cross_origin_requests"] != 0
    ):
        raise RemoteEvidenceError(f"{label} contains a cross-origin request")
    relative, match = _relative_path(raw["path"], label, pattern)
    if path is not None and relative != path:
        raise RemoteEvidenceError(f"{label} must use the expected relative HA path")
    return relative, match


def _websocket(value: object, origin: str, label: str) -> None:
    raw = _object(
        value,
        label,
        {
            "path",
            "origin_fingerprint",
            "auth",
            "reconnects",
            "event_sequences",
            "duplicate_events",
            "redirects",
            "cross_origin_requests",
        },
    )
    _relative_path(raw["path"], label)
    if raw["path"] != "/api/websocket" or raw["origin_fingerprint"] != origin:
        raise RemoteEvidenceError(f"{label} is cross-origin")
    if raw["auth"] != "passed":
        raise RemoteEvidenceError(f"{label} authentication category did not pass")
    if type(raw["reconnects"]) is not int or not 1 <= raw["reconnects"] <= 10:
        raise RemoteEvidenceError(f"{label} does not prove a bounded reconnect")
    sequences = raw["event_sequences"]
    if (
        not isinstance(sequences, list)
        or not 2 <= len(sequences) <= 64
        or any(type(item) is not int or item < 0 for item in sequences)
        or len(set(sequences)) != len(sequences)
        or sequences != sorted(sequences)
    ):
        raise RemoteEvidenceError(f"{label} contains duplicate replay evidence")
    if type(raw["duplicate_events"]) is not int or raw["duplicate_events"] != 0:
        raise RemoteEvidenceError(f"{label} contains duplicate replay evidence")
    if type(raw["redirects"]) is not int or raw["redirects"] != 0:
        raise RemoteEvidenceError(f"{label} contains a redirect")
    if (
        type(raw["cross_origin_requests"]) is not int
        or raw["cross_origin_requests"] != 0
    ):
        raise RemoteEvidenceError(f"{label} contains a cross-origin request")


def _upload(value: object, origin: str, label: str) -> str:
    raw = _object(value, label, {"create", "status", "chunk", "complete", "cancel"})
    _, create_match = _request(
        raw["create"],
        f"{label} create",
        method="POST",
        status=201,
        origin=origin,
        pattern=UPLOAD_CREATE_PATH,
    )
    _, status_match = _request(
        raw["status"],
        f"{label} status",
        method="GET",
        status=200,
        origin=origin,
        pattern=UPLOAD_STATUS_PATH,
    )

    chunk = _object(
        raw["chunk"],
        f"{label} chunk",
        REQUEST_FIELDS
        | {
            "chunk_bytes",
            "attempts",
            "commits",
            "response_losses",
            "idempotent_retries",
        },
    )
    _, chunk_match = _request(
        {key: chunk[key] for key in REQUEST_FIELDS},
        f"{label} chunk",
        method="PUT",
        status=200,
        origin=origin,
        pattern=UPLOAD_CHUNK_PATH,
    )
    if (
        type(chunk["chunk_bytes"]) is not int
        or chunk["chunk_bytes"] != UPLOAD_CHUNK_BYTES
    ):
        raise RemoteEvidenceError(f"{label} chunk is not exactly 8 MiB")
    if (
        any(
            type(chunk[field]) is not int
            for field in (
                "attempts",
                "commits",
                "response_losses",
                "idempotent_retries",
            )
        )
        or chunk["attempts"] != 2
        or chunk["commits"] != 1
        or chunk["response_losses"] != 1
        or chunk["idempotent_retries"] != 1
    ):
        raise RemoteEvidenceError(
            f"{label} chunk does not prove one commit and one idempotent retry"
        )

    _, complete_match = _request(
        raw["complete"],
        f"{label} complete",
        method="POST",
        status=201,
        origin=origin,
        pattern=UPLOAD_COMPLETE_PATH,
    )
    _, cancel_match = _request(
        raw["cancel"],
        f"{label} cancel",
        method="DELETE",
        status=200,
        origin=origin,
        pattern=UPLOAD_STATUS_PATH,
    )
    if any(
        match is None
        for match in (
            create_match,
            status_match,
            chunk_match,
            complete_match,
            cancel_match,
        )
    ):
        raise RemoteEvidenceError(f"{label} contains an invalid relative HA path")
    assert create_match is not None
    assert status_match is not None
    assert chunk_match is not None
    assert complete_match is not None
    assert cancel_match is not None
    thread = create_match.group("thread")
    upload = status_match.group("upload")
    if any(
        match.group("thread") != thread
        for match in (status_match, chunk_match, complete_match, cancel_match)
    ):
        raise RemoteEvidenceError(f"{label} paths do not share one thread")
    if any(match.group("upload") != upload for match in (chunk_match, complete_match)):
        raise RemoteEvidenceError(f"{label} replay paths do not share one upload")
    if cancel_match.group("upload") == upload:
        raise RemoteEvidenceError(
            f"{label} cancellation does not use a separate upload"
        )
    return thread


def _artifact(value: object, origin: str, thread: str, label: str) -> None:
    raw = _object(
        value,
        label,
        {
            "path",
            "origin_fingerprint",
            "redirects",
            "cross_origin_requests",
            "initial_status",
            "if_range",
            "interrupted_after_bytes",
            "resume_range_start",
            "resume_status",
            "unsatisfied_status",
        },
    )
    _, match = _relative_path(raw["path"], label, ARTIFACT_PATH)
    if match is None:
        raise RemoteEvidenceError(f"{label} must use a bounded relative HA path")
    if match.group("thread") != thread:
        raise RemoteEvidenceError(f"{label} path does not share the upload thread")
    if raw["origin_fingerprint"] != origin:
        raise RemoteEvidenceError(f"{label} is cross-origin")
    if type(raw["redirects"]) is not int or raw["redirects"] != 0:
        raise RemoteEvidenceError(f"{label} contains a redirect")
    if (
        type(raw["cross_origin_requests"]) is not int
        or raw["cross_origin_requests"] != 0
    ):
        raise RemoteEvidenceError(f"{label} contains a cross-origin request")
    interrupted = raw["interrupted_after_bytes"]
    if (
        type(raw["initial_status"]) is not int
        or raw["initial_status"] != 206
        or raw["if_range"] is not True
        or type(interrupted) is not int
        or not 1 <= interrupted <= 2**63 - 1
        or type(raw["resume_range_start"]) is not int
        or raw["resume_range_start"] != interrupted
        or type(raw["resume_status"]) is not int
        or raw["resume_status"] != 206
        or type(raw["unsatisfied_status"]) is not int
        or raw["unsatisfied_status"] != 416
    ):
        raise RemoteEvidenceError(
            f"{label} does not prove 206/416 If-Range resume behavior"
        )


def _capture(value: object, label: str) -> dict[str, Any]:
    raw = _object(
        value,
        label,
        {
            "schema_version",
            "capture",
            "route_profile",
            "network",
            "runtime_route",
            "browser",
            "flows",
        },
    )
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != INPUT_SCHEMA_VERSION
    ):
        raise RemoteEvidenceError(f"{label} has an unsupported schema version")
    profile = raw["route_profile"]
    if profile not in ROUTE_PROFILES:
        raise RemoteEvidenceError(f"{label} route profile is invalid")
    if raw["runtime_route"] != "provider-neutral":
        raise RemoteEvidenceError(f"{label} runtime route is not provider-neutral")
    metadata = _capture_metadata(raw["capture"], f"{label} metadata")
    network = _network(raw["network"], profile, f"{label} network")
    origin = metadata["origin_fingerprint"]
    browser = _object(
        raw["browser"],
        f"{label} browser",
        {"origin_fingerprint", "redirects", "cross_origin_requests"},
    )
    if browser["origin_fingerprint"] != origin:
        raise RemoteEvidenceError(f"{label} browser is cross-origin")
    if type(browser["redirects"]) is not int or browser["redirects"] != 0:
        raise RemoteEvidenceError(f"{label} browser contains a redirect")
    if (
        type(browser["cross_origin_requests"]) is not int
        or browser["cross_origin_requests"] != 0
    ):
        raise RemoteEvidenceError(f"{label} browser contains a cross-origin request")

    flows = _object(
        raw["flows"], f"{label} flows", {"api", "websocket", "upload", "artifact"}
    )
    _request(
        flows["api"],
        f"{label} API",
        method="GET",
        status=200,
        origin=origin,
        path="/api/",
    )
    _websocket(flows["websocket"], origin, f"{label} WebSocket")
    thread = _upload(flows["upload"], origin, f"{label} upload")
    _artifact(flows["artifact"], origin, thread, f"{label} artifact")
    return {
        **metadata,
        "route_profile": profile,
        "network_classification": network["classification"],
    }


def validate_bundle(value: object) -> list[dict[str, Any]]:
    """Validate all three provider-shaped profiles without adding runtime branches."""
    raw = _object(value, "evidence bundle", {"schema_version", "captures"})
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != INPUT_SCHEMA_VERSION
    ):
        raise RemoteEvidenceError("evidence bundle has an unsupported schema version")
    capture_values = raw["captures"]
    if not isinstance(capture_values, list) or len(capture_values) != 3:
        raise RemoteEvidenceError("evidence bundle must contain exactly three captures")
    captures = [
        _capture(item, f"capture {index}")
        for index, item in enumerate(capture_values, start=1)
    ]
    if {item["route_profile"] for item in captures} != set(ROUTE_PROFILES):
        raise RemoteEvidenceError(
            "capture route profiles must be distinct and complete"
        )
    if len({item["evidence_kind"] for item in captures}) != 1:
        raise RemoteEvidenceError("capture evidence kinds must not be mixed")
    if len({item["id"] for item in captures}) != len(captures):
        raise RemoteEvidenceError("capture ids must be distinct")
    if len({item["instant"] for item in captures}) != len(captures):
        raise RemoteEvidenceError("capture timestamps must be distinct")
    if len({item["origin_fingerprint"] for item in captures}) != len(captures):
        raise RemoteEvidenceError("capture origin fingerprints must be distinct")
    return sorted(captures, key=lambda item: PROFILE_ORDER[item["route_profile"]])


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collect(captures: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the canonical redacted result for validated captures."""
    capture_kind = captures[0]["evidence_kind"]
    return {
        "capture_kind": capture_kind,
        "captures": [
            {
                "capture_id_fingerprint": _sha256(item["id"]),
                "captured_at": item["captured_at"],
                "network_classification": item["network_classification"],
                "origin_fingerprint": item["origin_fingerprint"],
                "route_profile": item["route_profile"],
            }
            for item in captures
        ],
        "checks": [
            "same_origin",
            "websocket_reconnect",
            "upload_replay",
            "artifact_resume",
            "cancellation",
            "external_network_classification",
            "redaction",
            "provider_neutral_runtime",
        ],
        "collector_version": 1,
        # `evidence_kind` is operator supplied. Even a schema-valid real-shaped
        # bundle remains candidate evidence until an independent external run
        # records/binds its provenance; this offline collector cannot do that.
        "external_acceptance": "pending",
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "evidence_format_validated",
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Atomically write the collector's sole local output."""
    parent = path.parent
    if not parent.is_dir():
        raise RemoteEvidenceError("output directory does not exist")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise RemoteEvidenceError("existing output must be a regular non-symlink file")
    payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=parent
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="local redacted evidence bundle"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="local result manifest"
    )
    args = parser.parse_args(argv)
    try:
        input_path = _absolute_path(args.input)
        output_path = _absolute_path(args.output)
        if input_path == output_path:
            raise RemoteEvidenceError("output must not overwrite input evidence")
        captures = validate_bundle(_read_input(input_path))
        manifest = collect(captures)
        write_manifest(output_path, manifest)
    except (OSError, RemoteEvidenceError) as exc:
        print(f"remote acceptance failed: {exc}", file=sys.stderr)
        return 1
    if manifest["external_acceptance"] == "pending":
        print(
            "remote acceptance contract evidence verified; "
            "real external acceptance remains pending"
        )
    else:
        print("remote acceptance evidence verified")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
