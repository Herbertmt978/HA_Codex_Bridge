from __future__ import annotations

import pytest

from codex_bridge_service.mcp_elicitation import parse_elicitation, validate_form_content


def _form(properties: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    return {
        "serverName": "test_server", "threadId": "thread-1", "turnId": "turn-1",
        "mode": "form", "message": "Choose a result",
        "requestedSchema": {
            "type": "object", "properties": properties, "required": required or [],
        },
    }


def test_supported_form_fields_are_typed_and_answered_exactly() -> None:
    parsed = parse_elicitation(_form({
        "choice": {"type": "string", "enum": ["yes", "no"]},
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "enabled": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
    }, ["choice", "count", "enabled"]))
    assert parsed is not None
    fields = parsed.display.mcp_fields
    assert [field.kind for field in fields] == ["select", "integer", "boolean", "multi_select"]
    assert validate_form_content(
        {"choice": "yes", "count": 2, "enabled": False, "tags": ["a"]}, fields,
    ) == {"choice": "yes", "count": 2, "enabled": False, "tags": ["a"]}
    for invalid in (
        {"choice": "other", "count": 2, "enabled": False},
        {"choice": "yes", "count": 2.5, "enabled": False},
        {"choice": "yes", "count": 4, "enabled": False},
        {"choice": "yes", "count": 2},
        {"choice": "yes", "count": 2, "enabled": False, "unexpected": "x"},
    ):
        with pytest.raises(ValueError):
            validate_form_content(invalid, fields)


@pytest.mark.parametrize("properties", [
    {"password": {"type": "string"}},
    {"token": {"type": "string"}},
    {"pin": {"type": "string"}},
    {"otp": {"type": "string"}},
    {"verification_code": {"type": "string"}},
    {"accessCode": {"type": "string"}},
    {"oneTimeCode": {"type": "string"}},
    {"field": {"type": "object"}},
    {"field": {"type": "string", "format": "unsupported"}},
    {"field": {"type": "array", "items": {"type": "number"}}},
])
def test_unsupported_or_credential_forms_decline(properties: dict[str, object]) -> None:
    assert parse_elicitation(_form(properties)) is None


def test_credential_request_in_form_message_is_declined() -> None:
    params = _form({"value": {"type": "string"}})
    params["message"] = "Enter your API key here"
    assert parse_elicitation(params) is None


def test_url_requires_validated_destination_and_does_not_put_token_in_display() -> None:
    params = {
        "serverName": "test_server", "threadId": "thread-1", "turnId": "turn-1",
        "mode": "url", "message": "Authorise access", "elicitationId": "e-1",
        "url": "https://login.example.com/authorise?one_time=private-value",
    }
    spec = parse_elicitation(params, url_validator=lambda value: value)
    assert spec is not None
    assert spec.authorization_url == params["url"]
    assert spec.display.mcp_url_host == "login.example.com"
    assert "private-value" not in spec.display.model_dump_json()
    params["message"] = f"Open {params['url']} to continue"
    redacted = parse_elicitation(params, url_validator=lambda value: value)
    assert redacted is not None
    assert "private-value" not in redacted.display.model_dump_json()
    params["message"] = f"Open {params['url'].replace('https://', 'HTTPS://')} to continue"
    uppercase = parse_elicitation(params, url_validator=lambda value: value)
    assert uppercase is not None
    assert "private-value" not in uppercase.display.model_dump_json()
    params["message"] = "Use code private-value to continue"
    repeated = parse_elicitation(params, url_validator=lambda value: value)
    assert repeated is not None
    assert "private-value" not in repeated.display.model_dump_json()
    assert parse_elicitation(params, url_validator=lambda _value: (_ for _ in ()).throw(ValueError())) is None


def test_nonstandard_openai_form_is_declined() -> None:
    params = _form({"field": {"type": "string"}})
    params["mode"] = "openai/form"
    assert parse_elicitation(params) is None


def test_date_time_answers_require_rfc3339_timezone() -> None:
    spec = parse_elicitation(_form({
        "when": {"type": "string", "format": "date-time"},
    }, ["when"]))
    assert spec is not None
    fields = spec.display.mcp_fields
    assert validate_form_content({"when": "2026-09-23T20:30:00.000Z"}, fields)
    for invalid in ("2026-09-23", "2026-09-23T20:30", "2026-09-23T20:30:00"):
        with pytest.raises(ValueError):
            validate_form_content({"when": invalid}, fields)
