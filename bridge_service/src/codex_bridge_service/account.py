import base64
import json
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from .models import CodexAccountRecord

AUTH_CLAIMS_KEY = "https://api.openai.com/auth"
PROFILE_CLAIMS_KEY = "https://api.openai.com/profile"

SAFE_CHATGPT_PLAN_TYPES = frozenset(
    {
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_usage_based",
        "business",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "unknown",
    }
)


def normalize_chatgpt_plan_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SAFE_CHATGPT_PLAN_TYPES else None


class _AppServerClient(Protocol):
    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any: ...


class AppServerAccountProbe:
    """Read a privacy-preserving account projection from the shared app-server."""

    def __init__(
        self,
        client: _AppServerClient,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("account probe timeout must be positive")
        self._client = client
        self._timeout_seconds = float(timeout_seconds)
        self._probe_lock = Lock()

    def probe(self) -> CodexAccountRecord:
        with self._probe_lock:
            try:
                response = self._client.request(
                    "account/read",
                    {"refreshToken": False},
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception:
                return CodexAccountRecord()

        if not isinstance(response, dict):
            return CodexAccountRecord()
        account = response.get("account")
        if account is None:
            return CodexAccountRecord()
        if not isinstance(account, dict):
            return CodexAccountRecord()

        account_type = account.get("type")
        if account_type == "apiKey":
            return CodexAccountRecord(auth_mode="apikey")
        if account_type != "chatgpt":
            return CodexAccountRecord(
                auth_mode="unsupported" if isinstance(account_type, str) else None
            )

        plan_type = normalize_chatgpt_plan_type(account.get("planType"))
        if plan_type is None:
            return CodexAccountRecord(auth_mode="unsupported")
        return CodexAccountRecord(
            available=True,
            auth_mode="chatgpt",
            plan_type=plan_type,
        )


class CodexAccountProbe:
    def __init__(self, codex_home: Path | str) -> None:
        self.codex_home = Path(codex_home)
        self._cache_key: tuple[str, float] | None = None
        self._cached_account: CodexAccountRecord | None = None

    def probe(self) -> CodexAccountRecord:
        auth_path = self.codex_home / "auth.json"
        if not auth_path.exists():
            return CodexAccountRecord()

        try:
            cache_key = (str(auth_path), auth_path.stat().st_mtime)
            if self._cache_key == cache_key and self._cached_account is not None:
                return self._cached_account

            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            account = self._account_from_auth(auth)
            self._cache_key = cache_key
            self._cached_account = account
            return account
        except Exception:
            return CodexAccountRecord()

    def _account_from_auth(self, auth: dict[str, Any]) -> CodexAccountRecord:
        tokens_value = auth.get("tokens")
        tokens: dict[str, Any] = tokens_value if isinstance(tokens_value, dict) else {}
        id_claims = self._decode_claims(str(tokens.get("id_token") or ""))
        access_claims = self._decode_claims(str(tokens.get("access_token") or ""))

        auth_claims = self._first_dict(
            id_claims.get(AUTH_CLAIMS_KEY),
            access_claims.get(AUTH_CLAIMS_KEY),
        )
        profile_claims = self._first_dict(
            access_claims.get(PROFILE_CLAIMS_KEY),
            id_claims.get(PROFILE_CLAIMS_KEY),
        )
        organization = self._default_organization(auth_claims.get("organizations"))

        email = self._first_str(
            profile_claims.get("email"),
            id_claims.get("email"),
            access_claims.get("email"),
        )
        name = self._first_str(id_claims.get("name"), profile_claims.get("name"), access_claims.get("name"))
        account_id = self._first_str(auth_claims.get("chatgpt_account_id"), tokens.get("account_id"))
        user_id = self._first_str(auth_claims.get("chatgpt_user_id"), auth_claims.get("user_id"))
        plan_type = self._first_str(auth_claims.get("chatgpt_plan_type"))
        organization_id = self._first_str(organization.get("id"))
        organization_title = self._first_str(organization.get("title"))
        auth_mode = self._first_str(auth.get("auth_mode"))
        updated_at = self._first_str(auth.get("last_refresh"))

        return CodexAccountRecord(
            available=bool(email or name or account_id or user_id),
            auth_mode=auth_mode,
            email=email,
            name=name,
            account_id=account_id,
            user_id=user_id,
            plan_type=plan_type,
            organization_id=organization_id,
            organization_title=organization_title,
            updated_at=updated_at,
        )

    def _decode_claims(self, token: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _first_dict(self, *values: object) -> dict[str, Any]:
        for value in values:
            if isinstance(value, dict):
                return value
        return {}

    def _first_str(self, *values: object) -> str | None:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _default_organization(self, organizations: object) -> dict[str, Any]:
        if not isinstance(organizations, list):
            return {}
        for organization in organizations:
            if isinstance(organization, dict) and organization.get("is_default"):
                return organization
        for organization in organizations:
            if isinstance(organization, dict):
                return organization
        return {}
