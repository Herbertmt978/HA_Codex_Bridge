import hmac

from fastapi import Header, HTTPException, status


def require_bridge_token(
    authorization: str | None = Header(default=None),
    *,
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
