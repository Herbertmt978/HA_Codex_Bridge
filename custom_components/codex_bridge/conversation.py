"""Opt-in, bounded Home Assistant Assist conversations with Codex."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.components.conversation.chat_log import AssistantContent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge_api import BridgeApiConnectionError, BridgeApiError
from .const import (
    CONF_ASSIST_ALLOW_VOICE,
    CONF_ASSIST_ENABLED,
    CONF_ASSIST_PROJECT_ID,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_SUPERVISOR,
)
from .runtime import CodexBridgeRuntime, async_get_runtime


_MAX_PROMPT_CHARS = 4096
_MAX_SESSIONS = 64
_RESPONSE_TIMEOUT_SECONDS = 90
_POLL_SECONDS = 1.5
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


@dataclass(slots=True)
class _AssistSession:
    thread_id: str | None
    owner_id: str | None
    pending_task_id: str | None = None
    pending_prompt: str | None = None
    pending_payload: dict | None = None

    def clear_pending(self) -> None:
        self.pending_task_id = None
        self.pending_prompt = None
        self.pending_payload = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a selectable agent only after an administrator has chosen its project."""

    if (
        entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_SUPERVISOR
        or entry.options.get(CONF_ASSIST_ENABLED) is not True
        or not entry.options.get(CONF_ASSIST_PROJECT_ID)
    ):
        return
    async_add_entities([CodexAssistConversation(entry, async_get_runtime(hass))])


