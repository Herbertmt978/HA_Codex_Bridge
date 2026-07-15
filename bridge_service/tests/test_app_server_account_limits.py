from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from inspect import getsource
from math import inf, nan
from threading import Event, Lock
from typing import Any

import pytest

import codex_bridge_service.account as account_module
import codex_bridge_service.limits as limits_module
from codex_bridge_service.codex_app_server_contract import (
    AppServerProtocolValidator,
    load_bundled_protocol_contract,
)
from codex_bridge_service.models import CodexAccountRecord, LimitsStatusRecord


@dataclass(frozen=True, slots=True)
class AppServerCall:
    method: str
    params: Any


class RecordingAppServerClient:
    def __init__(self, *replies: Any) -> None:
        self.calls: list[AppServerCall] = []
        self.timeouts: list[float | None] = []
        self._replies = deque(replies)
        self._lock = Lock()

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        with self._lock:
            self.calls.append(AppServerCall(method, deepcopy(params)))
            self.timeouts.append(timeout_seconds)
            if not self._replies:
                raise AssertionError(f"no scripted reply for {method}")
            reply = self._replies.popleft()
        if isinstance(reply, BaseException):
            raise reply
        return deepcopy(reply)


class BlockingAppServerClient(RecordingAppServerClient):
    def __init__(self, *replies: Any) -> None:
        super().__init__(*replies)
        self.first_entered = Event()
        self.second_entered = Event()
        self.release_first = Event()

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        with self._lock:
            call_number = len(self.calls) + 1
            self.calls.append(AppServerCall(method, deepcopy(params)))
            if not self._replies:
                raise AssertionError(f"no scripted reply for {method}")
            reply = self._replies.popleft()
        if call_number == 1:
            self.first_entered.set()
            if not self.release_first.wait(10):
                raise AssertionError("first app-server request was not released")
        else:
            self.second_entered.set()
        if isinstance(reply, BaseException):
            raise reply
        return deepcopy(reply)


def _account_probe(client: RecordingAppServerClient):
    probe_type = getattr(account_module, "AppServerAccountProbe")
    return probe_type(client)


def _limits_probe(
    client: RecordingAppServerClient,
    *,
    min_fetch_interval_seconds: int = 0,
):
    probe_type = getattr(limits_module, "AppServerLimitsProbe")
    return probe_type(
        client,
        min_fetch_interval_seconds=min_fetch_interval_seconds,
    )


@lru_cache(maxsize=1)
def _locked_validator() -> AppServerProtocolValidator:
    return AppServerProtocolValidator(load_bundled_protocol_contract())


def _assert_locked_response(method: str, response: object) -> None:
    _locked_validator().validate_client_response(method, result=response)


def _signed_out_account() -> dict[str, Any]:
    response = {"account": None, "requiresOpenaiAuth": True}
    _assert_locked_response("account/read", response)
    return response


def _chatgpt_account(
    *,
    email: str = "private-person@example.test",
    plan_type: str = "plus",
) -> dict[str, Any]:
    response = {
        "account": {
            "type": "chatgpt",
            "email": email,
            "planType": plan_type,
        },
        "requiresOpenaiAuth": True,
    }
    _assert_locked_response("account/read", response)
    return response


