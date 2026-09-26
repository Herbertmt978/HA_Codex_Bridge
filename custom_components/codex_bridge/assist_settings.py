"""Bounded, plain Assist settings and live provider model choices."""

from collections.abc import Mapping
from dataclasses import dataclass
import re


MAX_ASSIST_INSTRUCTIONS = 4096
_ASSIST_MCP_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
MAX_ASSIST_MCP_SERVERS = 32


@dataclass(frozen=True, slots=True)
class AssistModelChoice:
    label: str
    reasoning: tuple[str, ...]


def _text(value: object, maximum: int) -> str | None:
    if (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and value.isprintable()
    ):
        return value
    return None


def live_assist_models(status: object) -> dict[str, AssistModelChoice]:
    """Accept advertised model/effort pairs only for the current signed-in runtime."""

    if not isinstance(status, Mapping):
        return {}
    auth, catalogue = status.get("auth"), status.get("model_catalog")
    if (
        not isinstance(auth, Mapping)
        or auth.get("state") != "ok"
        or auth.get("auth_mode") != "chatgpt"
        or auth.get("auth_required") is not False
        or not isinstance(catalogue, Mapping)
        or catalogue.get("source") != "codex-app-server"
        or catalogue.get("stale") is not False
        or not isinstance(catalogue.get("models"), list)
    ):
        return {}
    choices = {}
    for item in catalogue["models"][:256]:
        if not isinstance(item, Mapping) or item.get("catalogued") is not True:
            continue
        model = _text(item.get("model"), 160)
        efforts = item.get("advertised_thinking_levels")
        if model is None or not isinstance(efforts, list):
            continue
        reasoning = tuple(dict.fromkeys(
            effort for value in efforts[:16] if (effort := _text(value, 32))
        ))
        if reasoning:
            choices[model] = AssistModelChoice(
                _text(item.get("display_name"), 160) or model, reasoning
            )
    return choices


def assist_selection_supported(
    choices: Mapping[str, AssistModelChoice], model: object, reasoning: object
) -> bool:
    """Never infer support from the catalogue's union of reasoning levels."""

    return (
        isinstance(model, str)
        and isinstance(reasoning, str)
        and model in choices
        and reasoning in choices[model].reasoning
    )


def assist_prompt(question: str, instructions: str) -> str:
    """Use administrator instructions as plain prompt text, without templating."""

    if not instructions:
        return question
    return f"Conversation instructions:\n{instructions}\n\nUser question:\n{question}"


def assist_mcp_server_choices(servers: object) -> dict[str, tuple[str, bool]]:
    """Return bounded labels and enabled state for configured servers."""

    if not isinstance(servers, list):
        return {}
    choices: dict[str, tuple[str, bool]] = {}
    for server in servers[:256]:
        if not isinstance(server, Mapping):
            continue
        name = _text(server.get("name"), 128)
        if name is None or not _ASSIST_MCP_NAME.fullmatch(name):
            continue
        if type(server.get("enabled")) is not bool:
            continue
        title = _text(server.get("title"), 160)
        enabled = server["enabled"]
        choices[name] = (title or name, enabled)
    return choices


def assist_mcp_selection(value: object) -> tuple[str, ...] | None:
    """Validate and canonicalise a bounded list of MCP server names."""

    if not isinstance(value, (list, tuple)) or len(value) > MAX_ASSIST_MCP_SERVERS:
        return None
    if not all(
        isinstance(item, str) and _ASSIST_MCP_NAME.fullmatch(item) for item in value
    ):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(sorted(value))
