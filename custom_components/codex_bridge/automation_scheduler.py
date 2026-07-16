"""Home Assistant-owned timers for durable Bridge automation claims."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_point_in_time

from .bridge_api import BridgeApiConnectionError, BridgeApiError
from .const import CONNECTION_TYPE_EXTERNAL_LEGACY


class AutomationScheduler:
    """Re-arm HA point-in-time callbacks from the Bridge's durable snapshot."""

    def __init__(
        self,
        hass: HomeAssistant,
        client,
        connection_type: str,
        *,
        web_search_mode: str | None = None,
    ) -> None:
        self.hass = hass
        self.client = client
        self.connection_type = connection_type
        self.web_search_mode = web_search_mode
        self.timezone = ZoneInfo(hass.config.time_zone)
        self._callbacks: dict[str, CALLBACK_TYPE] = {}
        self._retry_callbacks: set[CALLBACK_TYPE] = set()
        self._reconciliation_callback: CALLBACK_TYPE | None = None
        self._closed = False
        self._grace = timedelta(minutes=5)

    async def async_start(self) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> None:
        if self._closed or self.connection_type == CONNECTION_TYPE_EXTERNAL_LEGACY:
            return
        snapshot = await self.client.async_scheduler_automations()
        values = snapshot.get("automations") if isinstance(snapshot, dict) else None
        if not isinstance(values, list):
            raise ValueError("automation scheduler snapshot is invalid")
        desired: dict[str, tuple[int, datetime]] = {}
        for item in values:
            if not isinstance(item, dict):
                continue
            automation_id = item.get("automation_id")
            revision = item.get("revision")
            due = _utc(item.get("next_run_at"))
            if (
                isinstance(automation_id, str)
                and automation_id
                and type(revision) is int
                and revision > 0
                and due is not None
            ):
                desired[automation_id] = (revision, due)
        for automation_id, unsubscribe in list(self._callbacks.items()):
            if automation_id not in desired:
                unsubscribe()
                self._callbacks.pop(automation_id, None)
        for automation_id, (revision, due) in desired.items():
            previous = self._callbacks.pop(automation_id, None)
            if previous is not None:
                previous()

            async def fire(
                _when: datetime,
                automation_id: str = automation_id,
                revision: int = revision,
                due: datetime = due,
            ) -> None:
                await self._async_fire(automation_id, revision, due)

            self._callbacks[automation_id] = async_track_point_in_time(
                self.hass,
                fire,
                due,
            )

    async def async_close(self) -> None:
        self._closed = True
        for unsubscribe in self._callbacks.values():
            unsubscribe()
        self._callbacks.clear()
        for unsubscribe in self._retry_callbacks:
            unsubscribe()
        self._retry_callbacks.clear()
        self._reconciliation_callback = None

    async def _async_fire(
        self, automation_id: str, revision: int, due: datetime
    ) -> None:
        self._callbacks.pop(automation_id, None)
        if self._closed:
            return
        key = f"automation:{automation_id}:{revision}:{_iso(due)}"
        try:
            claim = {
                "due_at": _iso(due),
                "idempotency_key": key,
                "expected_revision": revision,
            }
            if self.web_search_mode is not None:
                claim["web_search"] = self.web_search_mode
            await self.client.async_claim_automation_run(
                automation_id,
                **claim,
            )
        except BridgeApiConnectionError:
            self._schedule_retry(automation_id, revision, due)
            return
        except BridgeApiError as error:
            # The Bridge records deterministic rejection outcomes. Re-arm the
            # next recurrence rather than spinning on a configuration error.
            if error.retryable:
                self._schedule_retry(automation_id, revision, due)
                return
        try:
            await self.async_refresh()
        except (BridgeApiError, ValueError):
            self.schedule_reconciliation()

    def _schedule_retry(self, automation_id: str, revision: int, due: datetime) -> None:
        if self._closed:
            return
        if datetime.now(UTC) > due + self._grace:
            self.schedule_reconciliation()
            return
        callback: list[CALLBACK_TYPE] = []

        async def retry(_when: datetime) -> None:
            self._retry_callbacks.discard(callback[0])
            await self._async_fire(automation_id, revision, due)

        callback.append(async_call_later(self.hass, 15, retry))
        self._retry_callbacks.add(callback[0])

    def schedule_reconciliation(self) -> None:
        """Keep one rate-limited refresh alive after a claim retry expires."""

        if self._closed or self._reconciliation_callback is not None:
            return
        callback: list[CALLBACK_TYPE] = []

        async def reconcile(_when: datetime) -> None:
            unsubscribe = callback[0]
            self._retry_callbacks.discard(unsubscribe)
            if self._reconciliation_callback is unsubscribe:
                self._reconciliation_callback = None
            if self._closed:
                return
            try:
                await self.async_refresh()
            except (BridgeApiError, ValueError):
                self.schedule_reconciliation()

        callback.append(
            async_call_later(self.hass, self._grace.total_seconds(), reconcile)
        )
        self._reconciliation_callback = callback[0]
        self._retry_callbacks.add(callback[0])


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
