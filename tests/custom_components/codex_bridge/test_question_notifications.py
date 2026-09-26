"""Focused tests for the bounded question notification coordinator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import EventOrigin

from custom_components.codex_bridge.question_notification_recipients import (
    QuestionNotificationRecipient,
)
from custom_components.codex_bridge.question_notifications import (
    QuestionNotificationCoordinator,
)


class _Store:
    def __init__(self, value=None):
        self.value = value
        self.load_count = 0
        self.save_count = 0

    async def async_load(self):
        self.load_count += 1
        return deepcopy(self.value)

    async def async_save(self, value):
        self.save_count += 1
        self.value = deepcopy(value)


class _Services:
    def __init__(self, *, fail=False):
        self.available = {
            ("notify", "mobile_app_phone"),
            ("persistent_notification", "create"),
            ("persistent_notification", "dismiss"),
        }
        self.calls = []
        self.fail = fail

    def has_service(self, domain, service):
        return (domain, service) in self.available

    async def async_call(self, domain, service, data, **_kwargs):
        if self.fail:
            raise RuntimeError("private service detail")
        self.calls.append((domain, service, deepcopy(data)))


def _question(*, allow_free_text=True):
    return {
        "question_id": "scope",
        "header": "Scope",
        "prompt": "Which files should Codex update?",
        "options": [
            {"label": "Source only", "description": "Source files."},
            {"label": "Source and docs", "description": "Source and documentation."},
        ],
        "multiple": False,
        "allow_free_text": allow_free_text,
    }


def _interaction(*, questions=None, expires="2099-01-01T00:00:00Z"):
    return {
        "interaction_id": "question-1",
        "thread_id": "thread-1",
        "kind": "user_input",
        "status": "pending",
        "expires_at": expires,
        "display": {"questions": questions if questions is not None else [_question()]},
        "allowed_actions": ["answer", "cancel"],
    }


def _recipient(*, user_id="admin-user", inline_reply=True):
    return QuestionNotificationRecipient(
        registration_id="phone-registration",
        device_id="phone-device",
        user_id=user_id,
        service="mobile_app_phone",
        label="Phone",
        inline_reply=inline_reply,
    )


def _fixture(monkeypatch, interactions=None, *, settings=None, store=None, services=None, recipients=None):
    monkeypatch.setattr(
        "custom_components.codex_bridge.question_notifications.async_track_time_interval",
        lambda *_args: lambda: None,
    )
    recipient_values = [_recipient()] if recipients is None else recipients

    async def resolve(_hass, registration_ids):
        return tuple(r for r in recipient_values if r.registration_id in registration_ids)

    monkeypatch.setattr(
        "custom_components.codex_bridge.question_notifications.async_resolve_question_recipients",
        resolve,
    )
    client = SimpleNamespace(
        async_list_pending_interactions=AsyncMock(
            return_value={"items": [_interaction()] if interactions is None else interactions}
        ),
        async_answer_interaction=AsyncMock(return_value={"status": "accepted"}),
    )
    runtime = SimpleNamespace(
        client=client,
        event_broker=None,
        question_notification_settings=settings
        or {"enabled": True, "persistent": True, "preview": False, "mobile_targets": ["phone-registration"]},
        supports_capability=lambda capability: capability == "interactions_v2",
    )
    services = services or _Services()
    hass = SimpleNamespace(
        services=services,
        bus=SimpleNamespace(async_listen=lambda *_args: lambda: None),
        async_create_task=lambda coro, name=None: asyncio.create_task(coro, name=name),
    )
    coordinator = QuestionNotificationCoordinator(hass, runtime, store or _Store())
    return coordinator, runtime, client, services


def _event(token, *, user_id="admin-user", origin=EventOrigin.remote, reply_text=None):
    data = {"action": token}
    if reply_text is not None:
        data["reply_text"] = reply_text
    return SimpleNamespace(
        origin=origin,
        context=SimpleNamespace(user_id=user_id),
        data=data,
    )


def _tokens(services):
    mobile = next(call[2] for call in services.calls if call[:2] == ("notify", "mobile_app_phone"))
    return mobile, [a["action"] for a in mobile["data"]["actions"] if a["action"] not in {"URI"}]


async def test_opt_in_privacy_choice_reply_and_clear_all(monkeypatch):
    settings = {"enabled": True, "persistent": True, "preview": True, "mobile_targets": ["phone-registration"]}
    coordinator, _runtime, client, services = _fixture(
        monkeypatch, [_interaction(questions=[_question(allow_free_text=False)])], settings=settings
    )
    await coordinator.async_start()
    persistent = next(call[2] for call in services.calls if call[:2] == ("persistent_notification", "create"))
    mobile, tokens = _tokens(services)
    assert "A Codex question is waiting" in persistent["message"]
    assert "Which files" not in persistent["message"]
    assert "&interaction=question-1" in persistent["message"]
    assert mobile["title"] == "Scope"
    assert mobile["message"] == "Which files should Codex update?"
    assert [a["title"] for a in mobile["data"]["actions"]] == ["Source only", "Source and docs", "Open chat"]
    assert mobile["data"]["authenticationRequired"] is True
    assert all(a.get("authenticationRequired") is True for a in mobile["data"]["actions"][:-1])
    await coordinator._on_action(_event(tokens[1]))
    client.async_answer_interaction.assert_awaited_once()
    assert client.async_answer_interaction.await_args.kwargs["answers"] == [
        {"question_id": "scope", "values": ["Source and docs"]}
    ]
    assert all(call[0] != "notify" or call[2].get("message") == "clear_notification" for call in services.calls[2:])
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 1
    await coordinator.async_close()


async def test_default_choice_only_question_has_open_chat_without_blind_choices(monkeypatch):
    coordinator, _runtime, client, services = _fixture(
        monkeypatch, [_interaction(questions=[_question(allow_free_text=False)])]
    )
    await coordinator.async_start()
    mobile, _tokens_value = _tokens(services)
    assert mobile["title"] == "Codex Bridge"
    assert mobile["message"] == "A question needs your response."
    assert [a["title"] for a in mobile["data"]["actions"]] == ["Open chat"]
    client.async_answer_interaction.assert_not_awaited()
    await coordinator.async_close()


async def test_sensitive_preview_request_uses_generic_open_chat_fallback(monkeypatch):
    sensitive = {**_question(), "prompt": "Enter the API key: secret=abc"}
    settings = {"enabled": True, "persistent": False, "preview": True, "mobile_targets": ["phone-registration"]}
    coordinator, _runtime, _client, services = _fixture(
        monkeypatch, [_interaction(questions=[sensitive])], settings=settings
    )
    await coordinator.async_start()
    mobile, _tokens_value = _tokens(services)
    assert mobile["title"] == "Codex Bridge"
    assert mobile["message"] == "A question needs your response."
    assert [a["title"] for a in mobile["data"]["actions"]] == ["Open chat"]
    await coordinator.async_close()


async def test_secret_marked_question_uses_open_chat_only(monkeypatch):
    secret_marked = {**_question(), "isSecret": True}
    settings = {"enabled": True, "persistent": False, "preview": True, "mobile_targets": ["phone-registration"]}
    coordinator, _runtime, _client, services = _fixture(
        monkeypatch, [_interaction(questions=[secret_marked])], settings=settings
    )
    await coordinator.async_start()
    mobile, _tokens_value = _tokens(services)
    assert [a["title"] for a in mobile["data"]["actions"]] == ["Open chat"]
    await coordinator.async_close()


async def test_preview_is_explicit_and_text_reply_preserves_stable_request_id(monkeypatch):
    store = _Store()
    settings = {"enabled": True, "persistent": False, "preview": True, "mobile_targets": ["phone-registration"]}
    coordinator, _runtime, client, services = _fixture(monkeypatch, settings=settings, store=store)
    await coordinator.async_start()
    mobile, tokens = _tokens(services)
    assert mobile["title"] == "Scope"
    assert mobile["message"] == "Which files should Codex update?"
    action = next(a for a in coordinator._entries["question-1"]["destinations"][0]["actions"] if a["kind"] == "text")
    saved_request_id = action["client_request_id"]
    await coordinator._on_action(_event(action["token"], reply_text="Update the docs too"))
    assert client.async_answer_interaction.await_args.kwargs["answers"] == [
        {"question_id": "scope", "values": ["Update the docs too"]}
    ]
    assert client.async_answer_interaction.await_args.kwargs["client_request_id"] == saved_request_id
    assert len(tokens) == 2
    await coordinator.async_close()


async def test_complex_form_only_opens_chat(monkeypatch):
    complex_form = [_question(), {**_question(), "question_id": "other"}]
    coordinator, _runtime, client, services = _fixture(monkeypatch, [_interaction(questions=complex_form)])
    await coordinator.async_start()
    mobile = next(call[2] for call in services.calls if call[:2] == ("notify", "mobile_app_phone"))
    assert [a["action"] for a in mobile["data"]["actions"]] == ["URI"]
    assert mobile["data"]["actions"][0]["title"] == "Open chat"
    await coordinator._on_action(_event("random-unrelated-action-token-123"))
    client.async_answer_interaction.assert_not_awaited()
    await coordinator.async_close()


@pytest.mark.parametrize(
    "user_id,origin",
    [("other-user", EventOrigin.remote), ("admin-user", EventOrigin.local)],
)
async def test_event_origin_user_and_current_recipient_are_required(monkeypatch, user_id, origin):
    recipients = [_recipient()]
    coordinator, _runtime, client, services = _fixture(monkeypatch, recipients=recipients)
    await coordinator.async_start()
    _mobile, tokens = _tokens(services)
    await coordinator._on_action(_event(tokens[0], user_id=user_id, origin=origin))
    assert client.async_answer_interaction.await_count == 0
    await coordinator.async_close()


async def test_expiry_revocation_and_pending_lookup_failure_do_not_answer_or_clear(monkeypatch):
    coordinator, runtime, client, services = _fixture(monkeypatch)
    await coordinator.async_start()
    _mobile, tokens = _tokens(services)
    client.async_list_pending_interactions.side_effect = RuntimeError("transient")
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 0
    assert len(coordinator._entries) == 1
    client.async_list_pending_interactions.side_effect = None
    runtime.question_notification_settings["mobile_targets"] = []
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 0
    runtime.question_notification_settings["mobile_targets"] = ["phone-registration"]
    client.async_list_pending_interactions.return_value = {
        "items": [_interaction(expires="2000-01-01T00:00:00Z")]
    }
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 0
    await coordinator.async_close()


async def test_unknown_answer_outcome_is_not_replayed_and_resolved_clears_all(monkeypatch):
    coordinator, _runtime, client, services = _fixture(monkeypatch)
    await coordinator.async_start()
    _mobile, tokens = _tokens(services)
    client.async_answer_interaction.side_effect = RuntimeError("unknown")
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 1
    await coordinator._on_action(_event(tokens[0]))
    assert client.async_answer_interaction.await_count == 1
    client.async_answer_interaction.side_effect = None
    client.async_list_pending_interactions.return_value = {"items": []}
    await coordinator.async_refresh()
    assert any(call[:2] == ("persistent_notification", "dismiss") for call in services.calls)
    assert any(call[:2] == ("notify", "mobile_app_phone") and call[2].get("message") == "clear_notification" for call in services.calls)
    await coordinator.async_close()


async def test_service_failure_is_claimed_once_and_ledger_is_bounded(monkeypatch, caplog):
    services = _Services(fail=True)
    many = [
        {**_interaction(), "interaction_id": f"question-{index}"}
        for index in range(140)
    ]
    coordinator, _runtime, client, _services = _fixture(monkeypatch, many, services=services)
    await coordinator.async_start()
    assert len(coordinator._entries) == 128
    before = client.async_list_pending_interactions.await_count
    await coordinator.async_refresh()
    assert client.async_list_pending_interactions.await_count == before + 1
    assert "private service detail" not in caplog.text
    await coordinator.async_close()


async def test_restart_restores_only_current_matching_question_and_unload_removes_listeners(monkeypatch):
    store = _Store()
    first, _runtime, _client, services = _fixture(monkeypatch, store=store)
    await first.async_start()
    saved_action = first._entries["question-1"]["destinations"][0]["actions"][0]["token"]
    await first.async_close()
    second, _runtime2, client2, _services2 = _fixture(monkeypatch, store=store, services=services)
    await second.async_start()
    assert second._entries["question-1"]["destinations"][0]["actions"][0]["token"] == saved_action
    assert len([c for c in services.calls if c[:2] == ("notify", "mobile_app_phone")]) == 1
    await second.async_close()


async def test_disabled_by_default_does_not_subscribe_or_poll(monkeypatch):
    settings = {"enabled": False, "persistent": True, "preview": False, "mobile_targets": []}
    coordinator, _runtime, client, _services = _fixture(monkeypatch, settings=settings)
    await coordinator.async_start()
    client.async_list_pending_interactions.assert_not_awaited()
    assert coordinator._remove_bus is None
    assert coordinator._remove_broker is None
    assert coordinator._remove_timer is None
    await coordinator.async_start()
    assert coordinator._store.load_count == 1
    assert coordinator._store.save_count == 0
    await coordinator.async_close()


async def test_store_load_failure_stops_startup_without_overwriting_unknown_ledger(monkeypatch):
    store = _Store({"unrelated": "must remain untouched"})
    original_load = store.async_load
    store.async_load = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    coordinator, _runtime, client, services = _fixture(monkeypatch, store=store)
    await coordinator.async_refresh()
    assert client.async_list_pending_interactions.await_count == 0
    assert services.calls == []
    assert coordinator._remove_bus is None
    assert store.save_count == 0
    assert store.value == {"unrelated": "must remain untouched"}
    store.async_load = AsyncMock(return_value=None)
    await coordinator.async_start()
    assert client.async_list_pending_interactions.await_count == 1
    assert services.calls
    assert store.save_count > 0
    assert original_load is not None
    await coordinator.async_close()


async def test_malformed_ledger_fails_closed_without_partial_replacement(monkeypatch):
    saved = {"interactions": [{"interaction_id": "question-1", "attempted": True}]}
    store = _Store(saved)
    coordinator, runtime, client, services = _fixture(monkeypatch, store=store)
    runtime.supports_capability = lambda _capability: False
    await coordinator.async_refresh()
    await coordinator._on_action(_event("a" * 32, reply_text="Source only"))
    assert client.async_list_pending_interactions.await_count == 0
    assert client.async_answer_interaction.await_count == 0
    assert services.calls == []
    assert store.value == saved
    assert store.save_count == 0
    await coordinator.async_close()


@pytest.mark.parametrize("change", ["disabled", "revoked", "expired"])
async def test_answer_proofs_are_rechecked_after_durable_claim_wait(
    monkeypatch, change
):
    store = _Store()
    settings = {"enabled": True, "persistent": False, "preview": False, "mobile_targets": ["phone-registration"]}
    recipients = [_recipient()]
    coordinator, runtime, client, services = _fixture(
        monkeypatch, settings=settings, store=store, recipients=recipients
    )
    await coordinator.async_start()
    _mobile, tokens = _tokens(services)
    original_save = store.async_save
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def delayed_save(value):
        save_started.set()
        await release_save.wait()
        await original_save(value)

    store.async_save = delayed_save
    action = asyncio.create_task(
        coordinator._on_action(_event(tokens[0], reply_text="Source only"))
    )
    await save_started.wait()
    if change == "disabled":
        runtime.question_notification_settings["enabled"] = False
    elif change == "revoked":
        recipients.clear()
    else:
        monkeypatch.setattr(
            "custom_components.codex_bridge.question_notifications.dt_util.utcnow",
            lambda: datetime(2100, 1, 1, tzinfo=UTC),
        )
    release_save.set()
    await action
    client.async_answer_interaction.assert_not_awaited()
    await coordinator.async_close()


async def test_failed_delivery_claim_rolls_back_and_retries_same_action(monkeypatch):
    settings = {"enabled": True, "persistent": False, "preview": False, "mobile_targets": ["phone-registration"]}
    store = _Store()
    original_save = store.async_save

    async def fail_second_save(value):
        if store.save_count == 1:
            raise RuntimeError("temporary store error")
        await original_save(value)

    store.async_save = fail_second_save
    coordinator, _runtime, _client, services = _fixture(
        monkeypatch, settings=settings, store=store
    )
    await coordinator.async_start()
    destination = coordinator._entries["question-1"]["destinations"][0]
    token = destination["actions"][0]["token"]
    assert destination["attempted"] is False
    assert not any(call[:2] == ("notify", "mobile_app_phone") for call in services.calls)
    store.async_save = original_save
    await coordinator.async_refresh()
    assert coordinator._entries["question-1"]["destinations"][0]["actions"][0]["token"] == token
    assert coordinator._entries["question-1"]["destinations"][0]["attempted"] is True
    assert len([call for call in services.calls if call[:2] == ("notify", "mobile_app_phone")]) == 1
    await coordinator.async_close()


async def test_capability_loss_clears_notifications_and_invalidates_actions(monkeypatch):
    coordinator, runtime, client, services = _fixture(monkeypatch)
    await coordinator.async_start()
    _mobile, tokens = _tokens(services)
    runtime.supports_capability = lambda _capability: False
    await coordinator.async_refresh()
    assert coordinator._entries == {}
    assert any(call[:2] == ("persistent_notification", "dismiss") for call in services.calls)
    await coordinator._on_action(_event(tokens[0], reply_text="Source only"))
    client.async_answer_interaction.assert_not_awaited()
    await coordinator.async_close()


async def test_unknown_outcome_survives_restart_without_new_correlation(monkeypatch):
    store = _Store()
    first, _runtime, client, services = _fixture(monkeypatch, store=store)
    await first.async_start()
    _mobile, tokens = _tokens(services)
    client.async_answer_interaction.side_effect = RuntimeError("unknown")
    await first._on_action(_event(tokens[0], reply_text="Source only"))
    action = first._find_action(tokens[0])[2]
    request_id = action["client_request_id"]
    await first.async_close()
    second, _runtime2, client2, _services2 = _fixture(monkeypatch, store=store, services=services)
    await second.async_start()
    assert second._find_action(tokens[0])[2]["client_request_id"] == request_id
    await second._on_action(_event(tokens[0], reply_text="Source only"))
    client2.async_answer_interaction.assert_not_awaited()
    await second.async_close()


async def test_question_fingerprint_rejects_stale_choice_after_projection_changes(monkeypatch):
    coordinator, _runtime, client, services = _fixture(monkeypatch)
    await coordinator.async_start()
    _mobile, old_tokens = _tokens(services)
    changed = _interaction(questions=[
        {**_question(allow_free_text=False), "options": [
            {"label": "Docs only", "description": "Docs."},
            {"label": "Source only", "description": "Source."},
        ]}
    ])
    client.async_list_pending_interactions.return_value = {"items": [changed]}
    await coordinator.async_refresh()
    await coordinator._on_action(_event(old_tokens[0]))
    client.async_answer_interaction.assert_not_awaited()
    await coordinator.async_close()


async def test_broker_listener_schedules_coalesced_refresh_and_unload_cancels_it(monkeypatch):
    coordinator, runtime, client, _services = _fixture(monkeypatch, interactions=[])

    class _Broker:
        listener = None

        def add_async_listener(self, listener):
            self.listener = listener
            return lambda: setattr(self, "listener", None)

    broker = _Broker()
    runtime.event_broker = broker
    await coordinator.async_start()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_pending(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"items": []}

    client.async_list_pending_interactions.side_effect = blocked_pending
    await broker.listener(SimpleNamespace(event_type="interaction.created"))
    await started.wait()
    assert coordinator._refresh_task is not None
    assert not coordinator._refresh_task.done()
    await coordinator.async_close()
    assert coordinator._refresh_task is None
    assert broker.listener is None
    assert client.async_list_pending_interactions.await_count == 2
