import base64
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any
from urllib import request

from .models import LimitsStatusRecord, LimitsWindowRecord

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


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
