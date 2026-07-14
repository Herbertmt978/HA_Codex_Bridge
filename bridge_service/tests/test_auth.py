import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from codex_bridge_service.auth import require_bridge_token


def _request(
    api_header: str | None = "1", *, runtime_profile: str | None = None
) -> Request:
    headers = []
    if api_header is not None:
        headers.append((b"x-codex-bridge-api", api_header.encode()))
    app = FastAPI()
    if runtime_profile is not None:
        app.state.storage = type("Storage", (), {"runtime_profile": runtime_profile})()
    return Request({"type": "http", "headers": headers, "app": app})


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
        require_bridge_token(
            authorization=authorization,
            expected_token="secret",
            request=_request(),
        )

    assert error.value.status_code == 401
    assert error.value.detail == "unauthorized"


def test_bridge_auth_accepts_an_exact_bearer_token() -> None:
    require_bridge_token(
        authorization="Bearer secret",
        expected_token="secret",
        request=_request(),
    )


@pytest.mark.parametrize("api_header", [None, "0", "2", "01", "bogus"])
def test_bridge_auth_rejects_missing_legacy_future_or_malformed_api_versions(
    api_header: str | None,
) -> None:
    with pytest.raises(HTTPException) as error:
        require_bridge_token(
            authorization="Bearer secret",
            expected_token="secret",
            request=_request(api_header, runtime_profile="home_assistant"),
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "api_incompatible"


def test_bridge_auth_allows_unversioned_explicit_legacy_requests() -> None:
    request = _request(None, runtime_profile="external_legacy")

    require_bridge_token(
        authorization="Bearer secret",
        expected_token="secret",
        request=request,
    )


def test_bridge_auth_allows_v0_only_for_explicit_legacy_requests() -> None:
    require_bridge_token(
        authorization="Bearer secret",
        expected_token="secret",
        request=_request("0", runtime_profile="external_legacy"),
    )


def test_bridge_auth_checks_token_before_api_version() -> None:
    with pytest.raises(HTTPException) as error:
        require_bridge_token(
            authorization="Bearer wrong",
            expected_token="secret",
            request=_request("2"),
        )

    assert error.value.status_code == 401
