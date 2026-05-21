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
            message="Codex sign-in completed on the VM.",
        )
        manager._resolved_auth_error = old_error

    restored = manager.status(last_error=old_error)
    assert restored.state == "ok"
    assert restored.auth_required is False

    new_error = f"{old_error} after a later run"
    expired_again = manager.status(last_error=new_error)
    assert expired_again.state == "expired"
    assert expired_again.auth_required is True
