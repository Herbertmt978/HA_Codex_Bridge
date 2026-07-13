from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .account import normalize_chatgpt_plan_type
from .models import CodexAuthStatusRecord

MESSAGE_UNKNOWN = "Codex authentication has not been checked yet."
MESSAGE_CHECKING = "Checking the ChatGPT sign-in state."
MESSAGE_READY = "Codex is signed in with ChatGPT."
MESSAGE_SIGNED_OUT = "Sign in with ChatGPT to use Codex."
MESSAGE_LOGIN_STARTING = "Starting ChatGPT device sign-in."
MESSAGE_LOGIN_RUNNING = "Open the secure sign-in page and enter the device code."
MESSAGE_LOGIN_CANCELING = "Canceling ChatGPT device sign-in."
MESSAGE_LOGIN_COMPLETING = "Finishing ChatGPT device sign-in."
MESSAGE_LOGIN_FAILED = (
    "ChatGPT device sign-in did not complete. Enable device authorization and try again."
)
MESSAGE_LOGOUT_RUNNING = "Signing out of ChatGPT."
MESSAGE_LOGOUT_FAILED = "ChatGPT sign-out did not complete. Try again."
MESSAGE_UNSUPPORTED = (
    "This Codex authentication mode is unsupported. Sign out, then sign in with ChatGPT."
)
MESSAGE_UNAVAILABLE = "Codex authentication is temporarily unavailable. Try again."
MESSAGE_CLOSED = "Codex authentication has stopped."

_ALLOWED_VERIFICATION_HOSTS = frozenset(
    {
        "auth.openai.com",
        "chatgpt.com",
        "platform.openai.com",
    }
)
_UNSUPPORTED_AUTH_MODES = {
    "apikey": "apikey",
    "apiKey": "apikey",
    "personalAccessToken": "personalAccessToken",
    "chatgptAuthTokens": "chatgptAuthTokens",
    "agentIdentity": "agentIdentity",
}
_MAX_LOGIN_ID_LENGTH = 256
_MAX_DEVICE_CODE_LENGTH = 64
_DEVICE_CODE_PATTERN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")


def account_status(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise TypeError("invalid account response")
    account = response.get("account")
    if account is None:
        return public_status(
            state="logged_out",
            auth_required=True,
            message=MESSAGE_SIGNED_OUT,
        )
    if not isinstance(account, dict):
        raise TypeError("invalid account response")
    account_type = account.get("type")
    if account_type == "chatgpt":
        return public_status(
            state="ok",
            auth_required=False,
            auth_mode="chatgpt",
            plan_type=normalize_chatgpt_plan_type(account.get("planType")),
            message=MESSAGE_READY,
        )
    return public_status(
        state="unsupported",
        auth_required=True,
        auth_mode=_safe_unsupported_mode(account_type),
        message=MESSAGE_UNSUPPORTED,
    )


def updated_account_status(
    params: dict[str, Any],
    current: CodexAuthStatusRecord,
) -> dict[str, Any]:
    if "authMode" not in params:
        plan_type = current.plan_type
        if "planType" in params and current.auth_mode == "chatgpt":
            plan_type = normalize_chatgpt_plan_type(params.get("planType"))
        return public_status(
            state=current.state,
            auth_required=current.auth_required,
            auth_mode=current.auth_mode,
            plan_type=plan_type,
            message=current.message or MESSAGE_UNKNOWN,
        )
    auth_mode = params["authMode"]
    if auth_mode is None:
        return public_status(
            state="logged_out",
            auth_required=True,
            message=MESSAGE_SIGNED_OUT,
        )
    if auth_mode == "chatgpt":
        return public_status(
            state="ok",
            auth_required=False,
            auth_mode="chatgpt",
            plan_type=normalize_chatgpt_plan_type(params.get("planType")),
            message=MESSAGE_READY,
        )
    return public_status(
        state="unsupported",
        auth_required=True,
        auth_mode=_safe_unsupported_mode(auth_mode),
        message=MESSAGE_UNSUPPORTED,
    )


def public_status(
    *,
    state: str,
    auth_required: bool,
    message: str,
    auth_mode: str | None = None,
    plan_type: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "busy": False,
        "auth_required": auth_required,
        "auth_mode": auth_mode,
        "plan_type": plan_type,
        "message": message,
        **cleared_device_fields(),
    }


def cleared_device_fields() -> dict[str, Any]:
    return {
        "verification_uri": None,
        "login_url": None,
        "user_code": None,
        "output_tail": [],
    }


def parse_device_login(response: Any) -> tuple[str, str, str]:
    if not isinstance(response, dict) or response.get("type") != "chatgptDeviceCode":
        raise ValueError("invalid device login response")
    login_id = response.get("loginId")
    user_code = response.get("userCode")
    verification_url = response.get("verificationUrl")
    if (
        not isinstance(login_id, str)
        or not login_id
        or len(login_id) > _MAX_LOGIN_ID_LENGTH
    ):
        raise ValueError("invalid login correlation")
    if not _safe_device_code(user_code):
        raise ValueError("invalid device code")
    if not _safe_verification_url(verification_url):
        raise ValueError("invalid verification URL")
    assert isinstance(user_code, str)
    assert isinstance(verification_url, str)
    return login_id, user_code, verification_url


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_device_code(value: Any) -> bool:
    if not isinstance(value, str) or not 4 <= len(value) <= _MAX_DEVICE_CODE_LENGTH:
        return False
    return _DEVICE_CODE_PATTERN.fullmatch(value) is not None


def _safe_verification_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 512:
        return False
    if value != value.strip() or any(
        ord(character) <= 0x20 or ord(character) == 0x7F for character in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_VERIFICATION_HOSTS
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and bool(parsed.path)
        and not parsed.query
        and not parsed.fragment
    )


def _safe_unsupported_mode(value: Any) -> str:
    if isinstance(value, str):
        return _UNSUPPORTED_AUTH_MODES.get(value, "unsupported")
    return "unsupported"
