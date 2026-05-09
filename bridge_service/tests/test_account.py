import base64
import json

from codex_bridge_service.account import CodexAccountProbe


def _jwt(payload: dict[str, object]) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


def test_account_probe_exposes_safe_profile_fields_from_codex_auth(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    id_token = _jwt(
        {
            "email": "person@example.com",
            "name": "Person Example",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acc_123",
                "chatgpt_user_id": "user_123",
                "chatgpt_plan_type": "pro",
                "organizations": [
                    {
                        "id": "org_123",
                        "title": "Personal",
                        "is_default": True,
                    }
                ],
            },
        }
    )
    access_token = _jwt(
        {
            "https://api.openai.com/profile": {
                "email": "person@example.com",
                "email_verified": True,
            }
        }
    )
    (codex_home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": id_token,
                    "access_token": access_token,
                    "refresh_token": "secret_refresh_token",
                    "account_id": "acc_fallback",
                },
                "last_refresh": "2026-05-09T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    account = CodexAccountProbe(codex_home).probe()

    assert account.available is True
    assert account.auth_mode == "chatgpt"
    assert account.email == "person@example.com"
    assert account.name == "Person Example"
    assert account.account_id == "acc_123"
    assert account.user_id == "user_123"
    assert account.plan_type == "pro"
    assert account.organization_title == "Personal"


def test_account_probe_returns_unavailable_when_auth_is_missing(tmp_path) -> None:
    account = CodexAccountProbe(tmp_path / ".codex").probe()

    assert account.available is False
    assert account.email is None
