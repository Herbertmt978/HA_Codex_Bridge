"""Offline contract tests for redacted remote-path acceptance evidence."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "acceptance"
    / "collect_remote_acceptance.py"
)
MAX_INPUT_BYTES = 256 * 1024
CHUNK_BYTES = 8 * 1024 * 1024


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request(method: str, path: str, status: int, origin: str) -> dict[str, object]:
    return {
        "method": method,
        "path": path,
        "status": status,
        "origin_fingerprint": origin,
        "redirects": 0,
        "cross_origin_requests": 0,
    }


def _capture(
    profile: str,
    *,
    capture_id: str,
    captured_at: str,
    origin: str,
    evidence_kind: str = "synthetic",
) -> dict[str, object]:
    external = profile != "lan"
    thread_path = "/api/codex_bridge/threads/thr_acceptance"
    upload_path = f"{thread_path}/uploads/upl_acceptance"
    cancel_path = f"{thread_path}/uploads/upl_cancel"
    artifact_path = f"{thread_path}/artifacts/art_acceptance"
    chunk = _request("PUT", f"{upload_path}/chunks/0", 200, origin)
    chunk.update(
        {
            "chunk_bytes": CHUNK_BYTES,
            "attempts": 2,
            "commits": 1,
            "response_losses": 1,
            "idempotent_retries": 1,
        }
    )
    return {
        "schema_version": 1,
        "capture": {
            "id": capture_id,
            "captured_at": captured_at,
            "evidence_kind": evidence_kind,
            "origin_fingerprint": origin,
        },
        "route_profile": profile,
        "network": {
            "classification": "external" if external else "lan",
            "observed_from": "external-network" if external else "home-network",
        },
        "runtime_route": "provider-neutral",
        "browser": {
            "origin_fingerprint": origin,
            "redirects": 0,
            "cross_origin_requests": 0,
        },
        "flows": {
            "api": _request("GET", "/api/", 200, origin),
            "websocket": {
                "path": "/api/websocket",
                "origin_fingerprint": origin,
                "auth": "passed",
                "reconnects": 1,
                "event_sequences": [7, 8],
                "duplicate_events": 0,
                "redirects": 0,
                "cross_origin_requests": 0,
            },
            "upload": {
                "create": _request("POST", f"{thread_path}/uploads", 201, origin),
                "status": _request("GET", upload_path, 200, origin),
                "chunk": chunk,
                "complete": _request("POST", f"{upload_path}/complete", 201, origin),
                "cancel": _request("DELETE", cancel_path, 200, origin),
            },
            "artifact": {
                "path": artifact_path,
                "origin_fingerprint": origin,
                "redirects": 0,
                "cross_origin_requests": 0,
                "initial_status": 206,
                "if_range": True,
                "interrupted_after_bytes": 16,
                "resume_range_start": 16,
                "resume_status": 206,
                "unsatisfied_status": 416,
            },
        },
    }


def bundle(*, evidence_kind: str = "synthetic") -> dict[str, object]:
    return {
        "schema_version": 1,
        "captures": [
            _capture(
                "lan",
                capture_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                captured_at="2026-07-17T10:00:00Z",
                origin="1" * 64,
                evidence_kind=evidence_kind,
            ),
            _capture(
                "nabu-shaped",
                capture_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                captured_at="2026-07-17T10:01:00+00:00",
                origin="2" * 64,
                evidence_kind=evidence_kind,
            ),
            _capture(
                "cloudflare-shaped",
                capture_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                captured_at="2026-07-17T10:02:00.123456Z",
                origin="3" * 64,
                evidence_kind=evidence_kind,
            ),
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _run(
    tmp_path: Path, value: object
) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = tmp_path / "capture.json"
    output = tmp_path / "manifest.json"
    _write_json(source, value)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True,
        check=False,
        text=True,
    )
    return result, output


def _captures(value: dict[str, object]) -> list[dict[str, Any]]:
    captures = value["captures"]
    assert isinstance(captures, list)
    return captures  # type: ignore[return-value]


def _replace_origin(capture: dict[str, Any], origin: str) -> None:
    capture["capture"]["origin_fingerprint"] = origin
    capture["browser"]["origin_fingerprint"] = origin
    flows = capture["flows"]
    flows["api"]["origin_fingerprint"] = origin
    flows["websocket"]["origin_fingerprint"] = origin
    for request in flows["upload"].values():
        request["origin_fingerprint"] = origin
    flows["artifact"]["origin_fingerprint"] = origin


def _load_collector() -> Any:
    spec = importlib.util.spec_from_file_location("collect_remote_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_profiles_pass_contract_but_leave_external_acceptance_pending(
    tmp_path: Path,
) -> None:
    result, output = _run(tmp_path, bundle())

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest == {
        "capture_kind": "synthetic",
        "captures": [
            {
                "capture_id_fingerprint": _sha256(
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                ),
                "captured_at": "2026-07-17T10:00:00Z",
                "network_classification": "lan",
                "origin_fingerprint": "1" * 64,
                "route_profile": "lan",
            },
            {
                "capture_id_fingerprint": _sha256(
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
                ),
                "captured_at": "2026-07-17T10:01:00+00:00",
                "network_classification": "external",
                "origin_fingerprint": "2" * 64,
                "route_profile": "nabu-shaped",
            },
            {
                "capture_id_fingerprint": _sha256(
                    "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
                ),
                "captured_at": "2026-07-17T10:02:00.123456Z",
                "network_classification": "external",
                "origin_fingerprint": "3" * 64,
                "route_profile": "cloudflare-shaped",
            },
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
        "external_acceptance": "pending",
        "schema_version": 1,
        "status": "evidence_format_validated",
    }
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []
    assert "verified" in result.stdout


def test_real_shaped_profiles_require_distinct_proof_but_cannot_self_certify_acceptance(
    tmp_path: Path,
) -> None:
    result, output = _run(tmp_path, bundle(evidence_kind="real"))

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["capture_kind"] == "real"
    assert manifest["external_acceptance"] == "pending"
    assert manifest["status"] == "evidence_format_validated"
    assert len({item["capture_id_fingerprint"] for item in manifest["captures"]}) == 3
    assert len({item["captured_at"] for item in manifest["captures"]}) == 3
    assert len({item["origin_fingerprint"] for item in manifest["captures"]}) == 3


@pytest.mark.parametrize(
    ("profile", "classification", "observed_from"),
    [
        ("lan", "external", "external-network"),
        ("nabu-shaped", "lan", "home-network"),
        ("cloudflare-shaped", "external", "home-network"),
    ],
)
def test_rejects_wrong_external_network_classification(
    tmp_path: Path, profile: str, classification: str, observed_from: str
) -> None:
    value = bundle(evidence_kind="real")
    capture = next(
        item for item in _captures(value) if item["route_profile"] == profile
    )
    capture["network"] = {
        "classification": classification,
        "observed_from": observed_from,
    }

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("field", ["id", "captured_at", "origin_fingerprint"])
def test_rejects_replayed_capture_identity_time_or_origin(
    tmp_path: Path, field: str
) -> None:
    value = bundle(evidence_kind="real")
    captures = _captures(value)
    repeated = captures[0]["capture"][field]
    if field == "origin_fingerprint":
        _replace_origin(captures[1], repeated)
    else:
        captures[1]["capture"][field] = repeated

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()
    assert "distinct" in result.stderr


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda item: item.update(runtime_route="nabu-casa"), "provider-neutral"),
        (lambda item: item["browser"].update(redirects=1), "redirect"),
        (
            lambda item: item["flows"]["api"].update(cross_origin_requests=1),
            "cross-origin",
        ),
        (
            lambda item: item["flows"]["websocket"].update(duplicate_events=1),
            "duplicate",
        ),
        (
            lambda item: item["flows"]["websocket"].update(event_sequences=[7, 7]),
            "duplicate",
        ),
        (lambda item: item["flows"]["upload"]["chunk"].update(commits=2), "commit"),
        (
            lambda item: item["flows"]["upload"]["chunk"].update(
                chunk_bytes=CHUNK_BYTES + 1
            ),
            "8 MiB",
        ),
        (
            lambda item: item["flows"]["artifact"].update(resume_range_start=15),
            "resume",
        ),
        (
            lambda item: item["flows"]["upload"]["create"].update(
                path="https://bridge.invalid/upload"
            ),
            "relative",
        ),
        (
            lambda item: item["flows"]["upload"]["cancel"].update(
                path=item["flows"]["upload"]["status"]["path"]
            ),
            "separate upload",
        ),
    ],
)
def test_rejects_unsafe_or_non_idempotent_flow_evidence(
    tmp_path: Path, mutation: Any, expected: str
) -> None:
    value = bundle()
    mutation(_captures(value)[1])

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()
    assert expected in result.stderr
    assert "bridge.invalid" not in result.stderr


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("capture", "access_token"),
        ("browser", "cookie"),
        ("network", "app_url"),
        ("flows", "upstream_url"),
    ],
)
def test_rejects_secret_or_private_endpoint_fields_without_echoing_values(
    tmp_path: Path, target: str, field: str
) -> None:
    value = bundle()
    _captures(value)[0][target][field] = "https://private.invalid/sensitive-value"

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()
    assert "private.invalid" not in result.stderr
    assert "sensitive-value" not in result.stderr


def test_rejects_mixed_capture_kinds_and_wrong_profile_set(tmp_path: Path) -> None:
    mixed = bundle()
    _captures(mixed)[2]["capture"]["evidence_kind"] = "real"
    result, output = _run(tmp_path, mixed)
    assert result.returncode == 1
    assert not output.exists()

    profiles = bundle()
    _captures(profiles)[2]["route_profile"] = "nabu-shaped"
    result, output = _run(tmp_path, profiles)
    assert result.returncode == 1
    assert not output.exists()


def test_malformed_enum_fails_without_an_unhandled_traceback(tmp_path: Path) -> None:
    value = bundle()
    _captures(value)[0]["capture"]["evidence_kind"] = []

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()
    assert "Traceback" not in result.stderr


def test_reader_rejects_same_inode_metadata_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _load_collector()
    source = tmp_path / "capture.json"
    _write_json(source, bundle())
    real_fstat = os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> Any:
        nonlocal calls
        calls += 1
        metadata = real_fstat(descriptor)
        if calls < 2:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns + 1,
            st_ctime_ns=metadata.st_ctime_ns,
        )

    monkeypatch.setattr(collector.os, "fstat", changed_fstat)

    with pytest.raises(collector.RemoteEvidenceError, match="changed"):
        collector._read_input(source)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=True),
        lambda value: _captures(value)[0]["browser"].update(redirects=False),
        lambda value: _captures(value)[0]["flows"]["websocket"].update(
            duplicate_events=False
        ),
        lambda value: _captures(value)[0]["flows"]["upload"]["chunk"].update(
            commits=True
        ),
    ],
)
def test_rejects_boolean_values_in_integer_contract_fields(
    tmp_path: Path, mutation: Any
) -> None:
    value = bundle()
    mutation(value)

    result, output = _run(tmp_path, value)

    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"schema_version":1,"captures":[]}',
        b'{"schema_version":NaN,"captures":[]}',
        b"not json",
    ],
)
def test_rejects_malformed_or_duplicate_json(tmp_path: Path, raw: bytes) -> None:
    source = tmp_path / "capture.json"
    output = tmp_path / "manifest.json"
    source.write_bytes(raw)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert not output.exists()


def test_rejects_oversized_input_and_preserves_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "capture.json"
    output = tmp_path / "manifest.json"
    source.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    output.write_text("existing\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert output.read_text(encoding="utf-8") == "existing\n"


def test_collector_has_no_home_assistant_or_network_client() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imports.isdisjoint(
        {"aiohttp", "homeassistant", "httpx", "requests", "socket", "urllib"}
    )
