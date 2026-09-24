"""HA-owned ChatGPT profiles never expose credentials or cross account state."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.account import account_owner_marker
from codex_bridge_service.account_profiles import AccountProfileError, AccountProfileStore
from codex_bridge_service.auth_coordinator import (
    AuthOperationConflictError,
    CodexAuthCoordinator,
)
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_gate import RuntimeGate
from codex_bridge_service.routes.codex_auth import router as auth_router


pytestmark = pytest.mark.skipif(os.name == "nt", reason="HA credential storage requires Linux")


def _credential(account_id: str) -> bytes:
    return json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": account_id}}).encode()


def _jwt(account_id: str) -> str:
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class _AccountClient:
    def __init__(self, store: AccountProfileStore) -> None:
        self.store = store
        self.generation = 1
        self.restart_count = 0
        self.invalid_ids: set[str] = set()
        self.read_params: list[object] = []
        self.fail_next_restart = False
        self.provider_unavailable_ids: set[str] = set()

    def register_notification_handler(self, _method, _handler) -> None:
        return None

    def request(self, method, params=None, *, timeout_seconds=None):
        assert method in {"account/read", "account/rateLimits/read"}
        if method == "account/read":
            self.read_params.append(params)
        try:
            _, account_id = self.store.current_credential()
        except (AccountProfileError, FileNotFoundError):
            account_id = None
        if method == "account/rateLimits/read":
            if account_id in self.provider_unavailable_ids:
                raise RuntimeError("provider rejected the token")
            return {"rateLimits": {}}
        if account_id is None or account_id in self.invalid_ids:
            return {"account": None, "requiresOpenaiAuth": True}
        return {
            "account": {
                "type": "chatgpt",
                "email": "shared@example.test",
                "planType": "pro",
            },
            "requiresOpenaiAuth": True,
        }

    def restart_for_account_change(self) -> None:
        self.restart_count += 1
        if self.fail_next_restart:
            self.fail_next_restart = False
            raise RuntimeError("app-server restart failed")
        self.generation += 1


def test_same_email_profiles_switch_verify_and_restore_failed_target(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    store = AccountProfileStore(tmp_path / "private-profiles", codex_home)
    first_id = "account_personal"
    second_id = "account_workspace"
    store.activate_credential(_credential(first_id))
    client = _AccountClient(store)
    gate = RuntimeGate(limits=ResourceLimits())
    bindings: list[str] = []
    coordinator = CodexAuthCoordinator(
        client,
        runtime_gate=gate,
        account_owner_secret="private-bridge-secret",
        account_binding_listener=bindings.append,
        account_identity_provider=lambda: store.current_credential()[1],
    )
    try:
        assert coordinator.start().state == "ok"
        first = coordinator.save_account_profile(store, "Personal")
        assert client.read_params[-1] == {"refreshToken": True}
        store.activate_credential(_credential(second_id))
        client.generation += 1
        assert coordinator.reconcile_after_restart().state == "ok"
        second = coordinator.save_account_profile(store, "Workspace")
        assert client.read_params[-1] == {"refreshToken": True}
        assert first["id"] != second["id"]
        assert bindings[0] != bindings[1]
        assert account_owner_marker(
            client.request("account/read"), "private-bridge-secret", account_id=first_id
        ) != account_owner_marker(
            client.request("account/read"), "private-bridge-secret", account_id=second_id
        )

        prompt = gate.reserve_prompt(client_request_id="running")
        with pytest.raises(AuthOperationConflictError):
            coordinator.switch_account_profile(store, first["id"])
        assert store.current_credential()[1] == second_id
        prompt.release()

        assert coordinator.switch_account_profile(store, first["id"]).state == "ok"
        assert client.restart_count == 1
        assert client.read_params[-1] == {"refreshToken": True}
        assert store.current_credential()[1] == first_id
        assert store.list_profiles() == [
            {"id": first["id"], "label": "Personal", "plan": "pro", "active": True},
            {"id": second["id"], "label": "Workspace", "plan": "pro", "active": False},
        ]
        assert second_id not in repr(store.list_profiles())

        client.invalid_ids.add(second_id)
        with pytest.raises(AccountProfileError, match="new sign-in"):
            coordinator.switch_account_profile(store, second["id"])
        assert store.current_credential()[1] == first_id
        assert coordinator.status().state == "ok"
        assert client.restart_count == 3

        client.invalid_ids.remove(second_id)
        client.provider_unavailable_ids.add(second_id)
        with pytest.raises(AccountProfileError, match="could not be verified"):
            coordinator.switch_account_profile(store, second["id"])
        assert store.current_credential()[1] == first_id
        assert coordinator.status().state == "ok"
        assert client.restart_count == 5

        client.fail_next_restart = True
        with pytest.raises(AccountProfileError, match="could not be completed"):
            coordinator.switch_account_profile(store, second["id"])
        assert store.current_credential()[1] == first_id
        assert coordinator.status().state == "ok"
        assert client.restart_count == 7

        client.provider_unavailable_ids.add(first_id)
        with pytest.raises(AccountProfileError, match="previous sign-in needs checking"):
            coordinator.switch_account_profile(store, second["id"])
        assert store.current_credential()[1] == first_id
        recovery = coordinator.status()
        assert recovery.auth_required is True
        assert recovery.reauthentication_required is True
        assert recovery.state in {"unavailable", "expired"}
        assert gate.snapshot().auth_mutation_active is False

        coordinator.remove_account_profile(store, second["id"])
        assert len(store.list_profiles()) == 1
        with pytest.raises(AccountProfileError, match="Switch away"):
            coordinator.remove_account_profile(store, first["id"])
    finally:
        coordinator.close()
        gate.close()
        store.close()


def test_saved_accounts_have_a_bounded_limit(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "private-profiles", tmp_path / "codex-home")
    response = {"account": {"type": "chatgpt", "planType": "pro"}}
    try:
        for index in range(16):
            store.activate_credential(_credential(f"account_{index}"))
            store.save_current(f"Account {index}", response)
        assert len(store.list_profiles()) == 16
        store.activate_credential(_credential("account_over_limit"))
        with pytest.raises(AccountProfileError, match="limit"):
            store.save_current("Too many", response)
        assert len(store.list_profiles()) == 16
    finally:
        store.close()


def test_cannot_save_a_cached_identity_with_rejected_provider_token(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "private-profiles", tmp_path / "codex-home")
    store.activate_credential(_credential("expired_account"))
    client = _AccountClient(store)
    client.provider_unavailable_ids.add("expired_account")
    gate = RuntimeGate(limits=ResourceLimits())
    coordinator = CodexAuthCoordinator(client, runtime_gate=gate)
    try:
        assert coordinator.start().state == "ok"
        with pytest.raises(AccountProfileError, match="could not be verified"):
            coordinator.save_account_profile(store, "Expired")
        assert store.list_profiles() == []
        assert gate.snapshot().auth_mutation_active is False
    finally:
        coordinator.close()
        gate.close()
        store.close()


def test_saved_credentials_require_private_storage(tmp_path: Path) -> None:
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    os.chmod(insecure, 0o755)
    with pytest.raises(AccountProfileError, match="not private"):
        AccountProfileStore(insecure, tmp_path / "codex-home")

    root = tmp_path / "private-profiles"
    codex_home = tmp_path / "codex-home"
    store = AccountProfileStore(root, codex_home)
    try:
        store.activate_credential(_credential("private_account"))
        store.save_current(
            "Private", {"account": {"type": "chatgpt", "planType": "pro"}}
        )
        assert stat.S_IMODE((root / "profiles.json").stat().st_mode) == 0o600
        assert stat.S_IMODE((codex_home / "auth.json").stat().st_mode) == 0o600
        profile = store.list_profiles()[0]
        assert stat.S_IMODE((root / profile["id"] / "auth.json").stat().st_mode) == 0o600
    finally:
        store.close()


def test_rollback_restores_a_non_chatgpt_native_auth_file(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "private-profiles", tmp_path / "codex-home")
    native_api_key_auth = json.dumps({"auth_mode": "apiKey", "api_key": "test-only"}).encode()
    try:
        store.activate_credential(_credential("saved_chatgpt"))
        profile = store.save_current(
            "Saved", {"account": {"type": "chatgpt", "planType": "pro"}}
        )
        store.restore_credential(native_api_key_auth)
        assert store.current_credential_optional() == native_api_key_auth
        assert store.list_profiles() == [{**profile, "active": False}]
    finally:
        store.close()


def test_native_token_claim_precedes_the_account_id_fallback(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "private-profiles", tmp_path / "codex-home")
    try:
        raw = json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {"id_token": _jwt("claim_account"), "account_id": "stale_fallback"},
        }).encode()
        store.activate_credential(raw)
        assert store.current_credential()[1] == "claim_account"
        assert store.save_current(
            "Claim", {"account": {"type": "chatgpt", "planType": "pro"}}
        )["active"] is True
    finally:
        store.close()


def test_profile_routes_keep_credentials_private_and_require_bridge_auth(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "private-profiles", tmp_path / "codex-home")
    store.activate_credential(_credential("private_account_identity"))
    client = _AccountClient(store)
    gate = RuntimeGate(limits=ResourceLimits())
    coordinator = CodexAuthCoordinator(client, runtime_gate=gate)
    app = FastAPI()
    app.state.auth_token = "bridge-test-secret"
    app.state.account_profile_store = store
    app.state.auth_coordinator = coordinator
    app.include_router(auth_router)
    headers = {
        "Authorization": "Bearer bridge-test-secret",
        "X-Codex-Bridge-Api": "1",
    }
    try:
        coordinator.start()
        with TestClient(app) as http:
            assert http.get("/auth/profiles").status_code == 401
            assert http.post("/auth/profiles", json={"label": "Home"}).status_code == 401
            saved = http.post("/auth/profiles", headers=headers, json={"label": "Home"})
            assert saved.status_code == 200
            profile_id = saved.json()["id"]
            listed = http.get("/auth/profiles", headers=headers)
            assert listed.status_code == 200
            assert listed.json() == [{
                "id": profile_id, "label": "Home", "plan": "pro", "active": True,
            }]
            for response in (saved, listed):
                assert "private_account_identity" not in response.text
                assert "tokens" not in response.text
                assert "bridge-test-secret" not in response.text
            active_removal = http.delete(f"/auth/profiles/{profile_id}", headers=headers)
            assert active_removal.status_code == 409
    finally:
        coordinator.close()
        gate.close()
        store.close()
