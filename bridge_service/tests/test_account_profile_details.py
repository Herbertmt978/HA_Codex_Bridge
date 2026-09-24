"""Saved account summaries stay isolated from the active chat and public API."""

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.account_profile_details import AccountProfileDetailsProbe
from codex_bridge_service.account_profiles import AccountProfileStore
from codex_bridge_service.models import LimitsStatusRecord, LimitsWindowRecord
from codex_bridge_service.routes.codex_auth import router as auth_router


pytestmark = pytest.mark.skipif(os.name == "nt", reason="HA credential storage requires Linux")


def _credential(account_id: str, *, marker: str = "") -> bytes:
    return json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": account_id, "marker": marker}}).encode()


class _IsolatedClient:
    def __init__(self, *, codex_home: Path, auth_valid: bool = True, **_options) -> None:
        self.home = codex_home
        self.auth_valid = auth_valid

    def __enter__(self):
        assert (self.home.stat().st_mode & 0o077) == 0
        assert ((self.home / "auth.json").stat().st_mode & 0o077) == 0
        return self

    def __exit__(self, *_args):
        return None

    def request(self, method, params=None, *, timeout_seconds=None):
        assert timeout_seconds == 5.0
        if method == "account/read":
            assert params == {"refreshToken": True}
            if not self.auth_valid:
                return {"account": None}
            return {"account": {"type": "chatgpt", "planType": "plus"}}
        assert method == "account/rateLimits/read"
        return {
            "accountId": "saved-account",
            "rateLimits": {
                "planType": "plus",
                "primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 2_000_000_000},
                "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 2_000_100_000},
            },
            "rateLimitResetCredits": {
                "availableCount": 2,
                "credits": [
                    {"id": "private-credit-1", "status": "available", "expiresAt": 2_000_200_000},
                    {"id": "private-credit-2", "status": "available", "expiresAt": 2_000_150_000},
                ],
            },
        }


def test_inactive_profile_uses_private_isolated_client_and_projects_only_safe_details(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "profiles", tmp_path / "codex")
    try:
        store.activate_credential(_credential("saved-account"))
        saved = store.save_current("Saved", {"account": {"type": "chatgpt", "planType": "pro"}})
        store.activate_credential(_credential("active-account"))
        store.save_current("Active", {"account": {"type": "chatgpt", "planType": "pro"}})
        active_before = store.current_credential()[0]
        homes = []

        def factory(**options):
            homes.append(options["codex_home"])
            return _IsolatedClient(**options)

        probe = AccountProfileDetailsProbe(
            store, codex_command="codex", active_limits=lambda: None,
            client_factory=factory,
        )
        details = probe.read(saved["id"])
        assert details["status"] == "available"
        assert details["plan"] == "plus"
        assert [item["remaining_percent"] for item in details["windows"]] == [80, 60]
        assert details["available_resets"] == 2
        assert details["next_reset_expiry"] == 2_000_150_000
        assert "private-credit" not in json.dumps(details)
        assert store.current_credential()[0] == active_before
        assert len(homes) == 1 and not homes[0].exists()
        assert probe.read(saved["id"]) == details
        assert len(homes) == 1
    finally:
        store.close()


def test_unverified_inactive_sign_in_never_reports_another_accounts_usage(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "profiles", tmp_path / "codex")
    try:
        store.activate_credential(_credential("saved-account"))
        saved = store.save_current("Saved", {"account": {"type": "chatgpt", "planType": "pro"}})
        store.activate_credential(_credential("active-account"))
        store.save_current("Active", {"account": {"type": "chatgpt", "planType": "pro"}})
        probe = AccountProfileDetailsProbe(
            store, codex_command="codex", active_limits=lambda: None,
            client_factory=lambda **options: _IsolatedClient(auth_valid=False, **options),
        )
        details = probe.read(saved["id"])
        assert details["status"] == "reauthentication_required"
        assert details["windows"] == []
        assert details["available_resets"] is None
    finally:
        store.close()


def test_active_profile_uses_shared_limits_without_spawning_another_process(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "profiles", tmp_path / "codex")
    try:
        store.activate_credential(_credential("active-account"))
        active = store.save_current("Active", {"account": {"type": "chatgpt", "planType": "pro"}})
        limits = LimitsStatusRecord(
            available=True,
            primary=LimitsWindowRecord(remaining_percent=45, resets_at=2_000_000_000),
            reset_credits={"available_count": 0, "credits": []},
            plan_type="pro",
        )
        probe = AccountProfileDetailsProbe(
            store, codex_command="codex", active_limits=lambda: limits,
            client_factory=lambda **_options: pytest.fail("active profile launched a second process"),
        )
        details = probe.read(active["id"])
        assert details["windows"][0]["remaining_percent"] == 45
        assert details["available_resets"] == 0
        assert details["next_reset_expiry"] is None
    finally:
        store.close()


def test_profile_details_route_requires_bridge_auth_and_advertised_capability(tmp_path: Path) -> None:
    store = AccountProfileStore(tmp_path / "profiles", tmp_path / "codex")
    try:
        store.activate_credential(_credential("active-account"))
        active = store.save_current("Active", {"account": {"type": "chatgpt", "planType": "pro"}})
        app = FastAPI()
        app.state.auth_token = "bridge-test-secret"
        app.state.account_profile_store = store
        app.state.auth_coordinator = object()
        app.state.feature_capabilities = ("account_profile_details_v1",)
        app.state.account_profile_details = AccountProfileDetailsProbe(
            store, codex_command="codex", active_limits=lambda: LimitsStatusRecord(available=True),
        )
        app.include_router(auth_router)
        headers = {"Authorization": "Bearer bridge-test-secret", "X-Codex-Bridge-Api": "1"}
        with TestClient(app) as http:
            url = f"/auth/profiles/{active['id']}/details"
            assert http.get(url).status_code == 401
            result = http.get(url, headers=headers)
            assert result.status_code == 200
            assert result.json()["plan"] == "pro"
            assert "active-account" not in result.text
            app.state.feature_capabilities = ()
            assert http.get(url, headers=headers).status_code == 409
    finally:
        store.close()
