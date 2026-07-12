import pytest
from pydantic import ValidationError

from codex_bridge_service.settings import Settings


def test_settings_require_an_explicit_bridge_auth_token(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_settings_reject_known_or_short_bridge_auth_tokens(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "change-me")
    with pytest.raises(ValidationError):
        Settings()

    rejected_token = "too-short-sensitive-token"
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", rejected_token)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert rejected_token not in str(error.value)

    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "replace-this-with-a-long-random-token")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accept_a_long_random_bridge_auth_token(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)

    assert Settings().auth_token == "a" * 43
