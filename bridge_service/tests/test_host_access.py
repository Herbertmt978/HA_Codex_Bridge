import json
from pathlib import Path

import httpx
import pytest

from codex_bridge_service.host_access import HostAccessError, HostAccessManager, HostPairing
from codex_bridge_service.host_access_contract import HostIdentity


@pytest.fixture
def host(tmp_path):
    identity = HostIdentity(machine_fingerprint="d" * 64, companion_id="a" * 32, hostname="ha-test", os_version="18.3")
    state = {"identity": identity.model_dump(), "session_id": "b" * 32, "ready": True}
    calls = []

    def handle(request):
        assert request.headers["authorization"] == "Bearer " + "s" * 64
        assert request.headers["x-codex-host-api"] == "1"
        calls.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json=state)
        if request.method == "DELETE":
            assert request.headers["x-codex-host-session"] == "b" * 32
            return httpx.Response(200, json={"stopped": True})
        return httpx.Response(200, json={
            "status": "completed", "exit_code": 0, "output": "done", "truncated": False,
        })

    transport = httpx.MockTransport(handle)
    manager = HostAccessManager(tmp_path, transport=transport)
    pairing = HostPairing(host="172.30.33.5", token="s" * 64, companion_id="a" * 32)
    yield manager, pairing, state, calls, transport
    manager.close()


def test_pairing_does_not_grant_and_secret_is_never_public(host):
    manager, pairing, _, _, _ = host
    manager.pair(pairing)
    status = manager.status()
    assert status["state"] == "ready" and status["enabled"] is False
    assert "s" * 64 not in json.dumps(status)
    assert pairing.host not in json.dumps(status)
    assert "s" * 64 not in repr(pairing)
    with pytest.raises(HostAccessError):
        manager.authorise("run-1", None)


def test_warning_acknowledgement_and_current_revision_required(host):
    manager, pairing, _, _, _ = host
    manager.pair(pairing)
    revision = manager.status()["disclosure"]["scope_revision"]
    for old_revision, acknowledged in ((revision, False), ("0" * 64, True)):
        with pytest.raises(HostAccessError):
            manager.enable(old_revision, acknowledged)
    assert manager.status()["enabled"] is False


def grant(manager, pairing):
    manager.pair(pairing)
    status = manager.status()
    return manager.enable(status["disclosure"]["scope_revision"], True)["grant_id"]


def test_execution_requires_current_lease_and_revocation_cancels(host):
    manager, pairing, _, calls, _ = host
    grant_id = grant(manager, pairing)
    lease = manager.authorise("run-1", grant_id)
    result = manager.invoke(lease, {"command": "hostname"})
    assert result["output"] == "done"
    body = json.loads(next(call.content for call in calls if call.url.path == "/execute"))
    assert body["worker_session"] == "b" * 32
    assert body["run_id"] == "run-1"
    manager.revoke()
    assert not manager.active(lease)
    with pytest.raises(HostAccessError):
        manager.invoke(lease, {"command": "hostname"})
    manager.close()
    assert any(call.method == "DELETE" for call in calls)


@pytest.mark.parametrize("change", ["identity", "unavailable", "replace"])
def test_changed_or_lost_environment_invalidates_consent(host, change):
    manager, pairing, state, _, _ = host
    grant_id = grant(manager, pairing)
    lease = manager.authorise("run-1", grant_id)
    if change == "identity":
        state["identity"]["os_version"] = "19.0"
    elif change == "unavailable":
        state["ready"] = False
    else:
        manager.pair(pairing.model_copy(update={"host": "172.30.33.6"}))
    assert manager.status()["enabled"] is False
    assert not manager.active(lease)
    with pytest.raises(HostAccessError):
        manager.authorise("run-2", grant_id)


def test_reenable_does_not_restore_old_task_selection(host):
    manager, pairing, _, _, _ = host
    old = grant(manager, pairing)
    manager.revoke()
    new = grant(manager, pairing)
    assert old != new
    with pytest.raises(HostAccessError):
        manager.authorise("old-task", old)
    assert manager.authorise("new-task", new)


def test_saved_grant_restores_but_live_run_leases_do_not(host, tmp_path):
    manager, pairing, _, _, transport = host
    grant_id = grant(manager, pairing)
    lease = manager.authorise("run-1", grant_id)
    restored = HostAccessManager(tmp_path, transport=transport)
    try:
        assert restored.status()["grant_id"] == grant_id
        assert not restored.active(lease)
    finally:
        restored.close()


def test_revocation_survives_restart_when_disk_cannot_replace_state(host, tmp_path, monkeypatch):
    manager, pairing, _, _, transport = host
    grant_id = grant(manager, pairing)
    lease = manager.authorise("run-1", grant_id)

    def full_disk():
        raise OSError("No space left")

    monkeypatch.setattr(manager, "_save_locked", full_disk)
    assert manager.revoke() == {"enabled": False}
    assert not manager.active(lease)
    restored = HostAccessManager(tmp_path, transport=transport)
    try:
        assert restored.status()["enabled"] is False
    finally:
        restored.close()


def test_revocation_reports_unwritable_storage_and_still_blocks_new_commands(host, monkeypatch):
    manager, pairing, _, _, _ = host
    lease = manager.authorise("run-1", grant(manager, pairing))

    def read_only(*args, **kwargs):
        raise OSError("Read-only filesystem")

    monkeypatch.setattr(manager, "_save_locked", read_only)
    monkeypatch.setattr(Path, "unlink", read_only)
    with pytest.raises(HostAccessError, match="Stop the Host Access App"):
        manager.revoke()
    with pytest.raises(HostAccessError, match="revoked"):
        manager.invoke(lease, {"command": "hostname"})


def test_corrupt_consent_fails_closed(tmp_path):
    (tmp_path / "host-access.json").write_text('{"grant": {"grant_id": "anything"}}')
    manager = HostAccessManager(tmp_path)
    try:
        assert manager.status()["state"] == "not_paired"
        assert manager.status()["enabled"] is False
    finally:
        manager.close()


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.2", "8.8.8.8", "example.com", "http://172.30.33.5"])
def test_discovery_endpoint_is_literal_private_address(address):
    with pytest.raises(ValueError):
        HostPairing(host=address, token="s" * 64, companion_id="a" * 32)


def test_private_store_is_not_written_to_workspace(host, tmp_path):
    manager, pairing, _, _, _ = host
    grant(manager, pairing)
    assert list(tmp_path.iterdir()) == [Path(tmp_path / "host-access.json")]