class CodexAssistConversation(ConversationEntity):
    """Return a Codex answer from an explicitly exposed, observe-only project."""

    _attr_name = "Codex Bridge Assist"
    _attr_icon = "mdi:robot-outline"

    def __init__(self, entry: ConfigEntry, runtime: CodexBridgeRuntime) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_assist"
        self._sessions: OrderedDict[str, _AssistSession] = OrderedDict()
        # Reject concurrent voice requests rather than queueing unbounded work.
        self._request_lock = asyncio.Lock()

    @property
    def supported_languages(self) -> Literal["*"]:
        return "*"

    async def _async_handle_message(
        self, user_input: ConversationInput, chat_log: ChatLog
    ) -> ConversationResult:
        owner = user_input.context.user_id
        if owner is None:
            if self._entry.options.get(CONF_ASSIST_ALLOW_VOICE) is not True:
                return self._reply(
                    user_input,
                    chat_log,
                    "Ask a Home Assistant administrator to enable voice requests for Codex Bridge Assist.",
                )
        else:
            user = await self.hass.auth.async_get_user(owner)
            if user is None or not user.is_admin:
                return self._reply(
                    user_input,
                    chat_log,
                    "Codex Bridge Assist is available only to Home Assistant administrators.",
                )

        prompt = user_input.text.strip()
        if not prompt or len(prompt) > _MAX_PROMPT_CHARS:
            return self._reply(
                user_input,
                chat_log,
                "Please ask a shorter question, up to 4,096 characters.",
            )
        if not self._runtime.supports_capability("assist_conversation_v1"):
            return self._reply(
                user_input,
                chat_log,
                "Update the Codex Bridge App to use Assist conversations.",
            )
        if self._request_lock.locked():
            return self._reply(
                user_input,
                chat_log,
                "Codex is answering another Assist request. Try again shortly.",
            )

        async with self._request_lock:
            # Home Assistant may provide an ID on the first request and may
            # replace an unknown ULID before creating the chat log. Its log is
            # the authoritative session identity, not the incoming ID.
            conversation_id = chat_log.conversation_id
            session = self._sessions.get(conversation_id)
            if (session is not None and session.owner_id != owner) or (
                session is None
                and any(
                    getattr(message, "role", None) == "assistant"
                    for message in chat_log.content
                )
            ):
                return self._reply(
                    user_input,
                    chat_log,
                    "This Assist conversation has expired. Start a new conversation to ask Codex again.",
                )
            if session is None:
                if len(self._sessions) >= _MAX_SESSIONS:
                    old_id = next(
                        (
                            key
                            for key, item in self._sessions.items()
                            if item.pending_task_id is None
                        ),
                        None,
                    )
                    if old_id is None:
                        return self._reply(
                            user_input,
                            chat_log,
                            "Codex Bridge Assist is at capacity. Please try again later.",
                        )
                    self._sessions.pop(old_id)
                session = _AssistSession(None, owner)
                self._sessions[conversation_id] = session
            else:
                self._sessions.move_to_end(conversation_id)

            previous_answer = None
            if session is not None and session.pending_task_id is not None:
                try:
                    if session.pending_payload is not None:
                        await self._submit_pending(session)
                    previous = await self._runtime.client.async_get_task_answer(
                        session.pending_task_id
                    )
                except BridgeApiError:
                    return self._reply(
                        user_input,
                        chat_log,
                        "I could not confirm the previous Codex request. Please try again shortly; it will keep the same task ID.",
                        conversation_id,
                    )
                if previous["status"] not in _TERMINAL:
                    return self._reply(
                        user_input,
                        chat_log,
                        "Codex is still working on the previous question. Please try again shortly.",
                        conversation_id,
                    )
                previous_answer = (
                    previous["answer"]
                    if previous["status"] == "completed" and previous["answer"]
                    else "Codex finished without an answer to the previous question."
                )
                same_question = prompt == session.pending_prompt
                session.clear_pending()
                if same_question:
                    return self._reply(
                        user_input, chat_log, previous_answer, conversation_id
                    )

            task_id = uuid4().hex
            payload = {
                "task_id": task_id,
                "prompt": prompt,
                "assist": True,
                "web_search": "disabled",
            }
            if session.thread_id is None:
                payload.update(
                    {
                        "project_id": self._entry.options[CONF_ASSIST_PROJECT_ID],
                        "title": "Assist conversation",
                        "mode": "observe",
                    }
                )
            else:
                payload["thread_id"] = session.thread_id
            session.pending_task_id = task_id
            session.pending_prompt = prompt
            session.pending_payload = payload
            try:
                try:
                    await self._submit_pending(session)
                except BridgeApiConnectionError:
                    # The App may have accepted the request before the response
                    # was lost. Replay the identical idempotent task once.
                    await self._submit_pending(session)
                answer = await self._wait_for_answer(task_id)
            except TimeoutError:
                answer = {"status": "running", "answer": None}
            except BridgeApiError as error:
                if isinstance(error, BridgeApiConnectionError):
                    return self._reply(
                        user_input,
                        chat_log,
                        "I could not confirm the Codex request. Please try again shortly; it will keep the same task ID.",
                        conversation_id,
                    )
                if session.pending_payload is None:
                    # Admission succeeded; a failed status read does not
                    # establish that the turn was not accepted.
                    return self._reply(
                        user_input,
                        chat_log,
                        "Codex accepted the request, but its answer is unavailable right now. Please try again shortly.",
                        conversation_id,
                    )
                session.clear_pending()
                if session.thread_id is None:
                    self._sessions.pop(conversation_id, None)
                if error.code == "assist_mcp_unavailable":
                    speech = "Assist conversations need MCP to be disabled in the Codex Bridge App."
                elif error.code in {"authentication_required", "authentication_failed"}:
                    speech = "Sign in to Codex Bridge before using Assist."
                else:
                    speech = "Codex Bridge could not answer just now. Please try again."
                return self._reply(
                    user_input,
                    chat_log,
                    speech,
                    conversation_id if session.thread_id else None,
                )

            if answer["status"] == "completed":
                session.clear_pending()
                speech = (
                    answer["answer"]
                    or "Codex finished without a spoken answer. Open the Codex Bridge chat to review the result."
                )
            elif answer["status"] in _TERMINAL:
                session.clear_pending()
                self._sessions.pop(conversation_id, None)
                speech = "Codex could not complete that answer. Please try again."
                conversation_id = None
            else:
                speech = "Codex is still working. You can review the answer in the Codex Bridge chat."
            if previous_answer:
                speech = f"Previous answer: {previous_answer[:1024]}\n\n{speech}"
            return self._reply(user_input, chat_log, speech, conversation_id)

    async def _submit_pending(self, session: _AssistSession) -> None:
        """Replay an ambiguous request with exactly its original task ID and body."""

        payload = session.pending_payload
        if payload is None:
            return
        if "project_id" in payload:
            reference = await self._runtime.client.async_start_task(payload)
        else:
            reference = await self._runtime.client.async_continue_task(payload)
        if session.thread_id is None:
            session.thread_id = reference["thread_id"]
        elif session.thread_id != reference["thread_id"]:
            raise BridgeApiError("task_target_unavailable")
        session.pending_payload = None

    async def _wait_for_answer(self, task_id: str) -> dict:
        async with asyncio.timeout(_RESPONSE_TIMEOUT_SECONDS):
            while True:
                result = await self._runtime.client.async_get_task_answer(task_id)
                if result["status"] in _TERMINAL:
                    return result
                await asyncio.sleep(_POLL_SECONDS)

    @staticmethod
    def _reply(
        user_input: ConversationInput,
        chat_log: ChatLog,
        speech: str,
        conversation_id: str | None = None,
    ) -> ConversationResult:
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return ConversationResult(
            response=response,
            conversation_id=conversation_id,
            continue_conversation=conversation_id is not None,
        )
