"""Opt-in Home Assistant notifications for pending Codex questions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from homeassistant.core import CALLBACK_TYPE, Event, EventOrigin, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import PANEL_URL_PATH
from .question_notification_recipients import (
    QuestionNotificationRecipient,
    async_resolve_question_recipients,
)

_LOGGER = logging.getLogger(__name__)
_ACTION_EVENT = "mobile_app_notification_action"
_MAX_INTERACTIONS = 128
_MAX_DESTINATIONS = 8
_MAX_VALUE = 4096
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_MOBILE_SERVICE = re.compile(r"mobile_app_[a-z0-9_]{1,100}\Z")
_FINGERPRINT = re.compile(r"[a-f0-9]{64}\Z")
_SENSITIVE_PREVIEW = re.compile(
    r"(?i)\b(?:password|passwd|passcode|secret|credential|api[_ -]?key|access[_ -]?token|token|bearer|cookie|authori[sz]ation|private[_ -]?key)\b"
)


def _expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum
        and not any(ord(c) < 32 and c not in "\r\n\t" for c in value)
        and "\x7f" not in value
    )


def _valid_interaction(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("interaction_id"), str)
        and _ID.fullmatch(value["interaction_id"]) is not None
        and isinstance(value.get("thread_id"), str)
        and _ID.fullmatch(value["thread_id"]) is not None
        and value.get("kind") == "user_input"
        # The authoritative pending-list route omits unset model defaults.
        # Retain compatibility with explicit pending, but reject stale states.
        and ("status" not in value or value["status"] == "pending")
        and isinstance(value.get("allowed_actions"), list)
        and "answer" in value["allowed_actions"]
        and _expiry(value.get("expires_at")) is not None
        and isinstance(value.get("display"), Mapping)
    )


def _question(value: Mapping) -> Mapping | None:
    display = value.get("display")
    questions = display.get("questions") if isinstance(display, Mapping) else None
    if not isinstance(questions, list) or len(questions) != 1:
        return None
    question = questions[0]
    if (
        not isinstance(question, Mapping)
        or not isinstance(question.get("question_id"), str)
        or not _text(question.get("question_id"), 128)
        or question.get("multiple") is not False
        or not _safe_preview(question)
    ):
        return None
    return question


def _choice_labels(question: Mapping) -> list[str] | None:
    options = question.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 2:
        return None
    labels = []
    for option in options:
        if not isinstance(option, Mapping) or not _text(option.get("label"), 160):
            return None
        labels.append(option["label"])
    return labels


def _question_fingerprint(value: Mapping) -> str | None:
    display = value.get("display")
    questions = display.get("questions") if isinstance(display, Mapping) else None
    if not isinstance(questions, list) or len(questions) > 32:
        return None
    try:
        encoded = json.dumps(
            questions, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _safe_preview(question: Mapping | None) -> bool:
    if (
        question is None
        or question.get("isSecret")
        or question.get("is_secret")
        or not _text(question.get("header"), 160)
        or not question["header"].isprintable()
        or not _text(question.get("prompt"), 2048)
        or not question["prompt"].isprintable()
    ):
        return False
    values = [question["header"], question["prompt"]]
    options = question.get("options", [])
    if not isinstance(options, list) or len(options) > 32:
        return False
    for option in options:
        if not isinstance(option, Mapping):
            return False
        for key in ("label", "description"):
            value = option.get(key)
            if value is not None and (
                not isinstance(value, str) or len(value) > 512 or not value.isprintable()
            ):
                return False
            if isinstance(value, str):
                values.append(value)
    return not any(_SENSITIVE_PREVIEW.search(value) for value in values)


class QuestionNotificationCoordinator:
    """Keep a bounded correlation ledger for eligible mobile question replies."""

    def __init__(self, hass: HomeAssistant, runtime, store) -> None:
        self._hass = hass
        self._runtime = runtime
        self._store = store
        self._entries: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._remove_timer: CALLBACK_TYPE | None = None
        self._remove_bus: CALLBACK_TYPE | None = None
        self._remove_broker: CALLBACK_TYPE | None = None
        self._refresh_task: asyncio.Task | None = None
        self._refresh_pending = False
        self._loaded = False
        self._closed = False

    async def async_start(self) -> None:
        async with self._start_lock:
            if self._closed or self._remove_timer is not None:
                return
            if not self._loaded:
                try:
                    saved = await self._store.async_load()
                except Exception:
                    _LOGGER.warning("Question notification ledger could not be read")
                    return
                if self._closed:
                    return
                entries = self._normalise_ledger(saved)
                if entries is None:
                    _LOGGER.warning("Question notification ledger is invalid; replies remain disabled")
                    return
                self._entries = entries
                self._loaded = True
            if not self._enabled() or not self._runtime.supports_capability("interactions_v2"):
                had_entries = bool(self._entries)
                for entry in tuple(self._entries.values()):
                    await self._clear_entry(entry)
                self._entries.clear()
                if had_entries:
                    await self._persist()
                return
            self._remove_bus = self._hass.bus.async_listen(_ACTION_EVENT, self._on_action)
            broker = getattr(self._runtime, "event_broker", None)
            if broker is not None:
                self._remove_broker = broker.add_async_listener(self._on_broker_event)
            self._remove_timer = async_track_time_interval(
                self._hass, self._on_tick, timedelta(seconds=30)
            )
            await self.async_refresh()

    async def async_close(self) -> None:
        self._closed = True
        self._refresh_pending = False
        task, self._refresh_task = self._refresh_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for remove_name in ("_remove_timer", "_remove_bus", "_remove_broker"):
            remove = getattr(self, remove_name)
            if remove is not None:
                remove()
                setattr(self, remove_name, None)

    async def _on_tick(self, _now) -> None:
        await self.async_refresh()

    async def _on_broker_event(self, event) -> None:
        if getattr(event, "event_type", None) in {
            "interaction.created",
            "interaction.resolved",
            "runtime.changed",
            "auth.status_changed",
        }:
            self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._closed:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_pending = True
            return
        task = self._hass.async_create_task(
            self.async_refresh(), "codex_bridge_question_notifications_refresh"
        )
        self._refresh_task = task
        task.add_done_callback(self._refresh_done)

    def _refresh_done(self, task: asyncio.Task) -> None:
        if self._refresh_task is task:
            self._refresh_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            _LOGGER.warning("Question notification refresh failed")
        if self._refresh_pending and not self._closed:
            self._refresh_pending = False
            self._schedule_refresh()

    @staticmethod
    def _normalise_ledger(saved: object) -> dict[str, dict] | None:
        if saved is None:
            return {}
        if (
            not isinstance(saved, Mapping)
            or set(saved) != {"interactions"}
            or not isinstance(saved.get("interactions"), list)
            or len(saved["interactions"]) > _MAX_INTERACTIONS
        ):
            return None
        entries = {}
        action_tokens: set[str] = set()
        request_ids: set[str] = set()
        for entry in saved["interactions"]:
            if not isinstance(entry, Mapping):
                return None
            interaction_id = entry.get("interaction_id")
            thread_id = entry.get("thread_id")
            expires_at = entry.get("expires_at")
            question_id = entry.get("question_id")
            destinations = entry.get("destinations")
            if (
                not isinstance(interaction_id, str)
                or _ID.fullmatch(interaction_id) is None
                or interaction_id in entries
                or not isinstance(thread_id, str)
                or _ID.fullmatch(thread_id) is None
                or not isinstance(question_id, str)
                or not _text(question_id, 128)
                or not isinstance(entry.get("question_fingerprint"), str)
                or _FINGERPRINT.fullmatch(entry["question_fingerprint"]) is None
                or _expiry(expires_at) is None
                or not isinstance(destinations, list)
                or len(destinations) > _MAX_DESTINATIONS
                or type(entry.get("persistent")) is not bool
                or type(entry.get("attempted")) is not bool
                or type(entry.get("open_chat_only")) is not bool
            ):
                return None
            safe_destinations = []
            for destination in destinations:
                if not isinstance(destination, Mapping) or (
                    not all(
                        isinstance(destination.get(key), str)
                        and 1 <= len(destination[key]) <= limit
                        for key, limit in (
                            ("registration_id", 128),
                            ("device_id", 128),
                            ("user_id", 128),
                            ("service", 120),
                        )
                    )
                    or not _MOBILE_SERVICE.fullmatch(destination["service"])
                    or not isinstance(destination.get("actions"), list)
                    or type(destination.get("inline_reply")) is not bool
                    or type(destination.get("attempted")) is not bool
                    or len(destination["actions"]) > 3
                ):
                    return None
                actions = []
                for action in destination["actions"]:
                    if not isinstance(action, Mapping) or (
                        not isinstance(action.get("token"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", action["token"])
                        or action.get("kind") not in {"choice", "text"}
                        or (
                            action.get("kind") == "choice"
                            and (type(action.get("index")) is not int or action["index"] not in {0, 1})
                        )
                        or not isinstance(action.get("client_request_id"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", action["client_request_id"])
                    ):
                        return None
                    token, request_id = action["token"], action["client_request_id"]
                    if token in action_tokens or request_id in request_ids:
                        return None
                    action_tokens.add(token)
                    request_ids.add(request_id)
                    actions.append(
                        {key: action[key] for key in ("token", "kind", "index", "client_request_id") if key in action}
                    )
                safe_destinations.append(
                    {
                        **{key: destination[key] for key in ("registration_id", "device_id", "user_id", "service")},
                        "inline_reply": destination["inline_reply"],
                        "actions": actions,
                        "attempted": destination["attempted"],
                    }
                )
            entries[interaction_id] = {
                "interaction_id": interaction_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "question_fingerprint": entry["question_fingerprint"],
                "expires_at": expires_at,
                "destinations": safe_destinations,
                "persistent": entry["persistent"],
                "attempted": entry["attempted"],
                "open_chat_only": entry["open_chat_only"],
            }
        return entries

    async def async_refresh(self) -> None:
        if self._closed:
            return
        if not self._loaded:
            await self.async_start()
            return
        async with self._lock:
            if not self._enabled() or not self._runtime.supports_capability("interactions_v2"):
                had_entries = bool(self._entries)
                for entry in tuple(self._entries.values()):
                    await self._clear_entry(entry)
                self._entries.clear()
                if had_entries:
                    await self._persist()
                return
            try:
                response = await self._runtime.client.async_list_pending_interactions()
            except Exception:
                # An unavailable authoritative lookup must not revoke a valid
                # correlation or cause another delivery attempt.
                _LOGGER.debug("Pending question state is temporarily unavailable")
                return
            if self._closed or not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
                return
            settings = getattr(self._runtime, "question_notification_settings", None)
            settings = settings if isinstance(settings, Mapping) else {}
            enabled = settings.get("enabled") is True
            persistent = enabled and settings.get("persistent") is True
            preview = enabled and settings.get("preview") is True
            registrations = settings.get("mobile_targets", [])
            if not isinstance(registrations, list) or len(registrations) > _MAX_DESTINATIONS:
                registrations = []
            registrations = list(dict.fromkeys(item for item in registrations if isinstance(item, str)))
            try:
                recipients = await async_resolve_question_recipients(self._hass, registrations)
            except Exception:
                _LOGGER.warning("Question notification recipients could not be verified")
                recipients = ()
            recipient_map = {
                recipient.registration_id: recipient
                for recipient in recipients
                if isinstance(recipient, QuestionNotificationRecipient)
            }
            pending = {}
            now = dt_util.utcnow()
            for item in response["items"]:
                if not _valid_interaction(item):
                    continue
                deadline = _expiry(item["expires_at"])
                if deadline is None or deadline <= now:
                    continue
                pending[item["interaction_id"]] = item

            for interaction_id, old in tuple(self._entries.items()):
                current = pending.get(interaction_id)
                if current is None or not self._same_question(old, current):
                    await self._clear_entry(old)
                    self._entries.pop(interaction_id, None)

            for interaction_id, item in list(pending.items())[:_MAX_INTERACTIONS]:
                if self._closed:
                    return
                fingerprint = _question_fingerprint(item)
                if fingerprint is None:
                    continue
                question = _question(item)
                question_id = question["question_id"] if question is not None else "__open_chat_only__"
                entry = self._entries.get(interaction_id)
                if entry is None:
                    entry = {
                        "interaction_id": interaction_id,
                        "thread_id": item["thread_id"],
                        "question_id": question_id,
                        "question_fingerprint": fingerprint,
                        "expires_at": item["expires_at"],
                        "destinations": [],
                        "persistent": False,
                        "attempted": False,
                        "open_chat_only": question is None,
                    }
                    self._entries[interaction_id] = entry
                elif entry.get("open_chat_only") is not (question is None):
                    await self._clear_entry(entry)
                    self._entries.pop(interaction_id, None)
                    entry = {
                        "interaction_id": interaction_id,
                        "thread_id": item["thread_id"],
                        "question_id": question_id,
                        "question_fingerprint": fingerprint,
                        "expires_at": item["expires_at"],
                        "destinations": [],
                        "persistent": False,
                        "attempted": False,
                        "open_chat_only": question is None,
                    }
                    self._entries[interaction_id] = entry
                if persistent and not entry["persistent"]:
                    entry["persistent"] = True
                    if await self._persist():
                        if not self._closed:
                            await self._deliver_persistent(entry)
                    else:
                        entry["persistent"] = False
                elif not persistent and entry["persistent"]:
                    await self._clear_persistent(entry)
                    entry["persistent"] = False

                wanted = [recipient_map[key] for key in registrations if key in recipient_map]
                for recipient in wanted[:_MAX_DESTINATIONS]:
                    if self._closed:
                        return
                    existing = self._destination(entry, recipient.registration_id)
                    if existing is not None:
                        if not self._recipient_matches(existing, recipient):
                            await self._clear_mobile(entry, existing)
                            entry["destinations"].remove(existing)
                            existing = None
                    if existing is None:
                        destination = self._make_destination(recipient, question or {}, preview)
                        entry["destinations"].append(destination)
                        await self._persist()
                        if not self._closed:
                            await self._deliver_mobile(entry, destination, item, question, preview)
                    elif not existing["attempted"]:
                        await self._deliver_mobile(entry, existing, item, question, preview)
                for destination in tuple(entry["destinations"]):
                    if destination["registration_id"] not in {r.registration_id for r in wanted}:
                        await self._clear_mobile(entry, destination)
                        entry["destinations"].remove(destination)
                if len(self._entries) > _MAX_INTERACTIONS:
                    oldest_id = next(iter(self._entries))
                    oldest = self._entries.pop(oldest_id)
                    await self._clear_entry(oldest)
            await self._persist()

    @staticmethod
    def _same_question(entry: Mapping, current: Mapping) -> bool:
        question = _question(current)
        if entry.get("open_chat_only") is True:
            return (
                question is None
                and entry.get("thread_id") == current.get("thread_id")
                and entry.get("expires_at") == current.get("expires_at")
                and entry.get("question_fingerprint") == _question_fingerprint(current)
            )
        return (
            question is not None
            and entry.get("thread_id") == current.get("thread_id")
            and entry.get("question_id") == question.get("question_id")
            and entry.get("expires_at") == current.get("expires_at")
            and entry.get("question_fingerprint") == _question_fingerprint(current)
        )

    @staticmethod
    def _destination(entry: Mapping, registration_id: str) -> dict | None:
        return next(
            (d for d in entry.get("destinations", []) if d.get("registration_id") == registration_id),
            None,
        )

    @staticmethod
    def _recipient_matches(saved: Mapping, recipient: QuestionNotificationRecipient) -> bool:
        return recipient.inline_reply is (saved.get("inline_reply") is True) and all(
            saved.get(key) == getattr(recipient, key)
            for key in ("registration_id", "device_id", "user_id", "service")
        )

    @staticmethod
    def _make_destination(recipient, question: Mapping, preview: bool) -> dict:
        preview_requested = preview
        preview = preview and _safe_preview(question)
        sensitive_preview = preview_requested and not preview
        actions = []
        labels = _choice_labels(question) if preview else None
        if labels is not None:
            for index in range(len(labels)):
                actions.append({"token": secrets.token_urlsafe(24), "client_request_id": secrets.token_urlsafe(32), "kind": "choice", "index": index})
        if question.get("allow_free_text") is True and not sensitive_preview:
            # Companion notifications permit at most three Android actions.
            if len(actions) > 1:
                actions = actions[:1]
            actions.append({"token": secrets.token_urlsafe(24), "client_request_id": secrets.token_urlsafe(32), "kind": "text"})
        if not recipient.inline_reply:
            actions = []
        return {
            "registration_id": recipient.registration_id,
            "device_id": recipient.device_id,
            "user_id": recipient.user_id,
            "service": recipient.service,
            "inline_reply": recipient.inline_reply is True,
            "actions": actions,
            "attempted": False,
        }

    async def _deliver_persistent(self, entry: Mapping) -> None:
        if not self._hass.services.has_service("persistent_notification", "create"):
            return
        path = self._chat_path(entry)
        try:
            await self._hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Codex Bridge", "message": f"A Codex question is waiting.\n\n[Open Codex Bridge]({path})", "notification_id": self._tag(entry)},
                blocking=True,
            )
        except Exception:
            _LOGGER.warning("Persistent question notification delivery failed")

    async def _deliver_mobile(self, entry, destination, item, question, preview) -> None:
        service = destination["service"]
        if not self._hass.services.has_service("notify", service):
            destination["attempted"] = True
            if not await self._persist():
                destination["attempted"] = False
            return
        destination["attempted"] = True
        if not await self._persist():
            destination["attempted"] = False
            return
        if self._closed:
            return
        preview = preview and isinstance(question, Mapping) and _safe_preview(question)
        data = {"url": self._chat_path(entry), "clickAction": self._chat_path(entry), "tag": self._tag(entry), "authenticationRequired": True}
        actions = []
        labels = _choice_labels(question) if isinstance(question, Mapping) else None
        for action in destination["actions"]:
            if action["kind"] == "choice":
                title = labels[action["index"]] if preview and labels and action["index"] < len(labels) else f"Choice {action['index'] + 1}"
                actions.append({"action": action["token"], "title": title, "authenticationRequired": True})
            else:
                actions.append({"action": action["token"], "title": "Reply", "behavior": "textInput", "textInputButtonTitle": "Send", "authenticationRequired": True})
        actions.append({"action": "URI", "title": "Open chat", "uri": self._chat_path(entry)})
        if len(actions) <= 3:
            data["actions"] = actions
        title = "Codex Bridge"
        message = "A question needs your response."
        if preview and question:
            title = str(question.get("header", title))[:160]
            message = str(question.get("prompt", message))[:240]
        try:
            await self._hass.services.async_call(
                "notify", service,
                {"title": title, "message": message, "data": data},
                blocking=True,
            )
        except Exception:
            _LOGGER.warning("Question notification delivery failed for a selected device")

    @staticmethod
    def _tag(entry: Mapping) -> str:
        return "codex_bridge_question_" + entry["interaction_id"]

    @staticmethod
    def _chat_path(entry: Mapping) -> str:
        return (
            f"/{PANEL_URL_PATH}?thread={quote(entry['thread_id'], safe='')}"
            f"&interaction={quote(entry['interaction_id'], safe='')}"
        )

    async def _clear_mobile(self, entry: Mapping, destination: Mapping) -> None:
        if not destination.get("attempted"):
            return
        service = destination.get("service")
        if not isinstance(service, str) or not self._hass.services.has_service("notify", service):
            return
        try:
            recipients = await async_resolve_question_recipients(
                self._hass, [destination.get("registration_id")]
            )
        except Exception:
            return
        current = next(
            (r for r in recipients if r.registration_id == destination.get("registration_id")),
            None,
        )
        if current is None or not self._recipient_matches(destination, current):
            return
        try:
            await self._hass.services.async_call(
                "notify", service,
                {"message": "clear_notification", "data": {"tag": self._tag(entry)}},
                blocking=True,
            )
        except Exception:
            _LOGGER.warning("Question notification clearing failed for a selected device")

    async def _clear_persistent(self, entry: Mapping) -> None:
        if self._hass.services.has_service("persistent_notification", "dismiss"):
            try:
                await self._hass.services.async_call(
                    "persistent_notification", "dismiss", {"notification_id": self._tag(entry)}, blocking=True
                )
            except Exception:
                _LOGGER.warning("Persistent question notification clearing failed")

    async def _clear_entry(self, entry: Mapping) -> None:
        for destination in entry.get("destinations", []):
            await self._clear_mobile(entry, destination)
        if entry.get("persistent"):
            await self._clear_persistent(entry)

    async def _persist(self) -> bool:
        if self._closed or not self._loaded:
            return False
        try:
            await self._store.async_save({"interactions": list(self._entries.values())[-_MAX_INTERACTIONS:]})
        except Exception:
            _LOGGER.warning("Question notification ledger could not be saved")
            return False
        return True

    async def _on_action(self, event: Event) -> None:
        if (
            self._closed
            or not self._loaded
            or not self._enabled()
            or not self._runtime.supports_capability("interactions_v2")
            or event.origin is not EventOrigin.remote
            or not event.context.user_id
        ):
            return
        data = event.data
        token = data.get("action") if isinstance(data, Mapping) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", token):
            return
        async with self._lock:
            match = self._find_action(token)
            if match is None:
                return
            entry, destination, action = match
            if event.context.user_id != destination.get("user_id"):
                return
            if _expiry(entry.get("expires_at")) is None or _expiry(entry["expires_at"]) <= dt_util.utcnow():
                return
            settings = getattr(self._runtime, "question_notification_settings", {})
            mobile_targets = settings.get("mobile_targets", []) if isinstance(settings, Mapping) else []
            if not isinstance(mobile_targets, list) or not isinstance(settings, Mapping) or settings.get("enabled") is not True or destination["registration_id"] not in mobile_targets:
                return
            try:
                recipients = await async_resolve_question_recipients(self._hass, [destination["registration_id"]])
            except Exception:
                return
            recipient = next((r for r in recipients if r.registration_id == destination["registration_id"]), None)
            if recipient is None or not self._recipient_matches(destination, recipient):
                return
            try:
                response = await self._runtime.client.async_list_pending_interactions(thread_id=entry["thread_id"])
            except Exception:
                return
            if not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
                return
            current = next(
                (
                    item
                    for item in response["items"]
                    if isinstance(item, Mapping)
                    and item.get("interaction_id") == entry["interaction_id"]
                ),
                None,
            )
            if current is None or not _valid_interaction(current) or not self._same_question(entry, current):
                return
            question = _question(current)
            if question is None or question.get("question_id") != entry["question_id"]:
                return
            answers = self._answers(action, question, data)
            if answers is None:
                return
            # Record the stable request identity before sending. An ambiguous
            # result is reconciled; it is never retried with a new identity.
            if entry.get("attempted"):
                return
            entry["attempted"] = True
            request_id = action["client_request_id"]
            if not await self._persist():
                entry["attempted"] = False
                return
            dispatch_started = False
            try:
                if self._closed:
                    return
                # The pending API and durable claim both await external work.
                # Revalidate after those waits, immediately before dispatch.
                try:
                    latest_response = await self._runtime.client.async_list_pending_interactions(
                        thread_id=entry["thread_id"]
                    )
                except Exception:
                    return
                if not isinstance(latest_response, Mapping) or not isinstance(latest_response.get("items"), list):
                    return
                latest = next(
                    (
                        item for item in latest_response["items"]
                        if isinstance(item, Mapping)
                        and item.get("interaction_id") == entry["interaction_id"]
                    ),
                    None,
                )
                deadline = _expiry(latest.get("expires_at")) if isinstance(latest, Mapping) else None
                if (
                    latest is None
                    or not _valid_interaction(latest)
                    or not self._same_question(entry, latest)
                    or deadline is None
                    or deadline <= dt_util.utcnow()
                ):
                    return
                settings = getattr(self._runtime, "question_notification_settings", None)
                mobile_targets = settings.get("mobile_targets", []) if isinstance(settings, Mapping) else None
                if (
                    not isinstance(settings, Mapping)
                    or settings.get("enabled") is not True
                    or not isinstance(mobile_targets, list)
                    or destination["registration_id"] not in mobile_targets
                    or not self._runtime.supports_capability("interactions_v2")
                    or event.context.user_id != destination.get("user_id")
                    or _expiry(entry["expires_at"]) is None
                    or _expiry(entry["expires_at"]) <= dt_util.utcnow()
                ):
                    return
                try:
                    latest_recipients = await async_resolve_question_recipients(
                        self._hass, [destination["registration_id"]]
                    )
                except Exception:
                    return
                latest_recipient = next(
                    (r for r in latest_recipients if r.registration_id == destination["registration_id"]),
                    None,
                )
                final_settings = getattr(self._runtime, "question_notification_settings", None)
                final_targets = (
                    final_settings.get("mobile_targets", [])
                    if isinstance(final_settings, Mapping)
                    else None
                )
                if (
                    latest_recipient is None
                    or not self._recipient_matches(destination, latest_recipient)
                    or latest_recipient.user_id != event.context.user_id
                    or not isinstance(final_settings, Mapping)
                    or final_settings.get("enabled") is not True
                    or not isinstance(final_targets, list)
                    or not self._runtime.supports_capability("interactions_v2")
                    or destination["registration_id"] not in final_targets
                    or deadline <= dt_util.utcnow()
                    or _expiry(entry["expires_at"]) is None
                    or _expiry(entry["expires_at"]) <= dt_util.utcnow()
                ):
                    return
                dispatch_started = True
                try:
                    await self._runtime.client.async_answer_interaction(
                        entry["interaction_id"], thread_id=entry["thread_id"], answers=answers,
                        client_request_id=request_id,
                    )
                except Exception:
                    _LOGGER.warning("Question reply outcome is being reconciled")
                    return
                await self._clear_entry(entry)
                self._entries.pop(entry["interaction_id"], None)
                await self._persist()
            finally:
                if not dispatch_started:
                    await self._release_answer_claim(entry)

    async def _release_answer_claim(self, entry: dict) -> None:
        """Reopen only a claim for which no answer API attempt began."""
        entry["attempted"] = False
        try:
            persisted = await self._persist()
        except BaseException:
            # Keep concurrent events sealed even if cancellation interrupts save.
            entry["attempted"] = True
            raise
        if not persisted:
            # A failed release must remain sealed in memory.
            entry["attempted"] = True

    @staticmethod
    def _answers(action: Mapping, question: Mapping, data: Mapping) -> list[dict] | None:
        if action.get("kind") == "choice":
            labels = _choice_labels(question)
            index = action.get("index")
            if labels is None or type(index) is not int or not 0 <= index < len(labels):
                return None
            values = [labels[index]]
        elif action.get("kind") == "text" and question.get("allow_free_text") is True:
            value = data.get("reply_text")
            if not _text(value, _MAX_VALUE) or not value.strip():
                return None
            values = [value]
        else:
            return None
        return [{"question_id": question["question_id"], "values": values}]

    def _find_action(self, token: str):
        for entry in self._entries.values():
            for destination in entry["destinations"]:
                for action in destination["actions"]:
                    if action["token"] == token:
                        return entry, destination, action
        return None

    def _enabled(self) -> bool:
        settings = getattr(self._runtime, "question_notification_settings", None)
        return isinstance(settings, Mapping) and settings.get("enabled") is True
