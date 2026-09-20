from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex_bridge_service.host_access_contract import (
    HostCommand,
    HostIdentity,
    MAX_COMMAND_BYTES,
    host_dynamic_tool_spec,
)


def identity(**changes):
    return HostIdentity.model_validate({
        "machine_fingerprint": "d" * 64, "companion_id": "a" * 32, "hostname": "haos-dev", "os_version": "18.3",
        **changes,
    })


def test_disclosure_does_not_promise_credential_or_network_isolation():
    disclosure = identity().disclosure()
    words = " ".join(item["description"] for item in disclosure["warnings"])
    for expected in (
        "root", "delete", "credentials", "local-network", "Proxmox",
        "model provider", "household automations", "cannot undo", "continues",
    ):
        assert expected in words
    assert disclosure["hostname"] == "haos-dev"
    assert disclosure["execution_user"] == "root"
    assert "while I am absent" in disclosure["scheduled_acknowledgement"]


@pytest.mark.parametrize("change", [
    {"machine_fingerprint": "e" * 64}, {"companion_id": "b" * 32}, {"hostname": "other-ha"}, {"os_version": "18.4"},
])
def test_changed_machine_or_installation_invalidates_consent(change):
    assert identity(**change).scope_revision != identity().scope_revision


@pytest.mark.parametrize("version", ["18.3", "18.2.dev20260718", "18.0.rc1"])
def test_haos_stable_and_development_versions_are_disclosed_exactly(version):
    assert identity(os_version=version).disclosure()["os_version"] == version


@pytest.mark.parametrize("change", [
    {"protocol_version": 2}, {"disclosure_version": 2},
    {"operating_system": "Ubuntu"}, {"execution_user": "unknown"},
    {"hostname": "ha\nother"}, {"network": "public-only"},
    {"token": "must-never-be-projected"},
])
def test_unknown_or_misleading_identity_fails_closed(change):
    with pytest.raises(ValidationError):
        identity(**change)


@pytest.mark.parametrize("command", [
    {"command": "echo hi", "timeout_seconds": True},
    {"command": "echo hi", "timeout_seconds": 301},
    {"command": "echo hi", "cwd": "relative"},
    {"command": "echo\0hi"},
    {"command": "é" * MAX_COMMAND_BYTES},
    {"command": "echo hi", "environment": {"TOKEN": "secret"}},
])
def test_command_wire_limits_are_strict(command):
    with pytest.raises(ValidationError):
        HostCommand.model_validate(command)


def test_authorised_host_commands_are_not_mislabelled_as_workspace_commands():
    command = HostCommand(command="cat /etc/os-release", cwd="/mnt/data")
    assert command.cwd == "/mnt/data"
    spec = host_dynamic_tool_spec()
    assert spec["name"] == "ha_host"
    assert "root" in spec["description"]
    assert [tool["name"] for tool in spec["tools"]] == ["execute"]
    assert spec["tools"][0]["type"] == "function"
    assert spec["tools"][0]["deferLoading"] is False
