"""Deliver opted-in scheduled-run notices through Home Assistant services."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import quote

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .bridge_api import BridgeApiError
from .const import PANEL_URL_PATH

_LOGGER = logging.getLogger(__name__)
_RUN_ID = re.compile(r"autrun_[a-f0-9]{32}\Z")
_THREAD_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_MOBILE_SERVICE = re.compile(r"mobile_app_[a-z0-9_]{1,100}\Z")
_ATTENTION = frozenset(
    {
        "failed",
        "blocked",
        "interrupted_restart",
        "skipped_overlap",
        "skipped_capacity",
        "skipped_misfire",
        "skipped_paused",
    }
)
_ALL = _ATTENTION | {"completed", "cancelled"}
_LABELS = {
    "completed": "completed",
    "failed": "failed",
    "blocked": "needs attention",
    "interrupted_restart": "was interrupted",
    "skipped_overlap": "was skipped because another run was active",
    "skipped_capacity": "was skipped because capacity was unavailable",
    "skipped_misfire": "was skipped after its scheduled time",
    "skipped_paused": "was skipped while paused",
    "cancelled": "was cancelled",
}


def _settings(value: object) -> dict | None:
    if not isinstance(value, Mapping) or set(value) != {
        "policy",
        "persistent",
        "mobile_targets",
        "preview",
    }:
        return None
    policy, targets = value["policy"], value["mobile_targets"]
    if (
        not isinstance(policy, str)
        or policy not in {"off", "attention", "all"}
        or type(value["persistent"]) is not bool
        or type(value["preview"]) is not bool
        or not isinstance(targets, list)
        or len(targets) > 8
        or any(
            not isinstance(target, str) or not _MOBILE_SERVICE.fullmatch(target)
            for target in targets
        )
        or len(set(targets)) != len(targets)
        or (value["preview"] and not targets)
    ):
        return None
    return dict(value)


class AutomationNotificationCoordinator:
    """Poll durable run history; claim each destination before its side effect.

    Mobile services have no transaction or acknowledgement API. Persisting the
    claim first favours no duplicate alert after a crash over guaranteed delivery.
    """

    def __init__(self, hass: HomeAssistant, runtime, store) -> None:
        self._hass = hass
        self._runtime = runtime
        self._store = store
        self._receipts: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._remove_timer: CALLBACK_TYPE | None = None
        self._closed = False

    async def async_start(self) -> None:
        if self._closed or self._remove_timer is not None:
            return
        try:
            saved = await self._store.async_load()
        except Exception:
            # An optional notification ledger cannot prevent chat setup.
            _LOGGER.warning("Scheduled notification receipts are unavailable")
            return
        if isinstance(saved, Mapping) and isinstance(saved.get("receipts"), dict):
            self._receipts = {
                key: value
                for key, value in saved["receipts"].items()
                if isinstance(key, str) and isinstance(value, str)
            }
        self._remove_timer = async_track_time_interval(
            self._hass, self._on_tick, timedelta(seconds=60)
        )
        await self.async_refresh()

    async def async_close(self) -> None:
        self._closed = True
        if self._remove_timer is not None:
            self._remove_timer()
            self._remove_timer = None

    async def _on_tick(self, _now) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> None:
        if self._closed or not self._runtime.supports_capability(
            "automation_notifications_v1"
        ):
            return
        async with self._lock:
            try:
                definitions = await self._runtime.client.async_list_automations()
                if self._closed or not isinstance(definitions, list):
                    return
                for definition in definitions:
                    if self._closed:
                        return
                    if not isinstance(definition, Mapping):
                        continue
                    current = _settings(definition.get("notifications"))
                    automation_id = definition.get("automation_id")
                    if (
                        current is None
                        or current["policy"] == "off"
                        or not isinstance(automation_id, str)
                    ):
                        continue
                    runs = await self._runtime.client.async_list_automation_runs(
                        automation_id, limit=200
                    )
                    if self._closed or not isinstance(runs, list):
                        continue
                    for run in reversed(runs):
                        if self._closed:
                            return
                        await self._deliver(definition, current, run)
            except BridgeApiError:
                _LOGGER.debug("Scheduled notification history unavailable")
            except Exception:
                _LOGGER.warning("Scheduled notification delivery check failed")

    async def _deliver(
        self, definition: Mapping, current: Mapping, run: object
    ) -> None:
        if self._closed or not isinstance(run, Mapping):
            return
        run_id, status = run.get("automation_run_id"), run.get("status")
        if (
            not isinstance(run_id, str)
            or not _RUN_ID.fullmatch(run_id)
            or status not in _ALL
        ):
            return
        revision = definition.get("notifications_revision")
        if type(revision) is not int or run.get("notifications_revision") != revision:
            return
        snapshot = _settings(run.get("notifications"))
        if snapshot is None or snapshot["policy"] == "off":
            return
        if status not in (
            _ALL
            if snapshot["policy"] == "all" and current["policy"] == "all"
            else _ATTENTION
        ):
            return
        destinations = (
            ["persistent"] if current["persistent"] and snapshot["persistent"] else []
        )
        destinations.extend(
            target
            for target in current["mobile_targets"]
            if target in snapshot["mobile_targets"]
        )
        pending = [
            target
            for target in destinations
            if f"{run_id}:{target}" not in self._receipts
        ]
        if not pending:
            return
        name = definition.get("name")
        if not isinstance(name, str):
            return
        name = " ".join(name.split())[:80]
        if not name:
            return
        thread_id = run.get("thread_id")
        path = f"/{PANEL_URL_PATH}"
        if isinstance(thread_id, str) and _THREAD_ID.fullmatch(thread_id):
            path += f"?thread={quote(thread_id, safe='')}"
        title = f"Codex Bridge: {name}"
        message = f"Scheduled task {_LABELS[status]}."
        if (
            current["preview"]
            and snapshot["preview"]
            and any(destination != "persistent" for destination in pending)
            and status == "completed"
            and isinstance(thread_id, str)
            and _THREAD_ID.fullmatch(thread_id)
        ):
            try:
                preview = await self._runtime.client.async_automation_run_preview(
                    definition["automation_id"], run_id
                )
            except BridgeApiError:
                preview = None
            if self._closed:
                return
            if preview:
                message += f" {preview}"
        for destination in pending:
            if self._closed:
                return
            receipt_key = f"{run_id}:{destination}"
            if receipt_key in self._receipts:
                continue
            # An unavailable destination is recorded once, without changing
            # the task result or retrying on every polling cycle.
            if destination != "persistent" and not self._hass.services.has_service(
                "notify", destination
            ):
                await self._claim(receipt_key, "unavailable")
                continue
            if destination == "persistent" and not self._hass.services.has_service(
                "persistent_notification", "create"
            ):
                await self._claim(receipt_key, "unavailable")
                continue
            await self._claim(receipt_key, "attempted")
            if self._closed:
                return
            try:
                if destination == "persistent":
                    await self._hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            # HA persistent notifications are global to its
                            # users. Keep private task details on the admin
                            # panel or explicitly selected phones only.
                            "title": "Codex Bridge",
                            "message": f"A scheduled task has an update.\n\n[Open Codex Bridge]({path})",
                            "notification_id": f"codex_bridge_{run_id}",
                        },
                        blocking=True,
                    )
                else:
                    await self._hass.services.async_call(
                        "notify",
                        destination,
                        {
                            "title": title,
                            "message": message,
                            "data": {
                                "url": path,
                                "clickAction": path,
                                "tag": f"codex_bridge_{run_id}",
                            },
                        },
                        blocking=True,
                    )
            except Exception:
                _LOGGER.warning(
                    "Scheduled notification delivery failed for a selected destination"
                )

    async def _claim(self, key: str, state: str) -> None:
        self._receipts[key] = state
        # The App retains at most 5,000 runs, each with no more than nine
        # destinations. Older receipts cannot match its durable history.
        if len(self._receipts) > 50_000:
            for old_key in list(self._receipts)[: len(self._receipts) - 50_000]:
                self._receipts.pop(old_key, None)
        try:
            await self._store.async_save({"receipts": self._receipts})
        except Exception:
            self._receipts.pop(key, None)
            raise
