import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from codex_bridge_service.models import RuntimeProfile
from codex_bridge_service.resource_limits import MIB, ResourceLimits
from codex_bridge_service.settings import Settings


def test_settings_require_an_explicit_bridge_auth_token(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", raising=False)

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

    monkeypatch.setenv(
        "CODEX_BRIDGE_AUTH_TOKEN", "replace-this-with-a-long-random-token"
    )
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accept_a_long_random_bridge_auth_token(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)

    assert Settings().auth_token == "a" * 43


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CODEX_BRIDGE_MODEL_DISCOVERY_TIMEOUT_SECONDS", "0"),
        ("CODEX_BRIDGE_MODEL_DISCOVERY_TIMEOUT_SECONDS", "nan"),
        ("CODEX_BRIDGE_MODEL_CACHE_TTL_SECONDS", "-1"),
        ("CODEX_BRIDGE_MODEL_CACHE_TTL_SECONDS", "nan"),
    ],
)
def test_settings_reject_invalid_model_catalog_timing(
    monkeypatch, name: str, value: str
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.skipif(os.name == "nt", reason="token files are App/POSIX-only")
def test_settings_load_bridge_auth_token_from_a_private_file(
    monkeypatch, tmp_path: Path
) -> None:
    token = "private-random-bridge-token-" + ("a" * 32)
    token_file = tmp_path / "bridge-token"
    token_file.write_text(token, encoding="ascii")
    if os.name != "nt":
        token_file.chmod(0o600)
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    settings = Settings()

    assert settings.auth_token == token
    assert token not in repr(settings)
    assert str(token_file) not in repr(settings)
    assert "auth_token" not in settings.model_dump()
    assert "auth_token_file" not in settings.model_dump()


@pytest.mark.skipif(os.name == "nt", reason="token files are App/POSIX-only")
def test_settings_reject_windows_shaped_token_paths_on_posix(monkeypatch) -> None:
    token_path = r"C:\private\bridge-token"
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", token_path)

    with pytest.raises(ValidationError) as error:
        Settings()

    assert token_path not in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows must use the environment token")
def test_settings_reject_token_files_on_windows(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "bridge-token"
    token_file.write_text("a" * 43, encoding="ascii")
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    with pytest.raises(ValidationError) as error:
        Settings()

    assert str(token_file) not in str(error.value)


def test_settings_reject_both_token_sources_without_leaking_either(
    monkeypatch, tmp_path: Path
) -> None:
    environment_token = "environment-private-token-" + ("a" * 32)
    file_token = "file-private-token-" + ("b" * 32)
    token_file = tmp_path / "bridge-token"
    token_file.write_text(file_token, encoding="ascii")
    if os.name != "nt":
        token_file.chmod(0o600)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", environment_token)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    with pytest.raises(ValidationError) as error:
        Settings()

    serialized = str(error.value)
    assert environment_token not in serialized
    assert file_token not in serialized
    assert str(token_file) not in serialized


def test_settings_reject_token_files_with_extra_lines(
    monkeypatch, tmp_path: Path
) -> None:
    token = "a" * 43
    token_file = tmp_path / "bridge-token"
    token_file.write_text(f"{token}\n\n", encoding="ascii")
    if os.name != "nt":
        token_file.chmod(0o600)
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    with pytest.raises(ValidationError) as error:
        Settings()

    assert token not in str(error.value)


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX ownership and modes apply in the App"
)
def test_settings_reject_unsafe_token_file_modes(monkeypatch, tmp_path: Path) -> None:
    token_file = tmp_path / "bridge-token"
    token_file.write_text("a" * 43, encoding="ascii")
    token_file.chmod(0o644)
    monkeypatch.delenv("CODEX_BRIDGE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN_FILE", str(token_file))

    with pytest.raises(ValidationError):
        Settings()


def test_settings_default_to_external_legacy_without_workspace_root(
    monkeypatch,
) -> None:
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


def test_settings_expose_the_home_assistant_resource_limit_defaults(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)

    assert Settings().to_resource_limits() == ResourceLimits()


def test_settings_build_immutable_resource_limits_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    monkeypatch.setenv("CODEX_BRIDGE_MAX_QUEUED_PROMPTS", "3")
    monkeypatch.setenv("CODEX_BRIDGE_MAX_UPLOAD_FILE_BYTES", str(12 * MIB))
    monkeypatch.setenv("CODEX_BRIDGE_MINIMUM_FREE_FRACTION", "0.1")

    limits = Settings().to_resource_limits()

    assert limits.max_queued_prompts == 3
    assert limits.max_upload_file_bytes == 12 * MIB
    assert limits.minimum_free_fraction == 0.1


def test_settings_reject_invalid_resource_limits_without_echoing_input(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "a" * 43)
    invalid_value = "-999999999999999999999"
    monkeypatch.setenv("CODEX_BRIDGE_MAX_PRIVATE_BYTES", invalid_value)

    with pytest.raises(ValidationError) as error:
        Settings()

    assert invalid_value not in str(error.value)
