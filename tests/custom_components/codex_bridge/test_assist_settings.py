"""Explicit Assist choices must come from the current provider catalogue."""

from copy import deepcopy

from custom_components.codex_bridge.assist_settings import (
    assist_prompt,
    assist_selection_supported,
    live_assist_models,
)


def _status():
    return {
        "auth": {"state": "ok", "auth_mode": "chatgpt", "auth_required": False},
        "model_catalog": {
            "source": "codex-app-server", "stale": False,
            "models": [
                {"model": "gpt-6-astra", "display_name": "GPT-6 Astra", "catalogued": True, "thinking_levels": ["medium", "high", "max"], "advertised_thinking_levels": ["medium", "high", "max"]},
                {"model": "gpt-6-luna", "display_name": "GPT-6 Luna", "catalogued": True, "thinking_levels": ["medium", "max"], "advertised_thinking_levels": ["medium"]},
            ],
        },
    }


def test_live_assist_choices_validate_each_model_effort_pair():
    choices = live_assist_models(_status())
    assert assist_selection_supported(choices, "gpt-6-astra", "max")
    assert not assist_selection_supported(choices, "gpt-6-luna", "max")
    assert not assist_selection_supported(choices, "unknown", "medium")


def test_recovery_unverified_and_configured_only_models_are_not_choices():
    original = _status()
    for path, value in (
        (("model_catalog", "stale"), True),
        (("model_catalog", "source"), "codex-bundled"),
        (("auth", "auth_required"), True),
    ):
        status = deepcopy(original)
        status[path[0]][path[1]] = value
        assert live_assist_models(status) == {}
    status = deepcopy(original)
    status["model_catalog"]["models"][0]["catalogued"] = False
    assert "gpt-6-astra" not in live_assist_models(status)
    status = deepcopy(original)
    for model in status["model_catalog"]["models"]:
        model.pop("advertised_thinking_levels")
    assert live_assist_models(status) == {}


def test_instructions_are_plain_text_and_empty_preserves_original_prompt():
    assert assist_prompt("Hello", "") == "Hello"
    assert "{{ states('sensor.private') }}" in assist_prompt("Hello", "{{ states('sensor.private') }}")
