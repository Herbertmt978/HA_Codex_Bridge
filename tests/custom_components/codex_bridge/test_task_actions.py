"""Native Home Assistant task-action and lifecycle-event behaviour."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.const import (
    CONF_ALLOW_UNATTENDED_TASK_ACTIONS,
    DATA_ENTRIES,
    DOMAIN,
)
from custom_components.codex_bridge.event_broker import EventBroker
from custom_components.codex_bridge.task_events import TaskEventForwarder
from custom_components.codex_bridge.task_services import async_register_task_services

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TASK_ID = "a" * 32
TASK_REFERENCE = {
    "task_id": TASK_ID,
    "thread_id": "thr_task_" + TASK_ID,
    "run_id": "run_123",
    "status": "running",
}


def _batch(cursor: int, event_type: str, payload: dict[str, str]) -> dict:
    return {
        "events": [
            {
                "cursor": cursor,
                "event_id": f"evt_{cursor}",
                "scope": "thread",
                "thread_id": TASK_REFERENCE["thread_id"],
                "event_type": event_type,
                "payload": payload,
                "timestamp": "2026-09-24T08:00:00Z",
            }
        ],
        "next_cursor": cursor,
        "minimum_cursor": 0,
        "has_more": False,
        "heartbeat": False,
    }


class _MemoryStore:
    def __init__(self) -> None:
        self.saved: dict[str, int] | None = None

    async def async_load(self):
        return self.saved

    async def async_save(self, data):
        self.saved = dict(data)


async def test_task_events_are_safe_and_do_not_duplicate_after_restart(hass):
    observed = []
    interactions = []
    hass.bus.async_listen(f"{DOMAIN}_task_result", observed.append)
    hass.bus.async_listen(f"{DOMAIN}_task_interaction_needed", interactions.append)
    store = _MemoryStore()
    broker = EventBroker(AsyncMock(), initial_cursor=0)
    forwarder = TaskEventForwarder(hass, broker, store)
    await forwarder.async_start()
    payload = {
        "task_id": TASK_ID,
        "run_id": "run_123",
        "status": "completed",
        "prompt": "private text",
    }
    await broker._consume(_batch(1, "task.result", payload))
    await hass.async_block_till_done()
    assert len(observed) == 1
    assert observed[0].data == {
        "task_id": TASK_ID,
        "thread_id": TASK_REFERENCE["thread_id"],
        "run_id": "run_123",
        "status": "completed",
    }
    assert store.saved == {"cursor": 1}
    await broker._consume(
        _batch(
            2,
            "task.interaction_needed",
            {
                "task_id": TASK_ID,
                "run_id": "run_123",
                "kind": "approval",
                "tool_output": "private result",
            },
        )
    )
    await hass.async_block_till_done()
    assert len(interactions) == 1
    assert interactions[0].data == {
        "task_id": TASK_ID,
        "thread_id": TASK_REFERENCE["thread_id"],
        "run_id": "run_123",
        "kind": "approval",
    }
    assert store.saved == {"cursor": 2}
    await forwarder.async_close()
    await broker.async_close()

    replay_broker = EventBroker(AsyncMock(), initial_cursor=0)
    replay_forwarder = TaskEventForwarder(hass, replay_broker, store)
    await replay_forwarder.async_start()
    await replay_broker._consume(_batch(1, "task.result", payload))
    await hass.async_block_till_done()
    assert len(observed) == 1
    assert len(interactions) == 1
    await replay_forwarder.async_close()
    await replay_broker.async_close()


async def test_task_service_requires_opt_in_for_automation_context_and_returns_reference(
    hass,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_ALLOW_UNATTENDED_TASK_ACTIONS: False},
    )
    entry.add_to_hass(hass)
    client = Mock()
    client.async_start_task = AsyncMock(return_value=TASK_REFERENCE)
    client.async_get_task = AsyncMock(return_value=TASK_REFERENCE)
    runtime = SimpleNamespace(
        entry_id=entry.entry_id,
        client=client,
        supports_capability=lambda capability: capability == "task_actions_v1",
        web_search_payload=lambda: {},
    )
    hass.data[DOMAIN] = {DATA_ENTRIES: {entry.entry_id: runtime}}
    async_register_task_services(hass)

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "start_task",
            {"project_id": "prj_home", "title": "Review", "prompt": "Check it."},
            blocking=True,
            return_response=True,
            context=Context(),
        )
    client.async_start_task.assert_not_awaited()

    admin = await hass.auth.async_create_user("Bridge administrator", group_ids=[GROUP_ID_ADMIN])
    user = await hass.auth.async_create_user("Ordinary user", group_ids=[GROUP_ID_USER])
    admin_result = await hass.services.async_call(
        DOMAIN,
        "start_task",
        {"project_id": "prj_home", "title": "Review", "prompt": "Check it."},
        blocking=True,
        return_response=True,
        context=Context(user_id=admin.id),
    )
    assert admin_result == TASK_REFERENCE
    assert client.async_start_task.await_count == 1

    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_UNATTENDED_TASK_ACTIONS: True}
    )
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "start_task",
            {"project_id": "prj_home", "title": "Review", "prompt": "Check it."},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )
    assert client.async_start_task.await_count == 1
    result = await hass.services.async_call(
        DOMAIN,
        "start_task",
        {"project_id": "prj_home", "title": "Review", "prompt": "Check it."},
        blocking=True,
        return_response=True,
        context=Context(),
    )
    assert result == TASK_REFERENCE
    supplied = client.async_start_task.await_args.args[0]
    assert len(supplied["task_id"]) == 32
    assert supplied["prompt"] == "Check it."

    shared_context = Context(user_id=admin.id)
    await hass.services.async_call(
        DOMAIN,
        "start_task",
        {"project_id": "prj_home", "title": "First", "prompt": "Check it."},
        blocking=True,
        return_response=True,
        context=shared_context,
    )
    first_id = client.async_start_task.await_args.args[0]["task_id"]
    await hass.services.async_call(
        DOMAIN,
        "start_task",
        {"project_id": "prj_home", "title": "Second", "prompt": "Check it."},
        blocking=True,
        return_response=True,
        context=shared_context,
    )
    assert client.async_start_task.await_args.args[0]["task_id"] != first_id
