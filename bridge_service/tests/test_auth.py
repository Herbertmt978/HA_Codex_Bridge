import pytest
from fastapi import HTTPException

from codex_bridge_service.auth import require_bridge_token


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "bearer secret",
        "Basic secret",
        "Bearer  secret",
        "Bearer wrong",
    ],
)
def test_bridge_auth_rejects_malformed_or_wrong_tokens(
    authorization: str | None,
) -> None:
    with pytest.raises(HTTPException) as error:
        require_bridge_token(authorization=authorization, expected_token="secret")

    assert error.value.status_code == 401
    assert error.value.detail == "unauthorized"


def test_bridge_auth_accepts_an_exact_bearer_token() -> None:
    require_bridge_token(authorization="Bearer secret", expected_token="secret")
