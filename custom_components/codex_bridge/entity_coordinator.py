"""One config-entry-owned source for privacy-safe Home Assistant entities."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bridge_api import BridgeApiError
from .event_broker import EventBroker, EventRecord
from .runtime import CodexBridgeRuntime


UPDATE_INTERVAL = timedelta(seconds=60)
MAX_USAGE_AGE = timedelta(minutes=15)
_LOGGER = logging.getLogger(__name__)
_TERMINAL_EVENTS = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.interrupted": "interrupted",
}


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    authenticated: bool
    task_running: bool
    last_outcome: str | None
    usage_available: bool
    five_hour_used: float | None
    five_hour_reset: datetime | None
    weekly_used: float | None
    weekly_reset: datetime | None


def _time(value: object) -> datetime | None:
    try:
        if type(value) is int or type(value) is float:
            if not math.isfinite(value) or value <= 0:
                return None
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (OverflowError, OSError, ValueError):
        pass
    return None


def _percent(value: object) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return round(float(value), 1) if 0 <= value <= 100 else None


def _window(value: object, expected_minutes: int) -> tuple[float | None, datetime | None]:
    if not isinstance(value, Mapping):
        return None, None
    if value.get("window_minutes") != expected_minutes:
        return None, None
    return _percent(value.get("used_percent")), _time(value.get("resets_at"))


def project_status(
    status: object, threads: object, *, now: datetime, last_outcome: str | None
) -> EntitySnapshot:
    """Retain only entity fields; never put a raw Bridge response in HA state."""

    if not isinstance(status, Mapping) or not isinstance(threads, list):
        raise ValueError("invalid Bridge entity source")
    auth = status.get("auth")
    limits = status.get("limits")
    account = status.get("account")
    auth = auth if isinstance(auth, Mapping) else {}
    limits = limits if isinstance(limits, Mapping) else {}
    account = account if isinstance(account, Mapping) else {}
    authenticated = (
        auth.get("state") == "ok"
        and auth.get("auth_mode") == "chatgpt"
        and auth.get("auth_required") is False
    )
    running = any(
        isinstance(thread, Mapping) and thread.get("status") == "running"
        for thread in threads
    )
    measured_at = _time(limits.get("updated_at"))
    auth_at = _time(auth.get("updated_at"))
    account_at = _time(account.get("updated_at"))
    fresh = (
        authenticated
        and limits.get("available") is True
        and measured_at is not None
        and timedelta(0) <= now - measured_at <= MAX_USAGE_AGE
        and (auth_at is None or measured_at >= auth_at)
        and (account_at is None or measured_at >= account_at)
    )
    primary = _window(limits.get("primary"), 300) if fresh else (None, None)
    secondary = _window(limits.get("secondary"), 10080) if fresh else (None, None)
    return EntitySnapshot(
        authenticated=authenticated,
        task_running=running,
        last_outcome=last_outcome if last_outcome in _TERMINAL_EVENTS.values() else None,
        usage_available=fresh,
        five_hour_used=primary[0],
        five_hour_reset=primary[1],
        weekly_used=secondary[0],
        weekly_reset=secondary[1],
    )


class BridgeEntityCoordinator(DataUpdateCoordinator[EntitySnapshot]):
    """Share one status/threads refresh across all entities and broker events."""

    def __init__(self, hass: HomeAssistant, runtime: CodexBridgeRuntime) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="Codex Bridge entities",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self._runtime = runtime
        self._last_outcome: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_pending = False
        broker: EventBroker | None = runtime.event_broker
        self._remove_broker_listener = broker.add_listener(self._on_event) if broker else None

    def _on_event(self, event: EventRecord | None) -> None:
        if event is not None:
            outcome = _TERMINAL_EVENTS.get(event.event_type)
            if outcome is not None:
                self._last_outcome = outcome
                if self.data is not None:
                    self.async_set_updated_data(replace(self.data, last_outcome=outcome))
            elif event.event_type == "auth.status_changed":
                # An outcome observed under a previous sign-in is not an
                # outcome for the newly selected account.
                self._last_outcome = None
                if self.data is not None:
                    self.async_set_updated_data(replace(self.data, last_outcome=None))
            elif not event.event_type.startswith("run."):
                return
        self._refresh_pending = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self.hass.async_create_task(
                self._async_refresh_from_events(), "codex_bridge_entity_refresh"
            )

    async def _async_refresh_from_events(self) -> None:
        # Coalesce events while a request is in flight, then read the latest state.
        # The default request debouncer can defer the follow-up for ten seconds.
        while self._refresh_pending:
            self._refresh_pending = False
            await self.async_refresh()

    async def _async_update_data(self) -> EntitySnapshot:
        try:
            status, threads = await asyncio.gather(
                self._runtime.client.async_get_status(),
                self._runtime.client.async_list_threads(),
            )
            return project_status(
                status, threads, now=datetime.now(UTC), last_outcome=self._last_outcome
            )
        except (BridgeApiError, ValueError, TypeError) as error:
            raise UpdateFailed("Codex Bridge status is unavailable") from error

    async def async_close(self) -> None:
        if self._remove_broker_listener is not None:
            self._remove_broker_listener()
            self._remove_broker_listener = None
        self._refresh_pending = False
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        await self.async_shutdown()
