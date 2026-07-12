import pytest
from pydantic import ValidationError

from codex_bridge_service.models import RuntimeProfile
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


def test_settings_default_to_external_legacy_without_workspace_root(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)

    settings = Settings()

    assert settings.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY
    assert settings.workspace_root is None


def test_home_assistant_settings_require_workspace_root_without_leaking_input(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    monkeypatch.setenv("CODEX_BRIDGE_RUNTIME_PROFILE", "home_assistant")
    monkeypatch.delenv("CODEX_BRIDGE_WORKSPACE_ROOT", raising=False)

    with pytest.raises(ValidationError):
        Settings()

    private_value = "private-workspace-marker"
    monkeypatch.setenv("CODEX_BRIDGE_WORKSPACE_ROOT", f"   {private_value}   ")
    with pytest.raises(ValidationError) as error:
        Settings()
    assert private_value not in str(error.value)


def test_home_assistant_settings_accept_posix_workspace_root_on_windows_host(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    monkeypatch.setenv("CODEX_BRIDGE_RUNTIME_PROFILE", "home_assistant")
    monkeypatch.setenv("CODEX_BRIDGE_WORKSPACE_ROOT", "/config/workspaces")

    settings = Settings()

    assert settings.runtime_profile is RuntimeProfile.HOME_ASSISTANT
    assert settings.workspace_root == "/config/workspaces"


@pytest.mark.parametrize("workspace_root", ["relative/workspaces", "~/workspaces"])
def test_home_assistant_settings_reject_nonabsolute_workspace_roots_without_leaking_them(
    monkeypatch,
    workspace_root: str,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    monkeypatch.setenv("CODEX_BRIDGE_RUNTIME_PROFILE", "home_assistant")
    monkeypatch.setenv("CODEX_BRIDGE_WORKSPACE_ROOT", workspace_root)

    with pytest.raises(ValidationError) as error:
        Settings()

    assert workspace_root not in str(error.value)


@pytest.mark.parametrize("workspace_root", [None, "", "   "])
def test_settings_require_a_nonblank_home_assistant_workspace_root(
    monkeypatch,
    workspace_root: str | None,
) -> None:
    private_token = "private-token-material-that-must-stay-redacted"
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", private_token)
    monkeypatch.setenv("CODEX_BRIDGE_RUNTIME_PROFILE", "home_assistant")
    if workspace_root is None:
        monkeypatch.delenv("CODEX_BRIDGE_WORKSPACE_ROOT", raising=False)
    else:
        monkeypatch.setenv("CODEX_BRIDGE_WORKSPACE_ROOT", workspace_root)

    with pytest.raises(ValidationError) as error:
        Settings()

    serialized_error = str(error.value)
    assert private_token not in serialized_error
