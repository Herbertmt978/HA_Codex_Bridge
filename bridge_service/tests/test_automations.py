from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.automations import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationStore,
    AutomationValidationError,
    ScheduleValidationError,
)
from codex_bridge_service.routes.automations import create_router


NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


def _payload(**overrides):
    payload = {
        "name": "Morning repository check",
        "prompt": "Check the repository and report only actionable findings.",
        "target": {"kind": "standalone", "project_id": "prj_home"},
        "mode": "observe",
        "schedule": {
            "kind": "rrule",
            "rule": "RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=30",
            "start_at": "2026-07-15T10:30:00+01:00",
            "timezone": "Europe/London",
        },
    }
    payload.update(overrides)
    return payload


def test_store_persists_safe_definition_and_calculates_rrule_in_utc(tmp_path):
    store = AutomationStore(tmp_path)
    created = store.create(_payload(), now=NOW)

    assert created["next_run_at"] == "2026-07-15T09:30:00Z"
    assert "prompt" not in store.list()[0]
    assert store.get(created["automation_id"])["prompt"].startswith(
        "Check the repository"
    )

    restored = AutomationStore(tmp_path)
    assert restored.get(created["automation_id"])["revision"] == 1
    assert restored.list()[0]["next_run_at"] == "2026-07-15T09:30:00Z"


@pytest.mark.parametrize(
    "schedule",
    [
        {
            "kind": "rrule",
            "rule": "RRULE:FREQ=NOPE",
            "start_at": "2026-07-15T10:30:00Z",
            "timezone": "UTC",
        },
        {"kind": "once", "at": "not-a-date"},
        {"kind": "interval", "seconds": 1},
    ],
)
def test_store_rejects_invalid_schedules(tmp_path, schedule):
    store = AutomationStore(tmp_path)

    with pytest.raises(ScheduleValidationError):
        store.create(_payload(schedule=schedule), now=NOW)


def test_store_rejects_ambiguous_or_unscoped_targets(tmp_path):
    store = AutomationStore(tmp_path)

    with pytest.raises(AutomationValidationError):
        store.create(
            _payload(
                target={
                    "kind": "standalone",
                    "project_id": "prj_home",
                    "thread_id": "thr_extra",
                }
            ),
            now=NOW,
        )


def test_claim_is_idempotent_and_rejects_stale_or_overlapping_dispatch(tmp_path):
    store = AutomationStore(tmp_path)
    automation = store.create(
        _payload(
            schedule={
                "kind": "interval",
                "seconds": 3600,
                "anchor_at": "2026-07-15T09:00:00Z",
            }
        ),
        now=NOW,
    )
    automation_id = automation["automation_id"]

    first = store.claim(
        automation_id,
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:one",
        expected_revision=1,
        now=NOW + timedelta(hours=1),
    )
    duplicate = store.claim(
        automation_id,
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:one",
        expected_revision=1,
        now=NOW + timedelta(hours=1),
    )
    overlap = store.claim(
        automation_id,
        due_at="2026-07-15T11:00:00Z",
        idempotency_key="schedule:two",
        expected_revision=1,
        now=NOW + timedelta(hours=2),
    )

    assert first["status"] == "queued"
    assert duplicate == first
    assert overlap["status"] == "skipped_overlap"
    with pytest.raises(AutomationConflictError, match="revision"):
        store.claim(
            automation_id,
            due_at="2026-07-15T12:00:00Z",
            idempotency_key="schedule:stale",
            expected_revision=0,
            now=NOW + timedelta(hours=3),
        )


def test_restart_reconciles_an_unlinked_queued_run_after_dispatch_crash(tmp_path):
    first = AutomationStore(tmp_path)
    automation = first.create(
        _payload(schedule={"kind": "once", "at": "2026-07-15T10:00:00Z"}),
        now=NOW,
    )
    queued = first.run_now(automation["automation_id"], now=NOW)

    restored = AutomationStore(tmp_path)

    recovered = restored.list_runs(automation["automation_id"])[0]
    next_run = restored.run_now(
        automation["automation_id"], now=NOW + timedelta(minutes=1)
    )
    assert recovered["automation_run_id"] == queued["automation_run_id"]
    assert recovered["status"] == "interrupted_restart"
    assert recovered["dispatchable"] is False
    assert next_run["status"] == "queued"


def test_claim_records_capacity_and_misfire_without_dispatching(tmp_path):
    store = AutomationStore(tmp_path, misfire_grace_seconds=60)
    automation = store.create(
        _payload(schedule={"kind": "once", "at": "2026-07-15T10:00:00Z"}), now=NOW
    )

    missed = store.claim(
        automation["automation_id"],
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:missed",
        expected_revision=1,
        now=NOW + timedelta(hours=2),
    )
    capacity = store.run_now(
        automation["automation_id"], capacity_available=False, now=NOW
    )

    assert missed["status"] == "skipped_misfire"
    assert capacity["status"] == "skipped_capacity"
    assert all(not record["dispatchable"] for record in (missed, capacity))


