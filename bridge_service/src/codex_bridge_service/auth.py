from fastapi import Header, HTTPException, status


def require_bridge_token(
    authorization: str | None = Header(default=None),
    *,
    expected_token: str,
) -> None:
    if authorization != f"Bearer {expected_token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
