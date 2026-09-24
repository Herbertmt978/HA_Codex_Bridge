"""Forward safe task lifecycle events to Home Assistant's event bus."""

from __future__ import annotations

import re
from collections.abc import Mapping

from homeassistant.core import HomeAssistant

from .const import BRIDGE_EVENT_CURSOR_MAX, DOMAIN
from .event_broker import CursorStore, EventBroker, EventRecord

_EVENT_NAMES = {
    "task.accepted": f"{DOMAIN}_task_accepted",
    "task.interaction_needed": f"{DOMAIN}_task_interaction_needed",
    "task.result": f"{DOMAIN}_task_result",
}
_STATUSES = frozenset(
    {
        "accepted",
        "queued",
        "starting",
        "running",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }
)
_KINDS = frozenset({"question", "approval"})


class TaskEventForwarder:
    """At-most-once HA delivery with a durable receipt before bus publication.

    The App's task status remains authoritative if HA stops after saving the
    receipt but before publishing the event. The action's get_task service can
    recover that state without replaying an automation twice.
    """

    def __init__(
        self, hass: HomeAssistant, broker: EventBroker, store: CursorStore
    ) -> None:
        self._hass = hass
        self._store = store
        self._cursor = 0
        self._remove = broker.add_async_listener(self._handle)

    async def async_start(self) -> None:
        saved = await self._store.async_load()
        cursor = saved.get("cursor") if isinstance(saved, Mapping) else None
        if type(cursor) is int and 0 <= cursor <= BRIDGE_EVENT_CURSOR_MAX:
            self._cursor = cursor

    async def async_close(self) -> None:
        self._remove()

    async def _handle(self, event: EventRecord) -> None:
        event_name = _EVENT_NAMES.get(event.event_type)
        if event_name is None or event.cursor <= self._cursor:
            return
        payload = event.payload
        task_id = payload.get("task_id")
        run_id = payload.get("run_id")
        if (
            event.scope != "thread"
            or event.thread_id is None
            or not isinstance(task_id, str)
            or len(task_id) != 32
            or any(character not in "0123456789abcdef" for character in task_id)
            or not isinstance(run_id, str)
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_id) is None
        ):
            return
        data = {
            "task_id": task_id,
            "thread_id": event.thread_id,
            "run_id": run_id,
        }
        if event.event_type == "task.interaction_needed":
            kind = payload.get("kind")
            if not isinstance(kind, str) or kind not in _KINDS:
                return
            data["kind"] = kind
        else:
            status = payload.get("status")
            if not isinstance(status, str) or status not in _STATUSES:
                return
            data["status"] = status

        # Save before firing: a replay after restart cannot trigger duplicate
        # automations. If saving fails, the broker retries this exact event.
        await self._store.async_save({"cursor": event.cursor})
        self._cursor = event.cursor
        self._hass.bus.async_fire(event_name, data)