def _rate_limits(
    *,
    primary: object = None,
    secondary: object = None,
    credits: object = None,
    plan_type: object = None,
    reached_type: object = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    if primary is not None:
        snapshot["primary"] = primary
    if secondary is not None:
        snapshot["secondary"] = secondary
    if credits is not None:
        snapshot["credits"] = credits
    if plan_type is not None:
        snapshot["planType"] = plan_type
    if reached_type is not None:
        snapshot["rateLimitReachedType"] = reached_type
    response = {"rateLimits": snapshot}
    _assert_locked_response("account/rateLimits/read", response)
    return response


def test_account_probe_reads_chatgpt_account_through_the_shared_client() -> None:
    client = RecordingAppServerClient(_chatgpt_account(plan_type="pro"))

    account = _account_probe(client).probe()

    assert isinstance(account, CodexAccountRecord)
    assert client.calls == [
        AppServerCall("account/read", {"refreshToken": False}),
    ]
    assert client.timeouts == [5.0]
    assert account.available is True
    assert account.auth_mode == "chatgpt"
    assert account.plan_type == "pro"


def test_account_probe_normalizes_signed_out_response() -> None:
    client = RecordingAppServerClient(_signed_out_account())

    account = _account_probe(client).probe()

    assert account == CodexAccountRecord()


@pytest.mark.parametrize(
    ("account_payload", "expected_auth_mode"),
    [
        ({"type": "apiKey"}, "apikey"),
        ({"type": "amazonBedrock"}, "unsupported"),
    ],
)
def test_account_probe_blocks_locked_non_chatgpt_account_types(
    account_payload: dict[str, Any],
    expected_auth_mode: str,
) -> None:
    response = {
        "account": account_payload,
        "requiresOpenaiAuth": True,
    }
    _assert_locked_response("account/read", response)
    client = RecordingAppServerClient(response)

    account = _account_probe(client).probe()

    assert account.available is False
    assert account.auth_mode == expected_auth_mode
    assert account.plan_type is None


def test_account_probe_never_projects_identity_or_credentials() -> None:
    email = "private-person@example.test"
    account_id = "account-secret-123"
    access_token = "reusable-access-token-123"
    response = _chatgpt_account(email=email)
    response["account"].update(  # type: ignore[union-attr]
        {
            "accountId": account_id,
            "accessToken": access_token,
        }
    )
    _assert_locked_response("account/read", response)
    client = RecordingAppServerClient(response)

    account = _account_probe(client).probe()
    projection = f"{account!r} {account.model_dump_json()}"

    assert account.email is None
    assert account.name is None
    assert account.account_id is None
    assert account.user_id is None
    assert account.organization_id is None
    assert account.organization_title is None
    assert email not in projection
    assert account_id not in projection
    assert access_token not in projection


@pytest.mark.parametrize(
    ("account_type", "plan_type"),
    [
        ("CHATGPT", "pro"),
        ("chatgpt", "private-enterprise-plan"),
        ({"unexpected": "shape"}, "plus"),
        ("chatgpt", {"unexpected": "shape"}),
    ],
)
def test_account_probe_fail_closes_invalid_auth_and_plan_fields(
    account_type: object,
    plan_type: object,
) -> None:
    client = RecordingAppServerClient(
        {
            "account": {
                "type": account_type,
                "email": "private-person@example.test",
                "planType": plan_type,
            },
            "requiresOpenaiAuth": True,
        }
    )

    account = _account_probe(client).probe()

    assert account.available is False
    assert account.auth_mode in {None, "unsupported"}
    assert account.plan_type is None
    assert "private-enterprise-plan" not in repr(account)


def test_account_probe_returns_bounded_safe_state_on_client_error() -> None:
    secret = "Bearer reusable-token private-person@example.test"
    client = RecordingAppServerClient(RuntimeError(secret))

    account = _account_probe(client).probe()

    assert account == CodexAccountRecord()
    assert secret not in repr(account)
    assert len(repr(account)) < 1_000


def test_limits_probe_normalizes_primary_and_secondary_windows() -> None:
    response = _rate_limits(
        primary={
            "usedPercent": 20,
            "windowDurationMins": 300,
            "resetsAt": 1_788_800_000,
        },
        secondary={
            "usedPercent": 65,
            "windowDurationMins": 10_080,
            "resetsAt": 1_789_404_800,
        },
        credits={"hasCredits": True, "unlimited": False, "balance": "12.50"},
        plan_type="pro",
    )
    client = RecordingAppServerClient(response)

    status = _limits_probe(client).probe()

    assert isinstance(status, LimitsStatusRecord)
    assert client.calls == [AppServerCall("account/rateLimits/read", None)]
    assert client.timeouts == [5.0]
    assert status.available is True
    assert status.blocked is False
    assert status.primary is not None
    assert status.primary.used_percent == 20.0
    assert status.primary.remaining_percent == 80.0
    assert status.primary.window_minutes == 300
    assert status.primary.resets_at == 1_788_800_000
    assert status.secondary is not None
    assert status.secondary.used_percent == 65.0
    assert status.secondary.remaining_percent == 35.0
    assert status.secondary.window_minutes == 10_080
    assert status.secondary.resets_at == 1_789_404_800
    assert status.credits == {
        "hasCredits": True,
        "unlimited": False,
        "balance": "12.50",
    }
    assert status.plan_type == "pro"
    assert status.updated_at is not None


def test_limits_probe_classifies_a_weekly_only_primary_window_as_secondary() -> None:
    response = _rate_limits(
        primary={
            "usedPercent": 0,
            "windowDurationMins": 10_080,
            "resetsAt": 1_789_404_800,
        },
    )
    client = RecordingAppServerClient(response)
    probe = _limits_probe(client)

    status = probe.probe()

    assert status is not None
    assert status.primary is None
    assert status.secondary is not None
    assert status.secondary.used_percent == 0.0
    assert status.secondary.remaining_percent == 100.0
    assert status.secondary.window_minutes == 10_080
    assert status.secondary.resets_at == 1_789_404_800


def test_limits_probe_orders_known_windows_by_duration_not_protocol_position() -> None:
    response = _rate_limits(
        primary={"usedPercent": 65, "windowDurationMins": 10_080},
        secondary={"usedPercent": 20, "windowDurationMins": 300},
    )
    client = RecordingAppServerClient(response)
    probe = _limits_probe(client)

    status = probe.probe()

    assert status is not None
    assert status.primary is not None
    assert status.primary.window_minutes == 300
    assert status.primary.remaining_percent == 80.0
    assert status.secondary is not None
    assert status.secondary.window_minutes == 10_080
    assert status.secondary.remaining_percent == 35.0


def test_limits_probe_marks_a_locked_reached_snapshot_with_a_generic_message() -> None:
    response = _rate_limits(
        primary={"usedPercent": 100},
        reached_type="workspace_member_credits_depleted",
    )
    client = RecordingAppServerClient(response)

    status = _limits_probe(client).probe()

    assert status is not None
    assert status.blocked is True
    assert status.message == "Usage limit reached"


def test_limits_probe_accepts_a_sparse_locked_snapshot() -> None:
    response = _rate_limits()
    client = RecordingAppServerClient(response)

    status = _limits_probe(client).probe()

    assert status is not None
    assert status.available is True
    assert status.blocked is False
    assert status.primary is None
    assert status.secondary is None
    assert status.credits is None
    assert status.plan_type is None


def test_limits_probe_normalizes_nullable_resets_and_unlimited_credits() -> None:
    response = _rate_limits(
        primary={
            "usedPercent": 0,
            "windowDurationMins": None,
            "resetsAt": None,
        },
        credits={"hasCredits": False, "unlimited": True, "balance": None},
        plan_type="unknown",
    )
    client = RecordingAppServerClient(response)

    status = _limits_probe(client).probe()

    assert status is not None
    assert status.primary is not None
    assert status.primary.used_percent == 0.0
    assert status.primary.remaining_percent == 100.0
    assert status.primary.window_minutes is None
    assert status.primary.resets_at is None
    assert status.credits == {
        "hasCredits": False,
        "unlimited": True,
        "balance": None,
    }
    assert status.plan_type == "unknown"


@pytest.mark.parametrize(
    ("used_percent", "expected_used", "expected_remaining"),
    [
        (-20, 0.0, 100.0),
        (120, 100.0, 0.0),
        (nan, None, None),
        (inf, None, None),
        (-inf, None, None),
        (True, None, None),
        ("50", None, None),
    ],
)
def test_limits_probe_clamps_or_rejects_untrusted_usage_values(
    used_percent: object,
    expected_used: float | None,
    expected_remaining: float | None,
) -> None:
    client = RecordingAppServerClient(
        {"rateLimits": {"primary": {"usedPercent": used_percent}}}
    )

    status = _limits_probe(client).probe()

    assert status is not None
    assert status.primary is not None
    assert status.primary.used_percent == expected_used
    assert status.primary.remaining_percent == expected_remaining


@pytest.mark.parametrize("invalid_value", [-1, nan, inf, True, "300"])
def test_limits_probe_rejects_invalid_window_and_reset_values(
    invalid_value: object,
) -> None:
    client = RecordingAppServerClient(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 10,
                    "windowDurationMins": invalid_value,
                    "resetsAt": invalid_value,
                }
            }
        }
    )

    status = _limits_probe(client).probe()

    assert status is not None
    assert status.primary is not None
    assert status.primary.window_minutes is None
    assert status.primary.resets_at is None