def test_scheduler_snapshot_preserves_an_overdue_occurrence_until_claimed(tmp_path):
    store = AutomationStore(tmp_path, misfire_grace_seconds=60)
    automation = store.create(
        _payload(schedule={"kind": "once", "at": "2026-07-15T10:00:00Z"}),
        now=NOW,
    )
    restored = AutomationStore(tmp_path, misfire_grace_seconds=60)

    overdue = restored.scheduler_snapshot(now=NOW + timedelta(hours=2))

    assert overdue == [
        {
            "automation_id": automation["automation_id"],
            "revision": 1,
            "next_run_at": "2026-07-15T10:00:00Z",
        }
    ]
    assert (
        restored.get(automation["automation_id"])["next_run_at"]
        == "2026-07-15T10:00:00Z"
    )

    claimed = restored.claim(
        automation["automation_id"],
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:overdue",
        expected_revision=1,
        now=NOW + timedelta(hours=2),
    )

    assert claimed["status"] == "skipped_misfire"
    assert restored.scheduler_snapshot(now=NOW + timedelta(hours=2)) == []


@pytest.mark.parametrize(
    ("schedule", "expected_next"),
    [
        (
            {"kind": "interval", "seconds": 3600, "anchor_at": "2026-07-15T10:00:00Z"},
            "2026-07-15T11:00:00Z",
        ),
        (
            {
                "kind": "rrule",
                "rule": "RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=0",
                "start_at": "2026-07-15T10:00:00Z",
                "timezone": "UTC",
            },
            "2026-07-16T10:00:00Z",
        ),
    ],
)
def test_exact_due_scheduled_claim_advances_interval_and_rrule_occurrence(
    tmp_path,
    schedule,
    expected_next,
):
    store = AutomationStore(tmp_path)
    automation = store.create(_payload(schedule=schedule), now=NOW)
    automation_id = automation["automation_id"]

    claimed = store.claim(
        automation_id,
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:exact",
        expected_revision=1,
        now=NOW + timedelta(hours=1),
    )

    assert claimed["status"] == "queued"
    assert store.get(automation_id)["next_run_at"] == expected_next
    assert store.scheduler_snapshot(now=NOW + timedelta(hours=1)) == [
        {
            "automation_id": automation_id,
            "revision": 1,
            "next_run_at": expected_next,
        }
    ]
    assert (
        store.claim(
            automation_id,
            due_at="2026-07-15T10:00:00Z",
            idempotency_key="schedule:exact",
            expected_revision=1,
            now=NOW + timedelta(hours=1),
        )
        == claimed
    )


def test_overdue_recurring_claim_skips_catch_up_storm(tmp_path):
    store = AutomationStore(tmp_path, misfire_grace_seconds=60)
    automation = store.create(
        _payload(
            schedule={
                "kind": "interval",
                "seconds": 3600,
                "anchor_at": "2026-07-15T10:00:00Z",
            }
        ),
        now=NOW,
    )

    claimed = store.claim(
        automation["automation_id"],
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="schedule:overdue-interval",
        expected_revision=1,
        now=NOW + timedelta(hours=4),
    )

    assert claimed["status"] == "skipped_misfire"
    assert store.get(automation["automation_id"])["next_run_at"] == (
        "2026-07-15T14:00:00Z"
    )


def test_bridge_run_completion_lookup_survives_a_store_restart(tmp_path):
    first = AutomationStore(tmp_path)
    automation = first.create(
        _payload(
            schedule={
                "kind": "interval",
                "seconds": 3600,
                "anchor_at": "2026-07-15T09:00:00Z",
            }
        ),
        now=NOW,
    )
    queued = first.run_now(automation["automation_id"], now=NOW)
    first.mark_running(
        queued["automation_run_id"], bridge_run_id="run_bridge123", now=NOW
    )

    restored = AutomationStore(tmp_path)
    completed = restored.complete_by_bridge_run(
        "run_bridge123", status="completed", now=NOW
    )

    assert completed["automation_run_id"] == queued["automation_run_id"]
    assert completed["status"] == "completed"


def test_fast_runtime_terminal_before_link_is_reconciled_without_overlap(
    tmp_path,
):
    store = AutomationStore(tmp_path)
    automation = store.create(
        _payload(
            schedule={
                "kind": "interval",
                "seconds": 3600,
                "anchor_at": "2026-07-15T09:00:00Z",
            }
        ),
        now=NOW,
    )
    queued = store.run_now(automation["automation_id"], now=NOW)

    terminal = store.complete_runtime_run(
        "run_fast123",
        client_request_id=f"automation:{queued['automation_run_id']}",
        unattended=True,
        status="completed",
        now=NOW,
    )
    linked = store.mark_running(
        queued["automation_run_id"], bridge_run_id="run_fast123", now=NOW
    )

    assert terminal["status"] == "completed"
    assert linked == terminal
    restored = AutomationStore(tmp_path)
    assert restored.list_runs(automation["automation_id"])[0] == terminal
    fresh = restored.run_now(
        automation["automation_id"], now=NOW + timedelta(minutes=1)
    )
    overlap = restored.run_now(
        automation["automation_id"], now=NOW + timedelta(minutes=2)
    )
    assert fresh["status"] == "queued"
    assert overlap["status"] == "skipped_overlap"


