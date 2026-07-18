from __future__ import annotations

import json
import re

import codex_bridge_service.account as account_module
from codex_bridge_service.auth_state import account_status


def account_owner_marker(response: object, secret: str) -> str | None:
    marker = getattr(account_module, "account_owner_marker")
    return marker(response, secret)


def account_unverified_marker(secret: str) -> str:
    marker = getattr(account_module, "account_unverified_marker")
    return marker(secret)


def _chatgpt_account(email: str | None) -> dict[str, object]:
    return {
        "account": {
            "type": "chatgpt",
            "email": email,
            "planType": "pro",
        },
        "requiresOpenaiAuth": True,
    }


def test_account_owner_marker_is_stable_private_and_account_specific() -> None:
    secret = "stable-bridge-secret"
    email = "private-person@example.test"

    first = account_owner_marker(_chatgpt_account(f"  {email.upper()}  "), secret)
    second = account_owner_marker(_chatgpt_account(email), secret)
    different = account_owner_marker(
        _chatgpt_account("another-person@example.test"),
        secret,
    )

    assert first == second
    assert first != different
    assert isinstance(first, str)
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert email not in first
    assert secret not in first


def test_account_owner_marker_requires_a_chatgpt_identity() -> None:
    secret = "stable-bridge-secret"

    assert account_owner_marker({"account": None}, secret) is None
    assert account_owner_marker(_chatgpt_account(None), secret) is None
    assert (
        account_owner_marker(
            {"account": {"type": "apiKey"}},
            secret,
        )
        is None
    )


def test_unverified_account_marker_is_private_stable_and_not_an_owner_marker() -> None:
    secret = "stable-bridge-secret"

    first = account_unverified_marker(secret)
    second = account_unverified_marker(secret)
    owner = account_owner_marker(
        _chatgpt_account("private-person@example.test"),
        secret,
    )

    assert first == second
    assert first != owner
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert secret not in first


def test_public_account_status_never_projects_owner_material() -> None:
    secret = "stable-bridge-secret"
    email = "private-person@example.test"
    response = _chatgpt_account(email)

    marker = account_owner_marker(response, secret)
    projection = json.dumps(account_status(response), sort_keys=True)

    assert email not in projection
    assert secret not in projection
    assert marker not in projection