def test_limits_probe_drops_untrusted_plan_credits_and_reached_details() -> None:
    secret = "reusable-token private-person@example.test"
    client = RecordingAppServerClient(
        {
            "rateLimits": {
                "planType": secret,
                "credits": {"hasCredits": secret, "unlimited": secret, "balance": secret},
                "rateLimitReachedType": secret,
            }
        }
    )

    status = _limits_probe(client).probe()
    projection = repr(status)

    assert status is not None
    assert status.plan_type is None
    assert status.credits is None
    assert status.blocked is True
    assert status.message == "Usage limit reached"
    assert secret not in projection
    assert len(projection) < 2_000


def test_limits_probe_returns_none_without_leaking_client_error() -> None:
    secret = "Bearer reusable-token private-person@example.test"
    client = RecordingAppServerClient(RuntimeError(secret))

    status = _limits_probe(client).probe()

    assert status is None
    assert secret not in repr(status)


def test_limits_probe_reuses_a_recent_snapshot() -> None:
    response = _rate_limits(primary={"usedPercent": 25})
    client = RecordingAppServerClient(response)
    probe = _limits_probe(client, min_fetch_interval_seconds=60)

    first = probe.probe()
    second = probe.probe()

    assert second == first
    assert client.calls == [AppServerCall("account/rateLimits/read", None)]


