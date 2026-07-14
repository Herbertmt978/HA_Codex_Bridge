from types import SimpleNamespace

import pytest

from codex_bridge_service.codex_auth import CodexAuthManager
from codex_bridge_service.models import CodexAuthStatusRecord


def test_auth_output_extracts_device_code_without_ansi_noise() -> None:
    manager = CodexAuthManager()
    output = [
        "Follow these steps to sign in with ChatGPT using device code authorization:",
        "1. Open this link in your browser and sign in to your account",
        "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m",
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m",
        "\x1b[94mEBGG-69ZOF\x1b[0m",
    ]

    manager._update_login_output(output)

    status = manager.status()
    assert status.verification_uri == "https://auth.openai.com/codex/device"
    assert status.user_code == "EBGG-69ZOF"


def test_auth_output_rejects_untrusted_device_url() -> None:
    manager = CodexAuthManager()

    manager._update_login_output(
        ["https://evil.example/codex/device", "ABCD-EFGH"]
    )

    status = manager.status()
    assert status.verification_uri is None
    assert status.login_url is None
    assert "evil.example" not in repr(status)


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.openai.com",
        "https://auth.openai.com/",
        "https://auth.openai.com:8443/codex/device",
        "https://auth.openai.com:not-a-port/codex/device",
        "https://auth.openai.com/codex/device?token=secret",
        "https://auth.openai.com/codex/device#fragment",
        "https://user:secret@auth.openai.com/codex/device",
        "https://auth.openai.com/codex/device\n",
    ],
)
def test_auth_login_url_allowlist_rejects_noncanonical_or_malformed_urls(url: str) -> None:
    manager = CodexAuthManager()

    assert manager._safe_login_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.openai.com/codex/device",
        "https://auth.openai.com:443/codex/device",
        "https://chatgpt.com/codex/device",
        "https://platform.openai.com/codex/device",
    ],
)
def test_auth_login_url_allowlist_accepts_https_default_or_443(url: str) -> None:
    assert CodexAuthManager()._safe_login_url(url) is True


def test_auth_status_ignores_resolved_stale_auth_error() -> None:
    manager = CodexAuthManager()
    old_error = "failed to connect to websocket: HTTP error: 401 Unauthorized"

    expired = manager.status(last_error=old_error)
    assert expired.state == "expired"
    assert expired.auth_required is True

    with manager._lock:
        manager._status = CodexAuthStatusRecord(
            state="ok",
            auth_required=False,
            message="Codex sign-in completed.",
        )
        manager._resolved_auth_error = old_error

    restored = manager.status(last_error=old_error)
    assert restored.state == "ok"
    assert restored.auth_required is False

    new_error = f"{old_error} after a later run"
    expired_again = manager.status(last_error=new_error)
    assert expired_again.state == "expired"
    assert expired_again.auth_required is True


def test_device_login_default_does_not_logout_and_clears_terminal_code(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "codex_bridge_service.codex_auth.subprocess.run",
        lambda command, **kwargs: (
            calls.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    class FakeLoginProcess:
        stdout = iter(())

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr(
        "codex_bridge_service.codex_auth.subprocess.Popen",
        lambda command, **kwargs: (calls.append(command) or FakeLoginProcess()),
    )
    manager = CodexAuthManager()
    manager._update_login_output(
        ["https://auth.openai.com/codex/device", "ABCD-EFGH"]
    )

    status = manager._run_device_login()

    assert status is None
    assert calls == [["codex", "login", "--device-auth"]]
    terminal = manager.status()
    assert terminal.state == "ok"
    assert terminal.verification_uri is None
    assert terminal.login_url is None
    assert terminal.user_code is None
    assert terminal.output_tail == []


def test_device_login_failure_does_not_expose_subprocess_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "codex_bridge_service.codex_auth.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Bearer private-secret https://evil.example/device")
        ),
    )
    manager = CodexAuthManager()

    manager._run_device_login()

    status = manager.status()
    assert status.state == "login_failed"
    assert status.message == "Codex sign-in did not complete."
    assert status.output_tail == []
    assert "private-secret" not in repr(status)


def test_auth_logout_strips_bridge_secrets_and_propagates_codex_home(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")
    monkeypatch.setattr(
        "codex_bridge_service.codex_auth.subprocess.run",
        lambda *args, **kwargs: (
            captured.update(kwargs)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    codex_home = tmp_path / "codex-home"

    status = CodexAuthManager(codex_home=codex_home).logout()

    assert status.state == "logged_out"
    assert "CODEX_BRIDGE_AUTH_TOKEN" not in captured["env"]
    assert captured["env"]["CODEX_HOME"] == str(codex_home)


def test_device_login_subprocesses_use_sanitized_codex_environment(
    tmp_path,
    monkeypatch,
) -> None:
    environments = []
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")
    monkeypatch.setattr(
        "codex_bridge_service.codex_auth.subprocess.run",
        lambda *args, **kwargs: (
            environments.append(kwargs["env"])
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    class FakeLoginProcess:
        stdout = iter(())

        def poll(self):
            return None

        def wait(self):
            return 0

    def fake_popen(*args, **kwargs):
        environments.append(kwargs["env"])
        return FakeLoginProcess()

    monkeypatch.setattr("codex_bridge_service.codex_auth.subprocess.Popen", fake_popen)
    codex_home = tmp_path / "codex-home"
    manager = CodexAuthManager(codex_home=codex_home)

    manager._run_device_login(force_logout=True)

    assert manager.status().state == "ok"
    assert len(environments) == 2
    assert all("CODEX_BRIDGE_AUTH_TOKEN" not in environment for environment in environments)
    assert all(environment["CODEX_HOME"] == str(codex_home) for environment in environments)
