"""Durable, HA-triggered automation definitions and execution claims.

The Bridge deliberately owns definitions and idempotent run admission, but not a
wall-clock worker.  Home Assistant is expected to ask for the next UTC due time
and submit conditional claims from its own scheduler.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from math import ceil, floor
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Runtime dependency note: python-dateutil is required by this module for RFC
# 5545 evaluation.  It is present in the current test environment but must be
# added to the App/Bridge runtime dependency manifest by the wiring owner.
from dateutil.rrule import rrulestr


class AutomationError(RuntimeError):
    code = "automation_error"


class AutomationNotFoundError(AutomationError):
    code = "automation_not_found"


class AutomationConflictError(AutomationError):
    code = "automation_conflict"


class ScheduleValidationError(AutomationError, ValueError):
    code = "automation_invalid_schedule"


class AutomationValidationError(AutomationError, ValueError):
    code = "automation_invalid"


class AutomationDispatcher(Protocol):
    """Optional root-owned dispatch boundary for accepted automation claims."""

    def __call__(self, claim: Mapping[str, Any]) -> object: ...


_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "interrupted_restart",
}
_SKIPPED_STATUSES = {
    "skipped_overlap",
    "skipped_capacity",
    "skipped_misfire",
    "skipped_paused",
}
_RUN_STATUSES = _ACTIVE_STATUSES | _TERMINAL_STATUSES | _SKIPPED_STATUSES
_MODES = {"observe", "edit", "full-auto"}


def _is_pending_runtime_link(run: Mapping[str, Any]) -> bool:
    return (
        run["status"] in _TERMINAL_STATUSES
        and run["bridge_run_id"] is not None
        and run["started_at"] is None
    )


def _terminal_retention_key(run: Mapping[str, Any]) -> tuple[str, str]:
    return (
        run["completed_at"] or run["created_at"],
        run["automation_run_id"],
    )


class AutomationStore:
    """Small atomic JSON store with bounded run records and stable claims."""

    def __init__(
        self,
        state_root: Path | str,
        *,
        target_validator: Callable[[Mapping[str, Any]], None] | None = None,
        max_runs_per_automation: int = 200,
        max_total_runs: int = 5_000,
        misfire_grace_seconds: int = 300,
    ) -> None:
        if max_runs_per_automation < 1 or max_total_runs < max_runs_per_automation:
            raise ValueError("automation history limits are invalid")
        if misfire_grace_seconds < 0:
            raise ValueError("misfire grace must not be negative")
        self.root = Path(state_root)
        self.path = self.root / "automations.json"
        self._target_validator = target_validator
        self._max_runs_per_automation = max_runs_per_automation
        self._max_total_runs = max_total_runs
        self._misfire_grace = timedelta(seconds=misfire_grace_seconds)
        self._lock = RLock()
        self._state = self._load()
        self._recover_runs_after_restart()

    def _recover_runs_after_restart(self) -> None:
        """Settle claims left between durable dispatch writes by a restart.

        Prompt submission and automation linkage are separate durable writes.
        A process crash between them leaves an unlinked ``queued`` claim, so it
        must not reserve the automation forever. Runtime recovery independently
        stops interrupted prompts; this record preserves the safe outcome.

        A fast runtime can also finish before dispatch records its start. No
        delayed in-process linkage can survive a restart, so treat that durable
        terminal record as reconciled before applying normal history bounds.
        """

        changed = False
        protected_run_ids: set[str] = set()
        now = _iso(_now(None))
        with self._lock:
            for run in self._state["runs"].values():
                if _is_pending_runtime_link(run):
                    run["started_at"] = run["completed_at"] or now
                    protected_run_ids.add(run["automation_run_id"])
                    changed = True
                    continue
                if run["status"] != "queued" or run["bridge_run_id"] is not None:
                    continue
                run["status"] = "interrupted_restart"
                run["dispatchable"] = False
                run["completed_at"] = now
                run["error"] = "automation dispatch interrupted by bridge restart"
                protected_run_ids.add(run["automation_run_id"])
                automation = self._state["automations"].get(run["automation_id"])
                if automation is not None:
                    automation["last_run_at"] = now
                    automation["last_status"] = "interrupted_restart"
                    automation["updated_at"] = now
                changed = True
            if changed:
                self._prune_runs(protected_run_ids=protected_run_ids)
                self._save()

    def create(
        self, payload: Mapping[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]:
        now = _now(now)
        with self._lock:
            record = self._new_record(payload, now)
            self._state["automations"][record["automation_id"]] = record
            self._save()
            return _public_automation(record, include_prompt=True)

    def get(self, automation_id: str) -> dict[str, Any]:
        with self._lock:
            return _public_automation(
                self._automation(automation_id), include_prompt=True
            )

    def list(self, *, include_paused: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            values = self._state["automations"].values()
            if not include_paused:
                values = (value for value in values if value["enabled"])
            return [
                _public_automation(value, include_prompt=False)
                for value in sorted(
                    values,
                    key=lambda item: (item["name"].lower(), item["automation_id"]),
                )
            ]

    def update(
        self,
        automation_id: str,
        changes: Mapping[str, Any],
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        with self._lock:
            record = self._automation(automation_id)
            self._require_revision(record, expected_revision)
            allowed = {
                "name",
                "prompt",
                "target",
                "mode",
                "model",
                "thinking",
                "schedule",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise AutomationValidationError("unsupported automation fields")
            candidate = {
                **record,
                **{key: value for key, value in changes.items() if key in allowed},
            }
            normalized = self._new_record(
                candidate,
                now,
                automation_id=automation_id,
                revision=record["revision"] + 1,
                created_at=record["created_at"],
                enabled=record["enabled"],
            )
            self._state["automations"][automation_id] = normalized
            self._save()
            return _public_automation(normalized, include_prompt=True)

    def pause(
        self, automation_id: str, *, expected_revision: int, now: datetime | None = None
    ) -> dict[str, Any]:
        return self._set_enabled(
            automation_id, False, expected_revision=expected_revision, now=now
        )

    def resume(
        self, automation_id: str, *, expected_revision: int, now: datetime | None = None
    ) -> dict[str, Any]:
        return self._set_enabled(
            automation_id, True, expected_revision=expected_revision, now=now
        )

    def delete(self, automation_id: str, *, expected_revision: int) -> None:
        with self._lock:
            record = self._automation(automation_id)
            self._require_revision(record, expected_revision)
            if record["enabled"]:
                raise AutomationConflictError(
                    "automation must be paused before deletion"
                )
            if any(
                run["automation_id"] == automation_id
                and run["status"] in _ACTIVE_STATUSES
                for run in self._state["runs"].values()
            ):
                raise AutomationConflictError("automation has an active run")
            del self._state["automations"][automation_id]
            for run_id, run in list(self._state["runs"].items()):
                if run["automation_id"] == automation_id:
                    self._state["runs"].pop(run_id)
            self._rebuild_idempotency()
            self._save()

    def claim(
        self,
        automation_id: str,
        *,
        due_at: str,
        idempotency_key: str,
        expected_revision: int,
        capacity_available: bool = True,
        web_search: Literal["live", "disabled"] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        due = _parse_datetime(due_at, "due_at")
        return self._claim(
            automation_id,
            source="scheduled",
            due_at=due,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            capacity_available=capacity_available,
            web_search=web_search,
            now=now,
        )

    def run_now(
        self,
        automation_id: str,
        *,
        capacity_available: bool = True,
        web_search: Literal["live", "disabled"] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        with self._lock:
            record = self._automation(automation_id)
            return self._claim(
                automation_id,
                source="manual",
                due_at=now,
                idempotency_key=f"manual:{automation_id}:{uuid4().hex}",
                expected_revision=record["revision"],
                capacity_available=capacity_available,
                web_search=web_search,
                now=now,
            )

    def complete(
        self,
        automation_run_id: str,
        *,
        status: str,
        error: object | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        if status not in _TERMINAL_STATUSES:
            raise AutomationValidationError("automation run status is invalid")
        with self._lock:
            run = self._run(automation_run_id)
            if run["status"] not in _ACTIVE_STATUSES:
                raise AutomationConflictError("automation run is already terminal")
            run["status"] = status
            run["dispatchable"] = False
            run["completed_at"] = _iso(now)
            if error is not None:
                run["error"] = _safe_error(error)
            automation = self._automation(run["automation_id"])
            automation["last_run_at"] = run["completed_at"]
            automation["last_status"] = status
            automation["updated_at"] = _iso(now)
            self._prune_runs(protected_run_ids={automation_run_id})
            self._save()
            return _public_run(run)

    def complete_by_bridge_run(
        self,
        bridge_run_id: str,
        *,
        status: str,
        error: object | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Complete the durable automation record linked to a Bridge run ID."""
        bridge_run_id = _identifier(bridge_run_id, "bridge run id")
        with self._lock:
            matching = [
                run["automation_run_id"]
                for run in self._state["runs"].values()
                if run["bridge_run_id"] == bridge_run_id
            ]
            if len(matching) != 1:
                raise AutomationNotFoundError("automation run was not found")
            return self.complete(matching[0], status=status, error=error, now=now)

    def complete_runtime_run(
        self,
        bridge_run_id: str,
        *,
        client_request_id: str,
        unattended: bool,
        status: str,
        error: object | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Apply a terminal runtime outcome even if dispatch has not linked yet."""

        if status not in _TERMINAL_STATUSES:
            raise AutomationValidationError("automation run status is invalid")
        if unattended is not True or not client_request_id.startswith("automation:"):
            raise AutomationNotFoundError("automation run was not found")
        automation_run_id = client_request_id.removeprefix("automation:")
        if client_request_id != f"automation:{automation_run_id}":
            raise AutomationNotFoundError("automation run was not found")
        normalized_bridge_run_id = _identifier(bridge_run_id, "bridge run id")
        with self._lock:
            try:
                run = self._run(automation_run_id)
            except AutomationError:
                raise AutomationNotFoundError("automation run was not found") from None
            existing_bridge_run_id = run["bridge_run_id"]
            if existing_bridge_run_id not in {None, normalized_bridge_run_id}:
                raise AutomationConflictError("automation runtime link conflict")
            if run["status"] not in _ACTIVE_STATUSES:
                if (
                    run["status"] == status
                    and existing_bridge_run_id == normalized_bridge_run_id
                ):
                    return _public_run(run)
                raise AutomationConflictError("automation run is already terminal")
            run["bridge_run_id"] = normalized_bridge_run_id
            return self.complete(
                automation_run_id,
                status=status,
                error=error,
                now=now,
            )

    def mark_running(
        self,
        automation_run_id: str,
        *,
        bridge_run_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now(now)
        with self._lock:
            run = self._run(automation_run_id)
            normalized_bridge_run_id = (
                _identifier(bridge_run_id, "bridge run id")
                if bridge_run_id is not None
                else None
            )
            if run["status"] != "queued":
                if (
                    run["status"] in _TERMINAL_STATUSES
                    and normalized_bridge_run_id is not None
                    and run["bridge_run_id"] == normalized_bridge_run_id
                ):
                    if _is_pending_runtime_link(run):
                        run["started_at"] = _iso(now)
                        self._prune_runs(protected_run_ids={automation_run_id})
                        self._save()
                    return _public_run(run)
                raise AutomationConflictError("automation run is not queued")
            run["status"] = "running"
            run["dispatchable"] = False
            run["started_at"] = _iso(now)
            if normalized_bridge_run_id is not None:
                run["bridge_run_id"] = normalized_bridge_run_id
            self._save()
            return _public_run(run)

    def list_runs(
        self, automation_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise AutomationValidationError("automation run limit is invalid")
        with self._lock:
            self._automation(automation_id)
            values = [
                run
                for run in self._state["runs"].values()
                if run["automation_id"] == automation_id
            ]
            values.sort(
                key=lambda item: (item["created_at"], item["automation_run_id"]),
                reverse=True,
            )
            return [_public_run(run) for run in values[:limit]]

    def scheduler_snapshot(
        self, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        now = _now(now)
        with self._lock:
            changed = False
            result = []
            for record in self._state["automations"].values():
                if not record["enabled"]:
                    continue
                # Keep an already persisted occurrence visible until HA has a
                # chance to claim it.  Recomputing from ``now`` here would
                # silently drop a once occurrence (or jump an interval/rrule
                # past several missed occurrences) after a restart.
                stored_next_run_at = record.get("next_run_at")
                next_run_at = stored_next_run_at or _next_run(record["schedule"], now)
                if record["next_run_at"] != next_run_at:
                    record["next_run_at"] = next_run_at
                    record["updated_at"] = _iso(now)
                    changed = True
                if next_run_at is not None:
                    result.append(
                        {
                            "automation_id": record["automation_id"],
                            "revision": record["revision"],
                            "next_run_at": next_run_at,
                        }
                    )
            if changed:
                self._save()
            return sorted(
                result, key=lambda item: (item["next_run_at"], item["automation_id"])
            )

    def _set_enabled(
        self,
        automation_id: str,
        enabled: bool,
        *,
        expected_revision: int,
        now: datetime | None,
    ) -> dict[str, Any]:
        now = _now(now)
        with self._lock:
            record = self._automation(automation_id)
            self._require_revision(record, expected_revision)
            if record["enabled"] == enabled:
                return _public_automation(record, include_prompt=True)
            record["enabled"] = enabled
            record["revision"] += 1
            record["updated_at"] = _iso(now)
            record["next_run_at"] = (
                _next_run(record["schedule"], now) if enabled else None
            )
            self._save()
            return _public_automation(record, include_prompt=True)

    def _claim(
        self,
        automation_id: str,
        *,
        source: Literal["scheduled", "manual"],
        due_at: datetime,
        idempotency_key: str,
        expected_revision: int,
        capacity_available: bool,
        web_search: Literal["live", "disabled"] | None,
        now: datetime,
    ) -> dict[str, Any]:
        with self._lock:
            web_search = _normalize_web_search(web_search)
            existing_id = self._state["idempotency"].get(
                _identifier(idempotency_key, "idempotency key")
            )
            if existing_id is not None:
                return _public_run(self._run(existing_id))
            record = self._automation(automation_id)
            self._require_revision(record, expected_revision)
            if not record["enabled"]:
                status = "skipped_paused"
            elif source == "scheduled" and due_at < now - self._misfire_grace:
                status = "skipped_misfire"
            elif any(
                run["automation_id"] == automation_id
                and run["status"] in _ACTIVE_STATUSES
                for run in self._state["runs"].values()
            ):
                status = "skipped_overlap"
            elif not capacity_available:
                status = "skipped_capacity"
            else:
                status = "queued"
            run_id = f"autrun_{uuid4().hex}"
            run = {
                "automation_run_id": run_id,
                "automation_id": automation_id,
                "source": source,
                "due_at": _iso(due_at),
                "status": status,
                "dispatchable": status == "queued",
                "idempotency_key": idempotency_key,
                "created_at": _iso(now),
                "started_at": None,
                "completed_at": _iso(now) if status != "queued" else None,
                "bridge_run_id": None,
                "error": None,
                "web_search": web_search,
            }
            self._state["runs"][run_id] = run
            self._state["idempotency"][idempotency_key] = run_id
            record["last_run_at"] = run["completed_at"] or run["created_at"]
            record["last_status"] = status
            if source == "scheduled":
                # Advance strictly past both the claimed occurrence and claim
                # time so one recorded misfire cannot create a catch-up storm.
                record["next_run_at"] = _next_run_after(
                    record["schedule"],
                    max(due_at, now),
                )
            record["updated_at"] = _iso(now)
            self._prune_runs(protected_run_ids={run_id})
            self._save()
            return _public_run(run)

    def _new_record(
        self,
        payload: Mapping[str, Any],
        now: datetime,
        *,
        automation_id: str | None = None,
        revision: int = 1,
        created_at: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        name = _text(payload.get("name"), "name", 160)
        prompt = _text(payload.get("prompt"), "prompt", 1_048_576)
        mode = payload.get("mode", "observe")
        if mode not in _MODES:
            raise AutomationValidationError("automation mode is invalid")
        target = _normalize_target(payload.get("target"))
        if self._target_validator is not None:
            self._target_validator(target)
        schedule = _normalize_schedule(payload.get("schedule"))
        for field in ("model", "thinking"):
            value = payload.get(field)
            if value is not None:
                payload_value = _text(value, field, 160)
                if field == "model":
                    model = payload_value
                else:
                    thinking = payload_value
            elif field == "model":
                model = None
            else:
                thinking = None
        return {
            "automation_id": automation_id or f"aut_{uuid4().hex}",
            "revision": revision,
            "name": name,
            "prompt": prompt,
            "target": target,
            "mode": mode,
            "model": model,
            "thinking": thinking,
            "schedule": schedule,
            "enabled": enabled,
            "created_at": created_at or _iso(now),
            "updated_at": _iso(now),
            "next_run_at": _next_run(schedule, now) if enabled else None,
            "last_run_at": payload.get("last_run_at"),
            "last_status": payload.get("last_status"),
        }

    def _automation(self, automation_id: str) -> dict[str, Any]:
        try:
            return self._state["automations"][
                _identifier(automation_id, "automation id")
            ]
        except KeyError:
            raise AutomationNotFoundError("automation was not found") from None

    def _run(self, run_id: str) -> dict[str, Any]:
        try:
            return self._state["runs"][_identifier(run_id, "automation run id")]
        except KeyError:
            raise AutomationNotFoundError("automation run was not found") from None

    @staticmethod
    def _require_revision(record: Mapping[str, Any], expected_revision: int) -> None:
        if (
            type(expected_revision) is not int
            or expected_revision != record["revision"]
        ):
            raise AutomationConflictError("automation revision conflict")

    def _prune_runs(self, *, protected_run_ids: set[str] | None = None) -> None:
        protected = protected_run_ids or set()

        runs = self._state["runs"]
        by_automation: dict[str, list[dict[str, Any]]] = {}
        for run in runs.values():
            if run["status"] in _ACTIVE_STATUSES or _is_pending_runtime_link(run):
                continue
            by_automation.setdefault(run["automation_id"], []).append(run)
        remove: set[str] = set()
        for values in by_automation.values():
            protected_count = sum(
                run["automation_run_id"] in protected for run in values
            )
            candidates = [
                run for run in values if run["automation_run_id"] not in protected
            ]
            candidates.sort(key=_terminal_retention_key, reverse=True)
            available = max(
                self._max_runs_per_automation - protected_count,
                0,
            )
            remove.update(item["automation_run_id"] for item in candidates[available:])
        survivors = [
            run
            for run_id, run in runs.items()
            if run_id not in remove
            and run["status"] not in _ACTIVE_STATUSES
            and not _is_pending_runtime_link(run)
        ]
        protected_count = sum(
            run["automation_run_id"] in protected for run in survivors
        )
        candidates = [
            run for run in survivors if run["automation_run_id"] not in protected
        ]
        candidates.sort(key=_terminal_retention_key, reverse=True)
        available = max(self._max_total_runs - protected_count, 0)
        remove.update(item["automation_run_id"] for item in candidates[available:])
        for run_id in remove:
            runs.pop(run_id, None)
        if remove:
            self._rebuild_idempotency()

    def _rebuild_idempotency(self) -> None:
        self._state["idempotency"] = {
            run["idempotency_key"]: run_id
            for run_id, run in self._state["runs"].items()
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "automations": {}, "runs": {}, "idempotency": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise AutomationValidationError("automation state is invalid") from None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not all(
                isinstance(payload.get(key), dict)
                for key in ("automations", "runs", "idempotency")
            )
        ):
            raise AutomationValidationError("automation state is invalid")
        for run in payload["runs"].values():
            if isinstance(run, dict):
                # Older checkpoints predate per-claim search overrides.
                run.setdefault("web_search", None)
        return payload

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            self._state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".automations-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _normalize_target(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AutomationValidationError("automation target is invalid")
    kind = value.get("kind")
    if kind == "standalone" and set(value) == {"kind", "project_id"}:
        return {
            "kind": kind,
            "project_id": _identifier(value["project_id"], "project id"),
        }
    if kind == "continue_thread" and set(value) == {"kind", "thread_id"}:
        return {"kind": kind, "thread_id": _identifier(value["thread_id"], "thread id")}
    raise AutomationValidationError("automation target is invalid")


def _normalize_schedule(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ScheduleValidationError("automation schedule is invalid")
    kind = value.get("kind")
    if kind == "once" and set(value) == {"kind", "at"}:
        return {"kind": kind, "at": _iso(_parse_datetime(value["at"], "schedule time"))}
    if kind == "interval" and set(value) == {"kind", "seconds", "anchor_at"}:
        seconds = value["seconds"]
        if type(seconds) is not int or not 60 <= seconds <= 31_536_000:
            raise ScheduleValidationError("interval seconds are invalid")
        return {
            "kind": kind,
            "seconds": seconds,
            "anchor_at": _iso(_parse_datetime(value["anchor_at"], "interval anchor")),
        }
    if kind == "rrule" and set(value) == {"kind", "rule", "start_at", "timezone"}:
        rule = _text(value["rule"], "recurrence rule", 512)
        if not rule.startswith("RRULE:") or "\n" in rule or "\r" in rule:
            raise ScheduleValidationError("recurrence rule is invalid")
        try:
            timezone = ZoneInfo(_text(value["timezone"], "timezone", 128))
        except ZoneInfoNotFoundError:
            raise ScheduleValidationError("timezone is invalid") from None
        start = _parse_datetime(value["start_at"], "recurrence start")
        try:
            parsed = rrulestr(rule, dtstart=start.astimezone(timezone))
            parsed.after(start.astimezone(timezone), inc=True)
        except (TypeError, ValueError, OverflowError):
            raise ScheduleValidationError("recurrence rule is invalid") from None
        return {
            "kind": kind,
            "rule": rule,
            "start_at": _iso(start),
            "timezone": timezone.key,
        }
    raise ScheduleValidationError("automation schedule is invalid")


def _next_run(schedule: Mapping[str, Any], now: datetime) -> str | None:
    kind = schedule["kind"]
    if kind == "once":
        at = _parse_datetime(schedule["at"], "schedule time")
        return _iso(at) if at > now else None
    if kind == "interval":
        anchor = _parse_datetime(schedule["anchor_at"], "interval anchor")
        seconds = schedule["seconds"]
        if now <= anchor:
            return _iso(anchor)
        steps = ceil((now - anchor).total_seconds() / seconds)
        return _iso(anchor + timedelta(seconds=steps * seconds))
    timezone = ZoneInfo(schedule["timezone"])
    start = _parse_datetime(schedule["start_at"], "recurrence start").astimezone(
        timezone
    )
    try:
        candidate = rrulestr(schedule["rule"], dtstart=start).after(
            now.astimezone(timezone), inc=False
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return _iso(candidate) if candidate is not None else None


def _next_run_after(schedule: Mapping[str, Any], occurrence: datetime) -> str | None:
    """Return the first schedule occurrence strictly after ``occurrence``."""

    kind = schedule["kind"]
    if kind == "once":
        return None
    if kind == "interval":
        anchor = _parse_datetime(schedule["anchor_at"], "interval anchor")
        seconds = schedule["seconds"]
        if occurrence < anchor:
            return _iso(anchor)
        steps = floor((occurrence - anchor).total_seconds() / seconds) + 1
        return _iso(anchor + timedelta(seconds=steps * seconds))
    timezone = ZoneInfo(schedule["timezone"])
    start = _parse_datetime(schedule["start_at"], "recurrence start").astimezone(
        timezone
    )
    try:
        candidate = rrulestr(schedule["rule"], dtstart=start).after(
            occurrence.astimezone(timezone), inc=False
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return _iso(candidate) if candidate is not None else None


def _public_automation(
    value: Mapping[str, Any], *, include_prompt: bool
) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(value[key])
        for key in (
            "automation_id",
            "revision",
            "name",
            "target",
            "mode",
            "model",
            "thinking",
            "schedule",
            "enabled",
            "created_at",
            "updated_at",
            "next_run_at",
            "last_run_at",
            "last_status",
        )
    }
    if include_prompt:
        result["prompt"] = value["prompt"]
    return result


def _public_run(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "automation_run_id",
            "automation_id",
            "source",
            "due_at",
            "status",
            "dispatchable",
            "created_at",
            "started_at",
            "completed_at",
            "bridge_run_id",
            "error",
            "web_search",
        )
    }


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ScheduleValidationError(f"{field} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleValidationError(f"{field} is invalid") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ScheduleValidationError(f"{field} must include a timezone")
    return result.astimezone(UTC)


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: object, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
    ):
        raise AutomationValidationError(f"{field} is invalid")
    return value


def _normalize_web_search(value: object) -> Literal["live", "disabled"] | None:
    if value is None:
        return None
    if type(value) is not str or value not in {"live", "disabled"}:
        raise AutomationValidationError("web_search is invalid")
    return value  # type: ignore[return-value]


def _identifier(value: object, field: str) -> str:
    return _text(value, field, 256)


def _safe_error(value: object) -> str:
    text = (
        "automation dispatch failed"
        if not isinstance(value, str)
        else " ".join(value.split())
    )
    return text[:320] or "automation dispatch failed"