def test_runtime_terminal_requires_unattended_automation_request_identity(tmp_path):
    store = AutomationStore(tmp_path)
    automation = store.create(_payload(), now=NOW)
    queued = store.run_now(automation["automation_id"], now=NOW)

    with pytest.raises(AutomationNotFoundError):
        store.complete_runtime_run(
            "run_interactive",
            client_request_id=f"automation:{queued['automation_run_id']}",
            unattended=False,
            status="completed",
            now=NOW,
        )


def test_pause_resume_update_and_delete_enforce_revision_and_active_run_safety(
    tmp_path,
):
    store = AutomationStore(tmp_path)
    automation = store.create(
        _payload(
            schedule={
                "kind": "interval",
                "seconds": 3600,
                "anchor_at": "2026-07-15T09:00:00Z",
            }
        ),
        now=NOW,
    )
    automation_id = automation["automation_id"]
    active = store.run_now(automation_id, now=NOW)

    paused = store.pause(automation_id, expected_revision=1, now=NOW)
    assert paused["enabled"] is False
    with pytest.raises(AutomationConflictError, match="active"):
        store.delete(automation_id, expected_revision=2)
    store.complete(active["automation_run_id"], status="completed", now=NOW)
    resumed = store.resume(automation_id, expected_revision=2, now=NOW)
    updated = store.update(
        automation_id, {"name": "Safer check"}, expected_revision=3, now=NOW
    )
    store.pause(automation_id, expected_revision=4, now=NOW)
    store.delete(automation_id, expected_revision=5)

    assert resumed["enabled"] is True
    assert updated["name"] == "Safer check"
    with pytest.raises(AutomationNotFoundError):
        store.get(automation_id)


def test_router_uses_app_state_and_projects_safe_errors(tmp_path):
    app = FastAPI()
    app.state.auth_token = "secret"
    app.state.automations = AutomationStore(tmp_path)
    app.include_router(create_router())
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    created = client.post("/automations", headers=headers, json=_payload())
    assert created.status_code == 201
    automation_id = created.json()["automation_id"]
    assert "prompt" not in client.get("/automations", headers=headers).json()[0]

    conflict = client.patch(
        f"/automations/{automation_id}",
        headers=headers,
        json={"name": "Changed", "expected_revision": 0},
    )
    missing = client.post(
        "/automations/aut_missing/runs",
        headers=headers,
        json={"source": "manual"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "automation_revision_conflict",
        "retryable": False,
    }
    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "automation_not_found",
        "retryable": False,
    }


def test_router_projects_a_dispatch_failure_as_a_safe_blocked_run(tmp_path):
    app = FastAPI()
    app.state.auth_token = "secret"
    app.state.automations = AutomationStore(tmp_path)

    def reject_dispatch(_claim):
        raise RuntimeError("private runner detail")

    app.state.automation_dispatch = reject_dispatch
    app.include_router(create_router())
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    automation_id = client.post(
        "/automations", headers=headers, json=_payload()
    ).json()["automation_id"]

    response = client.post(
        f"/automations/{automation_id}/runs",
        headers=headers,
        json={"source": "manual"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "blocked"
    assert response.json()["dispatchable"] is False
    assert response.json()["error"] == "automation dispatcher rejected the claim"


def test_router_derives_capacity_from_the_runtime_gate(tmp_path):
    app = FastAPI()
    app.state.auth_token = "secret"
    app.state.automations = AutomationStore(tmp_path)
    app.state.runtime_gate = SimpleNamespace(
        limits=SimpleNamespace(max_active_turns=1, max_queued_prompts=0),
        snapshot=lambda: SimpleNamespace(
            active_turns=1,
            queued_prompts=0,
            auth_mutation_active=False,
            config_mutation_active=False,
            closed=False,
        ),
    )
    app.state.automation_dispatch = lambda _claim: pytest.fail(
        "a capacity-skipped claim must not dispatch"
    )
    app.include_router(create_router())
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    automation_id = client.post(
        "/automations",
        headers=headers,
        json=_payload(),
    ).json()["automation_id"]

    response = client.post(
        f"/automations/{automation_id}/runs",
        headers=headers,
        json={"source": "manual", "capacity_available": True},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "skipped_capacity"
    assert response.json()["dispatchable"] is False
