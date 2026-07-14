import hmac

from fastapi import Header, HTTPException, Request, status

from .api_contract import API_CURRENT, API_MAXIMUM, API_MINIMUM

API_HEADER = "X-Codex-Bridge-Api"


def _raise_api_incompatible(
    client_minimum: int | None = None,
    client_maximum: int | None = None,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "api_incompatible",
            "status": status.HTTP_409_CONFLICT,
            "retryable": False,
            "message": "The client and server API ranges are incompatible.",
            "client_minimum": client_minimum,
            "client_maximum": client_maximum,
            "server_minimum": API_MINIMUM,
            "server_maximum": API_MAXIMUM,
        },
    )


def _require_api_version(
    request: Request,
) -> None:
    raw_version = request.headers.get(API_HEADER)
    storage = getattr(request.app.state, "storage", None)
    runtime_profile = getattr(storage, "runtime_profile", None)
    is_external_legacy = str(runtime_profile) == "external_legacy"
    if raw_version is None and is_external_legacy:
        return
    if raw_version is None:
        _raise_api_incompatible()
    if is_external_legacy and raw_version == "0":
        return
    if raw_version != str(API_CURRENT):
        client_version = None
        if (
            raw_version
            and len(raw_version) <= 9
            and raw_version.isascii()
            and raw_version.isdecimal()
        ):
            client_version = int(raw_version)
        _raise_api_incompatible(client_version, client_version)


def require_bridge_token(
    authorization: str | None = Header(default=None),
    *,
    request: Request,
    expected_token: str,
) -> None:
    scheme, separator, provided_token = (authorization or "").partition(" ")
    try:
        authorized = (
            scheme == "Bearer"
            and separator == " "
            and hmac.compare_digest(provided_token, expected_token)
        )
    except TypeError:
        authorized = False
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    _require_api_version(request)
