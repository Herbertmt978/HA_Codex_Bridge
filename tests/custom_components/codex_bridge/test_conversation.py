"""Assist must return the selected run's answer without broadening HA access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.conversation import ConversationInput
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.conversation import (
    CodexAssistConversation,
    async_setup_entry,
)
from custom_components.codex_bridge.bridge_api import BridgeApiConnectionError, BridgeApiError
from custom_components.codex_bridge.const import (
    CONF_ASSIST_ALLOW_VOICE,
    CONF_ASSIST_ENABLED,
    CONF_ASSIST_PROJECT_ID,
    CONF_ASSIST_MODEL,
    CONF_ASSIST_REASONING,
    CONF_ASSIST_INSTRUCTIONS,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_SUPERVISOR,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _input(user_id=None, conversation_id=None, text="Hello"):
    return ConversationInput(
        text=text,
        context=Context(user_id=user_id),
        conversation_id=conversation_id,
        device_id=None,
        satellite_id=None,
        language="en",
        agent_id="conversation.codex_bridge_assist",
    )


def _agent(hass, *, allow_voice=False, settings=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_ASSIST_ENABLED: True,
            CONF_ASSIST_PROJECT_ID: "prj_assist",
            CONF_ASSIST_ALLOW_VOICE: allow_voice,
            **(settings or {}),
        },
    )
    client = Mock()
    client.async_start_task = AsyncMock(
        return_value={
            "task_id": "a" * 32,
            "thread_id": "thr_assist",
            "run_id": "run_first",
            "status": "running",
        }
    )
    client.async_continue_task = AsyncMock(
        return_value={
            "task_id": "b" * 32,
            "thread_id": "thr_assist",
            "run_id": "run_second",
            "status": "running",
        }
    )
    client.async_get_task_answer = AsyncMock(
        return_value={"status": "completed", "answer": "Hello back."}
    )
    client.async_get_status = AsyncMock(return_value=_catalogue())
    client.async_list_projects = AsyncMock(return_value=[{
        "project_id": "prj_assist", "archived_at": None,
        "default_model": "gpt-6-astra", "default_thinking_level": "high",
    }])
    client.async_get_thread = AsyncMock(return_value={
        "effective_model": "gpt-6-astra", "effective_thinking_level": "medium",
    })
    runtime = SimpleNamespace(
        client=client,
        discovery_uuid="app_discovery",
        title="Codex Bridge App",
        supports_capability=lambda capability: capability == "assist_conversation_v1",
    )
    agent = CodexAssistConversation(entry, runtime)
    agent.hass = hass
    return agent, client


def _catalogue(*, stale=False):
    return {
        "auth": {"state": "ok", "auth_mode": "chatgpt", "auth_required": False},
        "model_catalog": {
            "source": "codex-app-server", "stale": stale,
            "models": [{
                "model": "gpt-6-astra", "display_name": "GPT-6 Astra",
                "catalogued": True, "thinking_levels": ["medium", "high", "max"],
                "advertised_thinking_levels": ["medium", "high", "max"],
            }],
        },
    }


async def test_conversation_platform_requires_explicit_supervisor_opt_in(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR})
    added = Mock()
    await async_setup_entry(hass, entry, added)
    added.assert_not_called()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR},
        options={
            CONF_ASSIST_ENABLED: True,
            CONF_ASSIST_PROJECT_ID: "prj_assist",
        },
    )
    with patch("custom_components.codex_bridge.conversation.async_get_runtime", return_value=Mock()):
        await async_setup_entry(hass, entry, added)
    assert isinstance(added.call_args.args[0][0], CodexAssistConversation)


async def test_admin_gets_direct_answer_and_follow_up_uses_same_assist_thread(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])

    first = await agent._async_handle_message(_input(admin.id), chat_log)
    assert first.conversation_id == "ha_conversation"
    assert first.continue_conversation is True
    assert (
        chat_log.async_add_assistant_content_without_tools.call_args.args[0].content
        == "Hello back."
    )
    started = client.async_start_task.await_args.args[0]
    assert started["project_id"] == "prj_assist"
    assert started["mode"] == "observe"
    assert started["assist"] is True
    assert started["web_search"] == "disabled"

    follow_up = await agent._async_handle_message(
        _input(admin.id, first.conversation_id, "And again?"), chat_log
    )
    assert follow_up.conversation_id == first.conversation_id
    continued = client.async_continue_task.await_args.args[0]
    assert continued["thread_id"] == "thr_assist"
    assert continued["assist"] is True


async def test_conversation_entity_processes_a_real_ha_chat_log(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)

    result = await agent.async_process(_input(admin.id, text="Say hello"))

    assert result.response.as_dict()["speech"]["plain"]["speech"] == "Hello back."
    assert result.conversation_id
    assert client.async_start_task.await_count == 1


async def test_first_request_accepts_home_assistant_supplied_conversation_id(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)

    result = await agent.async_process(
        _input(admin.id, conversation_id="client-first-id", text="Say hello")
    )

    assert result.response.as_dict()["speech"]["plain"]["speech"] == "Hello back."
    assert result.conversation_id == "client-first-id"
    assert client.async_start_task.await_count == 1


async def test_old_chat_log_does_not_silently_start_a_new_codex_thread(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    chat_log = Mock(
        conversation_id="old-session",
        content=[SimpleNamespace(role="system"), SimpleNamespace(role="assistant")],
    )

    result = await agent._async_handle_message(
        _input(admin.id, conversation_id="old-session"), chat_log
    )

    assert result.conversation_id is None
    assert "expired" in chat_log.async_add_assistant_content_without_tools.call_args.args[0].content
    client.async_start_task.assert_not_awaited()


async def test_voice_and_non_admin_are_denied_without_explicit_authority(hass):
    await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    ordinary = await hass.auth.async_create_user(
        "Ordinary user", group_ids=[GROUP_ID_USER]
    )
    assert ordinary.is_admin is False
    agent, client = _agent(hass)
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])

    voice = await agent._async_handle_message(_input(), chat_log)
    assert voice.conversation_id is None
    non_admin = await agent._async_handle_message(_input(ordinary.id), chat_log)
    assert non_admin.conversation_id is None
    client.async_start_task.assert_not_awaited()


async def test_explicit_voice_opt_in_allows_bounded_observe_request(hass):
    agent, client = _agent(hass, allow_voice=True)
    chat_log = Mock(conversation_id="voice_conversation", content=[object()])
    result = await agent._async_handle_message(_input(), chat_log)
    assert result.conversation_id == "voice_conversation"
    assert client.async_start_task.await_args.args[0]["mode"] == "observe"


async def test_in_progress_answer_is_not_mistaken_for_a_completed_reply(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    client.async_get_task_answer = AsyncMock(
        return_value={"status": "running", "answer": None}
    )
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])
    with patch(
        "custom_components.codex_bridge.conversation._RESPONSE_TIMEOUT_SECONDS", 0.01
    ):
        first = await agent._async_handle_message(_input(admin.id), chat_log)
    assert first.conversation_id == "ha_conversation"
    assert (
        "still working"
        in chat_log.async_add_assistant_content_without_tools.call_args.args[0].content
    )

    await agent._async_handle_message(_input(admin.id, first.conversation_id), chat_log)
    client.async_continue_task.assert_not_awaited()


async def test_lost_admission_response_replays_the_same_task_id(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    accepted = client.async_start_task.return_value
    client.async_start_task.side_effect = [
        BridgeApiConnectionError(),
        BridgeApiConnectionError(),
        accepted,
    ]
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])

    uncertain = await agent._async_handle_message(_input(admin.id), chat_log)
    assert uncertain.conversation_id == "ha_conversation"
    recovered = await agent._async_handle_message(
        _input(admin.id, uncertain.conversation_id), chat_log
    )
    assert recovered.conversation_id == uncertain.conversation_id
    task_ids = {
        call.args[0]["task_id"] for call in client.async_start_task.await_args_list
    }
    assert len(task_ids) == 1
    client.async_continue_task.assert_not_awaited()
    assert (
        chat_log.async_add_assistant_content_without_tools.call_args.args[0].content
        == "Hello back."
    )


async def test_retryable_admission_error_keeps_the_same_task_id(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    accepted = client.async_start_task.return_value
    client.async_start_task.side_effect = [
        BridgeApiError("event_store_capacity_exhausted", status=507, retryable=True),
        accepted,
    ]
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])

    uncertain = await agent._async_handle_message(_input(admin.id), chat_log)
    assert uncertain.conversation_id == "ha_conversation"
    recovered = await agent._async_handle_message(
        _input(admin.id, uncertain.conversation_id), chat_log
    )

    assert recovered.conversation_id == uncertain.conversation_id
    assert len({call.args[0]["task_id"] for call in client.async_start_task.await_args_list}) == 1
    client.async_continue_task.assert_not_awaited()


async def test_answer_from_timed_out_turn_is_delivered_before_new_answer(hass):
    admin = await hass.auth.async_create_user(
        "Administrator", group_ids=[GROUP_ID_ADMIN]
    )
    agent, client = _agent(hass)
    client.async_get_task_answer = AsyncMock(
        return_value={"status": "running", "answer": None}
    )
    chat_log = Mock(conversation_id="ha_conversation", content=[object()])
    with patch(
        "custom_components.codex_bridge.conversation._RESPONSE_TIMEOUT_SECONDS", 0.01
    ):
        first = await agent._async_handle_message(_input(admin.id), chat_log)

    client.async_get_task_answer.side_effect = [
        {"status": "completed", "answer": "The earlier answer."},
        {"status": "completed", "answer": "The new answer."},
    ]
    await agent._async_handle_message(
        _input(admin.id, first.conversation_id, "A new question"), chat_log
    )
    speech = chat_log.async_add_assistant_content_without_tools.call_args.args[
        0
    ].content
    assert "The earlier answer." in speech
    assert "The new answer." in speech
    assert client.async_continue_task.await_count == 1


async def test_assist_entity_keeps_identity_and_attaches_to_bridge_device(hass):
    agent, _client = _agent(hass)
    assert agent.unique_id == f"{agent._entry.entry_id}_assist"
    assert agent.device_info["identifiers"] == {(DOMAIN, "app_discovery")}
    assert agent.device_info["name"] == "Codex Bridge App"


async def test_explicit_model_reasoning_and_plain_instructions_are_used(hass):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    instructions = "Be brief. Keep {{ states('sensor.private') }} as literal text."
    agent, client = _agent(hass, settings={
        CONF_ASSIST_MODEL: "gpt-6-astra",
        CONF_ASSIST_REASONING: "medium",
        CONF_ASSIST_INSTRUCTIONS: instructions,
    })
    chat_log = Mock(conversation_id="configured", content=[object()])
    first = await agent._async_handle_message(_input(admin.id), chat_log)
    payload = client.async_start_task.await_args.args[0]
    assert payload["model_override"] == "gpt-6-astra"
    assert payload["thinking_override"] == "medium"
    assert payload["prompt"] == f"Conversation instructions:\n{instructions}\n\nUser question:\nHello"
    assert payload["mode"] == "observe"
    await agent._async_handle_message(_input(admin.id, first.conversation_id, "Again"), chat_log)
    continued = client.async_continue_task.await_args.args[0]
    assert continued["thread_id"] == "thr_assist"
    assert continued["prompt"].endswith("User question:\nAgain")
    assert client.async_get_status.await_count == 2


async def test_unset_assist_model_preserves_existing_project_defaults(hass):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    agent, client = _agent(hass)
    await agent._async_handle_message(_input(admin.id), Mock(conversation_id="default", content=[]))
    payload = client.async_start_task.await_args.args[0]
    assert "model_override" not in payload
    assert "thinking_override" not in payload
    assert payload["prompt"] == "Hello"
    client.async_get_status.assert_not_awaited()


@pytest.mark.parametrize("settings, expected_field, expected_value", [
    ({CONF_ASSIST_MODEL: "gpt-6-astra"}, "model_override", "gpt-6-astra"),
    ({CONF_ASSIST_REASONING: "max"}, "thinking_override", "max"),
])
async def test_one_explicit_setting_inherits_the_other_project_default(
    hass, settings, expected_field, expected_value
):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    agent, client = _agent(hass, settings=settings)
    await agent._async_handle_message(_input(admin.id), Mock(conversation_id="partial", content=[]))
    payload = client.async_start_task.await_args.args[0]
    assert payload[expected_field] == expected_value
    omitted_field = "thinking_override" if expected_field == "model_override" else "model_override"
    assert omitted_field not in payload


async def test_accepted_retry_keeps_its_identity_after_catalogue_changes(hass):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    agent, client = _agent(hass, settings={
        CONF_ASSIST_MODEL: "gpt-6-astra", CONF_ASSIST_REASONING: "medium",
    })
    accepted = client.async_start_task.return_value
    client.async_start_task.side_effect = [
        BridgeApiConnectionError(), BridgeApiConnectionError(), accepted,
    ]
    chat_log = Mock(conversation_id="configured", content=[])
    uncertain = await agent._async_handle_message(_input(admin.id), chat_log)
    client.async_get_status.return_value = _catalogue(stale=True)
    recovered = await agent._async_handle_message(_input(admin.id, uncertain.conversation_id), chat_log)
    assert recovered.conversation_id == uncertain.conversation_id
    assert len({call.args[0]["task_id"] for call in client.async_start_task.await_args_list}) == 1
    assert client.async_get_status.await_count == 1
    client.async_continue_task.assert_not_awaited()
    assert chat_log.async_add_assistant_content_without_tools.call_args.args[0].content == "Hello back."


async def test_stale_or_removed_model_fails_closed_without_substitution(hass):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    agent, client = _agent(hass, settings={
        CONF_ASSIST_MODEL: "gpt-6-astra", CONF_ASSIST_REASONING: "medium",
    })
    chat_log = Mock(conversation_id="configured", content=[])
    client.async_get_status.return_value = _catalogue(stale=True)
    refused = await agent._async_handle_message(_input(admin.id), chat_log)
    assert refused.conversation_id is None
    client.async_start_task.assert_not_awaited()

    client.async_get_status.return_value = _catalogue()
    first = await agent._async_handle_message(_input(admin.id), chat_log)
    client.async_get_status.return_value = _catalogue(stale=True)
    await agent._async_handle_message(_input(admin.id, first.conversation_id, "Again"), chat_log)
    client.async_continue_task.assert_not_awaited()
    assert "unavailable" in chat_log.async_add_assistant_content_without_tools.call_args.args[0].content


async def test_oversized_instructions_never_reach_codex(hass):
    admin = await hass.auth.async_create_user("Administrator", group_ids=[GROUP_ID_ADMIN])
    agent, client = _agent(hass, settings={CONF_ASSIST_INSTRUCTIONS: "x" * 4097})
    await agent._async_handle_message(_input(admin.id), Mock(conversation_id="invalid", content=[]))
    client.async_start_task.assert_not_awaited()
