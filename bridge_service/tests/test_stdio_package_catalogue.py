"""The image catalogue must reject altered bytes and run a real MCP server."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from codex_bridge_service import stdio_package_catalogue as catalogue


ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "scripts" / "stage_stdio_packages.py"


@pytest.fixture
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "stdio-packages"
    subprocess.run([sys.executable, str(STAGE), "--output", str(target)], check=True)
    monkeypatch.setattr(catalogue, "PACKAGE_ROOT", target)
    monkeypatch.setattr(catalogue, "REQUIRED_OWNER_UID", target.stat().st_uid)
    return target


def test_staged_package_is_byte_verified_and_advertises_one_revision(staged: Path) -> None:
    verified = catalogue.verify_package("bridge-time", "1.0.0")
    assert verified.package_path == staged / "bridge-time" / "1.0.0"
    assert verified.entrypoint == ("python", "-m", "codex_bridge_time")
    assert verified.tools == ("get_current_time", "convert_time")
    assert catalogue.packaged_revisions("bridge-time") == ("1.0.0",)
    assert catalogue.previous_revision("bridge-time", "1.0.0") is None
    summary = catalogue.list_packages()
    assert len(summary) == 1
    assert summary[0]["title"] == "Time and timezone"
    assert summary[0]["network"] == summary[0]["files"] == "none"
    assert summary[0]["rollback_available"] is False
    assert len(summary[0]["digest"]) == 64
    assert "package_path" not in summary[0]
    with pytest.raises(catalogue.PackageVerificationError, match="unknown"):
        catalogue.verify_package("../private", "1.0.0")


def test_image_staging_rejects_a_changed_source_before_copy(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("stage_stdio_packages_test", STAGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "codex_bridge_app" / "stdio_packages" / "bridge-time" / "1.0.0"
    approved = ROOT / "codex_bridge_app" / "stdio_packages" / "bridge-time" / "1.0.0"
    shutil.copytree(approved, source)
    implementation = source / "codex_bridge_time" / "__main__.py"
    implementation.write_bytes(implementation.read_bytes() + b"\n# changed\n")
    module.ROOT = tmp_path
    with pytest.raises(module.StagePackageError, match="reviewed lock"):
        module.stage_catalogue(tmp_path / "output")


def test_staging_can_carry_a_future_revision_for_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The release ships one revision. This exercises the multi-revision image
    # mechanism without advertising or fabricating a previous release.
    current = catalogue.catalogue()[0]
    future = replace(current, revision="1.0.1")
    monkeypatch.setattr(catalogue, "_CATALOGUE", (current, future))
    spec = importlib.util.spec_from_file_location("stage_stdio_packages_future_test", STAGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_root = tmp_path / "codex_bridge_app" / "stdio_packages" / "bridge-time"
    approved = ROOT / "codex_bridge_app" / "stdio_packages" / "bridge-time" / "1.0.0"
    shutil.copytree(approved, source_root / "1.0.0")
    shutil.copytree(approved, source_root / "1.0.1")
    module.ROOT = tmp_path
    output = tmp_path / "output"
    module.stage_catalogue(output)
    monkeypatch.setattr(catalogue, "PACKAGE_ROOT", output)
    monkeypatch.setattr(catalogue, "REQUIRED_OWNER_UID", output.stat().st_uid)
    assert catalogue.verify_package("bridge-time", "1.0.1").revision == "1.0.1"
    assert catalogue.previous_revision("bridge-time", "1.0.1") == "1.0.0"


def test_package_mutation_and_extra_entries_fail_closed(staged: Path) -> None:
    package = staged / "bridge-time" / "1.0.0"
    source = package / "codex_bridge_time" / "__main__.py"
    source.chmod(0o644)
    source.write_bytes(source.read_bytes() + b"\n# altered\n")
    source.chmod(0o444)
    with pytest.raises(catalogue.PackageVerificationError, match="digest"):
        catalogue.verify_package("bridge-time", "1.0.0")
    with pytest.raises(catalogue.PackageVerificationError, match="digest"):
        catalogue.list_packages()

    # Restore exact approved bytes to isolate the extra-entry check.
    approved = ROOT / "codex_bridge_app" / "stdio_packages" / "bridge-time" / "1.0.0"
    source.chmod(0o644)
    source.write_bytes((approved / "codex_bridge_time" / "__main__.py").read_bytes())
    source.chmod(0o444)
    package.chmod(0o755)
    extra = package / "extra.py"
    extra.write_text("pass\n", encoding="utf-8")
    extra.chmod(0o444)
    package.chmod(0o555)
    with pytest.raises(catalogue.PackageVerificationError, match="undeclared"):
        catalogue.verify_package("bridge-time", "1.0.0")


def test_package_rejects_symlink_and_manifest_changes(staged: Path) -> None:
    package = staged / "bridge-time" / "1.0.0"
    manifest = package / "manifest.json"
    manifest.chmod(0o644)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entrypoint"] = ["python", "-c", "print('unsafe')"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    manifest.chmod(0o444)
    with pytest.raises(catalogue.PackageVerificationError, match="manifest"):
        catalogue.verify_package("bridge-time", "1.0.0")

    if os.name != "nt":
        package.chmod(0o755)
        manifest.unlink()
        manifest.symlink_to(package / "codex_bridge_time" / "__init__.py")
        package.chmod(0o555)
        with pytest.raises(catalogue.PackageVerificationError, match="non-regular"):
            catalogue.verify_package("bridge-time", "1.0.0")


def test_server_initialises_lists_and_calls_real_time_tools(staged: Path) -> None:
    verified = catalogue.verify_package("bridge-time", "1.0.0")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_current_time", "arguments": {"timezone": "UTC"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "convert_time", "arguments": {"time": "2026-09-25T12:00:00+00:00", "timezone": "Europe/London"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "convert_time", "arguments": {"time": "2026-09-25T12:00:00", "timezone": "UTC"}}},
    ]
    payload = b"".join(json.dumps(item).encode() + b"\n" for item in requests)
    env = {"PYTHONPATH": str(verified.package_path), "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.run(
        [sys.executable, "-m", "codex_bridge_time"],
        input=payload,
        capture_output=True,
        env=env,
        check=True,
        timeout=5,
    )
    assert process.stderr == b""
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    assert [message["id"] for message in messages] == [1, 2, 3, 4, 5]
    assert messages[0]["result"]["protocolVersion"] == "2025-06-18"
    assert [tool["name"] for tool in messages[1]["result"]["tools"]] == list(verified.tools)
    now = json.loads(messages[2]["result"]["content"][0]["text"])
    if messages[3]["result"]["isError"]:
        # Windows has no system IANA database. The App's Alpine base image is
        # checked separately with its system timezone data.
        assert messages[3]["result"]["isError"] is True
        converted = None
    else:
        converted = json.loads(messages[3]["result"]["content"][0]["text"])
    assert now["timezone"] == "UTC" and now["time"].endswith("+00:00")
    if converted is not None:
        assert converted == {"time": "2026-09-25T13:00:00+01:00", "timezone": "Europe/London"}
    assert messages[4]["result"]["isError"] is True


def test_oversized_protocol_line_exits_without_running_tool(staged: Path) -> None:
    verified = catalogue.verify_package("bridge-time", "1.0.0")
    process = subprocess.run(
        [sys.executable, "-m", "codex_bridge_time"],
        input=b"{" + b"x" * (64 * 1024) + b"}\n",
        capture_output=True,
        env={"PYTHONPATH": str(verified.package_path), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=5,
    )
    assert process.returncode == 2
    assert process.stdout == b""