def test_limits_probe_replaces_cached_windows_with_a_fresh_sparse_snapshot() -> None:
    full = _rate_limits(primary={"usedPercent": 25})
    sparse = _rate_limits()
    client = RecordingAppServerClient(full, sparse)
    probe = _limits_probe(client, min_fetch_interval_seconds=0)

    first = probe.probe()
    second = probe.probe()

    assert first is not None and first.primary is not None
    assert second is not None and second.primary is None
    assert len(client.calls) == 2


def test_limits_probe_retains_the_last_safe_snapshot_on_a_transient_error() -> None:
    full = _rate_limits(primary={"usedPercent": 25})
    client = RecordingAppServerClient(full, RuntimeError("reusable-secret"))
    probe = _limits_probe(client, min_fetch_interval_seconds=0)

    first = probe.probe()
    recovered = probe.probe()

    assert first is not None
    assert recovered == first
    assert recovered is not first
    assert "reusable-secret" not in repr(recovered)


def test_limits_probe_serializes_concurrent_app_server_reads() -> None:
    response = _rate_limits(primary={"usedPercent": 25})
    client = BlockingAppServerClient(response, response)
    probe = _limits_probe(client, min_fetch_interval_seconds=0)
    second_worker_started = Event()

    def second_probe() -> LimitsStatusRecord | None:
        second_worker_started.set()
        return probe.probe()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(probe.probe)
        assert client.first_entered.wait(10)
        second = executor.submit(second_probe)
        assert second_worker_started.wait(10)
        assert not client.second_entered.wait(0.1)
        client.release_first.set()
        assert first.result(timeout=10) is not None
        assert second.result(timeout=10) is not None

    assert client.second_entered.is_set()


def test_app_server_probes_do_not_read_auth_files_or_call_private_backends() -> None:
    account_source = getsource(getattr(account_module, "AppServerAccountProbe"))
    limits_source = getsource(getattr(limits_module, "AppServerLimitsProbe"))
    source = f"{account_source}\n{limits_source}".lower()

    assert "auth.json" not in source
    assert "backend-api" not in source
    assert "urlopen" not in source
    assert "access_token" not in source
    assert "refresh_token" not in source
