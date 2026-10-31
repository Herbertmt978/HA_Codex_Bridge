"""HA refresh and safe projection for cached saved-account usage telemetry."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bridge_api import BridgeApiError
from .const import DOMAIN
from .runtime import CodexBridgeConfigEntry, CodexBridgeRuntime

if TYPE_CHECKING:
    from .sensor import AccountAllowanceEntityManager


UPDATE_INTERVAL = timedelta(seconds=60)
MAX_USAGE_AGE = timedelta(minutes=15)
MAX_PROFILES = 32
_WINDOW_MINUTES = {"5 hours": 300, "Weekly": 10080}
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER = re.compile(r"\bbearer\s+[A-Z0-9._~+/-]+=*", re.IGNORECASE)
_SECRET = re.compile(r"\b(token|api[_ -]?key|password|secret)\s*[:=]\s*[^\s,;]+", re.IGNORECASE)
_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|/(?:data|config|share|home|root|Users)/)[^\s,;]+")
_REAUTHENTICATION_ISSUE = "account_reauthentication"


@dataclass(frozen=True, slots=True)
class AllowanceWindow:
    remaining_percent: float | None
    resets_at: datetime | None

    @property
    def used_percent(self) -> float | None:
        return round(100.0 - self.remaining_percent, 1) if self.remaining_percent is not None else None


@dataclass(frozen=True, slots=True)
class AccountAllowance:
    profile_id: str
    label: str
    active: bool
    status: str
    updated_at: datetime | None
    windows: dict[str, AllowanceWindow]
    available_resets: int | None
    next_reset_expiry: datetime | None
    expiry_complete: bool
    five_hour_enabled: bool | None
    reauthentication_required: bool


@dataclass(frozen=True, slots=True)
class AccountAllowanceSnapshot:
    inventory_complete: bool
    profiles: dict[str, AccountAllowance]
    refreshed_at: datetime


def safe_profile_label(value: object) -> str:
    """Keep user-facing account labels useful without reflecting private values."""
    if not isinstance(value, str):
        return "Saved account"
    label = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value[:512]).strip()
    label = _EMAIL.sub("[private account]", label)
    label = _BEARER.sub("[private credential]", label)
    label = _SECRET.sub(r"\1=[private credential]", label)
    label = _PATH.sub("[private path]", label)
    label = re.sub(r"\s+", " ", label).strip()[:60]
    return label or "Saved account"


def _timestamp(value: object) -> datetime | None:
    try:
        if type(value) is int:
            if value <= 0:
                return None
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _percent(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 100
        or not math.isfinite(value)
    ):
        return None
    return round(float(value), 1)


def _profile(row: object, now: datetime) -> AccountAllowance:
    if not isinstance(row, Mapping):
        raise ValueError("account telemetry profile is invalid")
    profile_id = row.get("id")
    label = row.get("label")
    active = row.get("active")
    status = row.get("status")
    if (
        not isinstance(profile_id, str) or re.fullmatch(r"[a-f0-9]{32}", profile_id) is None
        or type(active) is not bool
        or not isinstance(status, str)
        or status not in {"available", "stale", "unavailable", "reauthentication_required"}
    ):
        raise ValueError("account telemetry profile identity or status is invalid")

    updated_at = _timestamp(row.get("updated_at"))
    if status == "available" and (
        updated_at is None or not timedelta(0) <= now - updated_at <= MAX_USAGE_AGE
    ):
        status = "stale"

    raw_windows = row.get("windows")
    if not isinstance(raw_windows, list) or len(raw_windows) > 2:
        raise ValueError("account telemetry windows are invalid")
    windows: dict[str, AllowanceWindow] = {}
    for item in raw_windows:
        if not isinstance(item, Mapping):
            raise ValueError("account telemetry window is invalid")
        name = item.get("name")
        if (
            not isinstance(name, str)
            or (expected_minutes := _WINDOW_MINUTES.get(name)) is None
            or item.get("window_minutes") != expected_minutes
            or name in windows
        ):
            continue
        reset = _timestamp(item.get("resets_at"))
        windows[name] = AllowanceWindow(_percent(item.get("remaining_percent")), reset)

    credits = row.get("available_resets")
    if type(credits) is not int or not 0 <= credits <= 10000:
        credits = None
    expiry = _timestamp(row.get("next_reset_expiry"))
    if credits in (None, 0):
        expiry = None
    complete = row.get("expiry_complete") is True
    if credits is None or credits > 0 and expiry is None:
        complete = False
    five_hour_enabled = row.get("five_hour_enabled")
    if type(five_hour_enabled) is not bool:
        five_hour_enabled = None
    return AccountAllowance(
        profile_id=profile_id,
        label=safe_profile_label(label),
        active=active,
        status=status,
        updated_at=updated_at,
        windows=windows,
        available_resets=credits,
        next_reset_expiry=expiry,
        expiry_complete=complete,
        five_hour_enabled=five_hour_enabled,
        reauthentication_required=row.get("reauthentication_required") is True,
    )


def project_account_telemetry(payload: object, *, now: datetime | None = None) -> AccountAllowanceSnapshot:
    """Validate a complete inventory and retain only bounded public allowance fields."""
    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        raise ValueError("account telemetry observation time must be timezone-aware")
    if not isinstance(payload, Mapping) or payload.get("inventory_complete") is not True:
        raise ValueError("account telemetry inventory is incomplete")
    rows = payload.get("profiles")
    if not isinstance(rows, list) or len(rows) > MAX_PROFILES:
        raise ValueError("account telemetry inventory is invalid")
    profiles: dict[str, AccountAllowance] = {}
    active_count = 0
    for raw in rows:
        item = _profile(raw, observed_at)
        if item.profile_id in profiles:
            raise ValueError("account telemetry contains duplicate profiles")
        active_count += int(item.active)
        profiles[item.profile_id] = item
    if active_count > 1:
        raise ValueError("account telemetry has more than one active profile")
    return AccountAllowanceSnapshot(True, profiles, observed_at)


class AccountAllowanceCoordinator(DataUpdateCoordinator[AccountAllowanceSnapshot]):
    """Poll one cached Bridge projection; native profile refresh stays App-owned."""

    config_entry: CodexBridgeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: CodexBridgeConfigEntry,
        runtime: CodexBridgeRuntime,
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            config_entry=config_entry,
            name="Codex Bridge account allowances",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.runtime = runtime
        # The sensor platform owns the account entities; closing ends its sync.
        self.manager: AccountAllowanceEntityManager | None = None

    async def _async_update_data(self) -> AccountAllowanceSnapshot:
        if not self.runtime.supports_capability("account_profile_telemetry_v1"):
            raise UpdateFailed("Saved-account usage telemetry is unavailable")
        try:
            payload = await self.runtime.client.async_account_profile_telemetry()
            snapshot = project_account_telemetry(payload)
        except (BridgeApiError, TypeError, ValueError) as error:
            raise UpdateFailed("Saved-account usage telemetry is unavailable") from error
        self._sync_reauthentication_issues(snapshot)
        return snapshot

    @callback
    def _sync_reauthentication_issues(self, snapshot: AccountAllowanceSnapshot) -> None:
        """Raise one repair per signed-out account; a complete inventory clears the rest."""

        prefix = _reauthentication_issue_prefix(self.config_entry.entry_id)
        current: set[str] = set()
        for profile in snapshot.profiles.values():
            if not (
                profile.reauthentication_required
                or profile.status == "reauthentication_required"
            ):
                continue
            issue_id = f"{prefix}{profile.profile_id}"
            current.add(issue_id)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="account_reauthentication_required",
                translation_placeholders={"account": profile.label},
            )
        for domain, issue_id in tuple(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in current:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def async_close(self) -> None:
        if self.manager is not None:
            await self.manager.async_close()
        await self.async_shutdown()


def _reauthentication_issue_prefix(entry_id: str) -> str:
    return f"{_REAUTHENTICATION_ISSUE}_{entry_id}_"


@callback
def async_delete_reauthentication_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove a deleted entry's account repairs; unloading keeps them."""

    prefix = _reauthentication_issue_prefix(entry_id)
    for domain, issue_id in tuple(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
