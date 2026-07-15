import base64
import json
from math import isfinite
from pathlib import Path
from threading import Lock
import time
from typing import Any, Protocol
from urllib import request

from .account import normalize_chatgpt_plan_type
from .models import LimitsStatusRecord, LimitsWindowRecord

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

_MAX_SIGNED_64 = (1 << 63) - 1
_LONG_RATE_LIMIT_WINDOW_MINUTES = 24 * 60


class _AppServerClient(Protocol):
    @property
    def generation(self) -> int: ...

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any: ...


class AppServerLimitsProbe:
    """Read normalized usage limits from the shared app-server transport."""

    def __init__(
        self,
        client: _AppServerClient,
        *,
        min_fetch_interval_seconds: int = 45,
        timeout_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("limits probe timeout must be positive")
        self._client = client
        self._timeout_seconds = float(timeout_seconds)
        self._min_fetch_interval_seconds = max(0, min_fetch_interval_seconds)
        self._last_fetch_at = 0.0
        self._cached_status: LimitsStatusRecord | None = None
        self._generation: int | None = None
        self._probe_lock = Lock()

    def probe(self) -> LimitsStatusRecord | None:
        with self._probe_lock:
            now = time.monotonic()
            generation = getattr(self._client, "generation", None)
            resolved_generation = generation if type(generation) is int else None
            if self._generation != resolved_generation:
                self._generation = resolved_generation
                self._last_fetch_at = 0.0
                self._cached_status = None
            if (
                self._cached_status is not None
                and now - self._last_fetch_at < self._min_fetch_interval_seconds
            ):
                return self._cached_status.model_copy(deep=True)

            try:
                response = self._client.request(
                    "account/rateLimits/read",
                    None,
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception:
                return (
                    self._cached_status.model_copy(deep=True)
                    if self._cached_status is not None
                    else None
                )
            status = _app_server_limits_status(response)
            if status is None:
                return (
                    self._cached_status.model_copy(deep=True)
                    if self._cached_status is not None
                    else None
                )
            self._last_fetch_at = now
            self._generation = resolved_generation
            self._cached_status = status.model_copy(deep=True)
            return status.model_copy(deep=True)


def _app_server_limits_status(response: object) -> LimitsStatusRecord | None:
    if not isinstance(response, dict):
        return None
    snapshot = response.get("rateLimits")
    if not isinstance(snapshot, dict):
        return None

    primary = _app_server_limits_window(snapshot.get("primary"))
    secondary = _app_server_limits_window(snapshot.get("secondary"))
    primary, secondary = _classify_app_server_limits_windows(primary, secondary)
    reached_type = snapshot.get("rateLimitReachedType")
    blocked = reached_type is not None or any(
        window is not None and window.used_percent == 100.0
        for window in (primary, secondary)
    )
    plan_type = snapshot.get("planType")
    return LimitsStatusRecord(
        available=True,
        blocked=blocked,
        message="Usage limit reached" if blocked else None,
        primary=primary,
        secondary=secondary,
        credits=_app_server_credits(snapshot.get("credits")),
        plan_type=normalize_chatgpt_plan_type(plan_type),
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _classify_app_server_limits_windows(
    primary: LimitsWindowRecord | None,
    secondary: LimitsWindowRecord | None,
) -> tuple[LimitsWindowRecord | None, LimitsWindowRecord | None]:
    """Map protocol positions to the panel's short and long allowance slots."""

    if primary is not None and secondary is not None:
        primary_minutes = primary.window_minutes
        secondary_minutes = secondary.window_minutes
        if (
            primary_minutes is not None
            and secondary_minutes is not None
            and primary_minutes != secondary_minutes
        ):
            return (
                (primary, secondary)
                if primary_minutes < secondary_minutes
                else (secondary, primary)
            )
        return primary, secondary
    window = primary or secondary
    if window is None or window.window_minutes is None:
        return primary, secondary
    if window.window_minutes >= _LONG_RATE_LIMIT_WINDOW_MINUTES:
        return None, window
    return window, None


def _app_server_limits_window(payload: object) -> LimitsWindowRecord | None:
    if not isinstance(payload, dict):
        return None
    used_percent = _safe_percentage(payload.get("usedPercent"))
    return LimitsWindowRecord(
        used_percent=used_percent,
        remaining_percent=(100.0 - used_percent if used_percent is not None else None),
        window_minutes=_safe_nonnegative_integer(payload.get("windowDurationMins")),
        resets_at=_safe_nonnegative_integer(payload.get("resetsAt")),
    )


def _safe_percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not isfinite(result):
        return None
    if not 0.0 <= result <= 100.0:
        result = min(100.0, max(0.0, result))
    return result


def _safe_nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not value.is_integer() or not value == value):
        return None
    result = int(value)
    return result if 0 <= result <= _MAX_SIGNED_64 else None


def _app_server_credits(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    has_credits = value.get("hasCredits")
    unlimited = value.get("unlimited")
    if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
        return None
    balance = value.get("balance")
    if balance is not None and not _is_safe_balance(balance):
        return None
    return {
        "hasCredits": has_credits,
        "unlimited": unlimited,
        "balance": balance,
    }


def _is_safe_balance(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    if value.count(".") > 1:
        return False
    whole, separator, fraction = value.partition(".")
    return whole.isdigit() and (not separator or bool(fraction) and fraction.isdigit())


class CodexLimitsProbe:
    def __init__(self, codex_home: Path | str, *, min_fetch_interval_seconds: int = 45) -> None:
        self.codex_home = Path(codex_home)
        self._cache_key: tuple[str, float] | None = None
        self._cached_status: LimitsStatusRecord | None = None
        self._last_live_fetch_at = 0.0
        self._min_fetch_interval_seconds = min_fetch_interval_seconds
        self._probe_lock = Lock()

    def probe(self) -> LimitsStatusRecord | None:
        with self._probe_lock:
            return self._probe_serialized()

    def _probe_serialized(self) -> LimitsStatusRecord | None:
        live_status = self._probe_live_backend()
        if live_status is not None:
            self._cached_status = live_status
            return live_status

        candidates = self._candidate_paths()
        if not candidates:
            return self._cached_status

        newest = candidates[0]
        cache_key = (str(newest), newest.stat().st_mtime)
        if self._cache_key == cache_key:
            return self._cached_status

        for path in candidates:
            status = self._probe_file(path)
            if status is not None:
                self._cache_key = cache_key
                self._cached_status = status
                return status

        self._cache_key = cache_key
        self._cached_status = None
        return None

    def _probe_live_backend(self) -> LimitsStatusRecord | None:
        now = time.monotonic()
        if self._cached_status is not None and now - self._last_live_fetch_at < self._min_fetch_interval_seconds:
            return self._cached_status

        try:
            auth_path = self.codex_home / "auth.json"
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            tokens = auth.get("tokens") or {}
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                return None
            if self._token_expired(access_token):
                return None

            headers = {
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "codex-bridge",
            }
            account_id = tokens.get("account_id")
            if account_id:
                headers["ChatGPT-Account-Id"] = str(account_id)

            payload = self._fetch_json(
                USAGE_URL,
                headers=headers,
            )
            self._last_live_fetch_at = now
            return self._normalize_backend_snapshot(payload)
        except Exception:
            return None

    def _candidate_paths(self) -> list[Path]:
        patterns = [
            self.codex_home / "sessions",
            self.codex_home / "archived_sessions",
        ]
        paths: list[Path] = []
        for root in patterns:
            if not root.exists():
                continue
            paths.extend(root.glob("**/rollout-*.jsonl"))
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:8]

    def _probe_file(self, path: Path) -> LimitsStatusRecord | None:
        latest_rate_limits: dict[str, Any] | None = None
        latest_timestamp: str | None = None

        try:
            with path.open("r", encoding="utf-8", errors="ignore") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    rate_limits = self._extract_rate_limits(payload)
                    if rate_limits is None:
                        continue
                    latest_rate_limits = rate_limits
                    timestamp = payload.get("timestamp")
                    latest_timestamp = str(timestamp) if timestamp is not None else latest_timestamp
        except OSError:
            return None

        if latest_rate_limits is None:
            return None

        return LimitsStatusRecord(
            available=True,
            blocked=False,
            message=None,
            primary=self._limits_window(
                latest_rate_limits.get("primary_window") or latest_rate_limits.get("primary")
            ),
            secondary=self._limits_window(
                latest_rate_limits.get("secondary_window") or latest_rate_limits.get("secondary")
            ),
            credits=latest_rate_limits.get("credits")
            if isinstance(latest_rate_limits.get("credits"), dict)
            else None,
            plan_type=str(latest_rate_limits.get("plan_type"))
            if latest_rate_limits.get("plan_type") is not None
            else None,
            updated_at=latest_timestamp,
        )

    def _extract_rate_limits(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event_payload = payload.get("payload")
        if (
            payload.get("type") == "event_msg"
            and isinstance(event_payload, dict)
            and event_payload.get("type") == "token_count"
            and isinstance(event_payload.get("rate_limits"), dict)
        ):
            return event_payload["rate_limits"]

        if payload.get("type") == "token_count" and isinstance(payload.get("rate_limits"), dict):
            return payload["rate_limits"]

        return None

    def _fetch_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        method: str = "GET",
        body: bytes | None = None,
    ) -> dict[str, Any]:
        req = request.Request(url, headers=headers, method=method, data=body)
        with request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _token_expired(self, token: str, skew_seconds: int = 120) -> bool:
        parts = token.split(".")
        if len(parts) < 2:
            return False
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
        except Exception:
            return False
        exp = claims.get("exp")
        return isinstance(exp, (int, float)) and exp <= time.time() + skew_seconds

    def _normalize_backend_snapshot(self, payload: dict[str, Any]) -> LimitsStatusRecord:
        rate_limit = payload.get("rate_limit") or payload.get("rateLimits") or {}
        blocked = bool(rate_limit.get("limit_reached")) or bool(payload.get("rate_limit_reached_type"))
        message = None
        if blocked:
            reached_type = payload.get("rate_limit_reached_type")
            message = (
                str(reached_type.get("kind"))
                if isinstance(reached_type, dict) and reached_type.get("kind") is not None
                else str(reached_type or "Usage limit reached")
            )

        return LimitsStatusRecord(
            available=True,
            blocked=blocked,
            message=message,
            primary=self._limits_window(rate_limit.get("primary_window") or rate_limit.get("primary")),
            secondary=self._limits_window(rate_limit.get("secondary_window") or rate_limit.get("secondary")),
            credits=payload.get("credits") if isinstance(payload.get("credits"), dict) else None,
            plan_type=str(payload.get("plan_type")) if payload.get("plan_type") is not None else None,
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def _limits_window(self, payload: object) -> LimitsWindowRecord | None:
        if not isinstance(payload, dict):
            return None

        used = payload.get("used_percent")
        used_percent = float(used) if isinstance(used, (int, float)) else None
        remaining_percent = None
        if used_percent is not None:
            remaining_percent = max(0.0, min(100.0, 100.0 - used_percent))

        window_minutes = payload.get("window_minutes")
        window_seconds = payload.get("limit_window_seconds")
        resets_at = payload.get("resets_at") or payload.get("reset_at")
        return LimitsWindowRecord(
            used_percent=used_percent,
            remaining_percent=remaining_percent,
            window_minutes=(
                int(window_minutes)
                if isinstance(window_minutes, (int, float))
                else int(window_seconds / 60)
                if isinstance(window_seconds, (int, float)) and window_seconds > 0
                else None
            ),
            resets_at=int(resets_at) if isinstance(resets_at, (int, float)) else None,
        )
