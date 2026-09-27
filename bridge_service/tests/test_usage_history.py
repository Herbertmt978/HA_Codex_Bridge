from datetime import UTC, datetime, timedelta
from io import BytesIO
import json

import pytest
from pydantic import ValidationError

from codex_bridge_service.usage_history import DurationBudget, UsageHistory, UsageHistoryError, UsageRun
import codex_bridge_service.usage_history as usage_module

START = "2026-09-27T10:00:00+00:00"
LATER = "2026-09-27T10:00:10+00:00"


def row(run_id="run-one", *, account="a", native="native-one", thread="thread-one", **kwargs):
    return UsageRun(run_id=run_id, thread_id=thread, project_id="project-one",
                    account_marker=account * 64 if account else None,
                    native_thread_id=native, native_turn_id=run_id, started_at=START, **kwargs)


def snapshot(count, *, last=999):
    return {"total": {"totalTokens": count, "inputTokens": count,
                      "outputTokens": 0, "cachedInputTokens": 0, "reasoningOutputTokens": 0},
            "last": {"totalTokens": last}, "modelContextWindow": 1000}


def observe(history, count, *, run_id="run-one", native="native-one", observed_at=LATER):
    return history.observe(run_id, native_thread_id=native, native_turn_id=run_id,
                           token_usage=snapshot(count), observed_at=observed_at)


def test_cumulative_not_last_and_duplicates_reorder_reload(tmp_path):
    history = UsageHistory(tmp_path)
    history.start(row(), fresh_native_thread=True)
    assert observe(history, 100)
    assert not observe(history, 100)
    assert not observe(history, 50, observed_at=START)
    assert observe(history, 180)
    assert history.view()["known_reported_tokens"] == 180
    history.close()
    reopened = UsageHistory(tmp_path)
    assert not observe(reopened, 180)
    assert observe(reopened, 200)
    assert reopened.view()["known_reported_tokens"] == 200
    assert reopened.view()["items"][0]["reported_thread_tokens"] == 200
    reopened.close()


def test_unknown_not_zero_and_resumed_baseline(tmp_path):
    history = UsageHistory(tmp_path)
    history.start(row())
    assert history.view()["known_reported_tokens"] is None
    observe(history, 500)
    assert history.view()["items"][0]["reported_tokens"] is None
    observe(history, 550)
    assert history.view()["known_reported_tokens"] == 50
    assert history.view()["coverage"] == "partial"
    history.finish("run-one", status="completed", finished_at=LATER)
    history.start(row("run-two"))
    assert not observe(history, 550, run_id="run-one")
    assert observe(history, 570, run_id="run-two")
    assert history.view()["items"][0]["reported_tokens"] == 20
    assert history.view()["coverage"] == "partial"
    history.close()


def test_verified_zero_account_boundaries_and_no_private_ids(tmp_path):
    history = UsageHistory(tmp_path)
    history.start(row(), fresh_native_thread=True)
    observe(history, 0)
    # Identical native ID in another execution account has a separate watermark.
    history.start(row("run-two", account="b", thread="thread-two"), fresh_native_thread=True)
    observe(history, 20, run_id="run-two")
    history.start(row("run-three", account=None, native="native-three"), fresh_native_thread=True)
    assert history.view()["known_reported_tokens"] == 20
    assert history.view()["coverage"] == "partial"
    view = history.view()
    items = {item["run_id"]: item for item in view["items"]}
    assert items["run-two"]["reported_tokens"] == 20
    assert items["run-one"]["reported_tokens"] == 0
    assert items["run-three"]["reported_tokens"] is None
    assert items["run-two"]["account_group"] != items["run-one"]["account_group"]
    assert "a" * 64 not in str(view) and "native-one" not in str(view)
    assert view["billing"] == "not_reported" and not view["token_limit_supported"]
    history.close()


def test_bad_missing_synthetic_reset_and_wrong_turn(tmp_path):
    history = UsageHistory(tmp_path)
    history.start(row(), fresh_native_thread=True)
    observe(history, 100)
    assert not history.observe("run-one", native_thread_id="native-one", native_turn_id="wrong",
                               token_usage=snapshot(999), observed_at=LATER)
    synthetic = snapshot(1000)
    synthetic["total"]["inputTokens"] = 0
    assert not history.observe("run-one", native_thread_id="native-one", native_turn_id="run-one",
                               token_usage=synthetic, observed_at=LATER)
    assert history.view()["known_reported_tokens"] == 100
    assert history.view()["coverage"] == "partial"
    assert not observe(history, 5)
    assert not history.observe("run-one", native_thread_id="native-one", native_turn_id="run-one",
                               token_usage=None, observed_at=LATER)
    assert history.view()["known_reported_tokens"] == 100
    history.close()


def test_atomic_failed_write_does_not_advance_watermark(tmp_path, monkeypatch):
    history = UsageHistory(tmp_path)
    history.start(row(), fresh_native_thread=True)
    original = history._boundary.atomic_write_bytes
    def fail(*_args):
        raise OSError("injected disk failure")
    monkeypatch.setattr(history._boundary, "atomic_write_bytes", fail)
    with pytest.raises(OSError):
        observe(history, 100)
    assert history.view()["known_reported_tokens"] is None
    monkeypatch.setattr(history._boundary, "atomic_write_bytes", original)
    assert observe(history, 100)
    assert history.view()["known_reported_tokens"] == 100
    history.close()


def test_bounded_retention_keeps_watermark_and_active_rows(tmp_path):
    history = UsageHistory(tmp_path, max_runs=1)
    history.start(row(), fresh_native_thread=True)
    observe(history, 100)
    with pytest.raises(UsageHistoryError):
        history.start(row("other", native="other"), fresh_native_thread=True)
    history.finish("run-one", status="completed", finished_at=LATER)
    history.start(row("run-two"))
    observe(history, 150, run_id="run-two")
    assert history.view()["known_reported_tokens"] == 50
    assert history.view()["evicted_runs"] == 1
    assert len(history.view()["items"]) == 1
    history.close()


def test_stop_intent_is_durable_exact_boundary_and_never_resets(tmp_path):
    history = UsageHistory(tmp_path)
    history.start(row(budget=DurationBudget(max_duration_seconds=10)), fresh_native_thread=True)
    assert not history.request_budget_stop("run-one", elapsed_seconds=9.99, requested_at=LATER)
    assert history.request_budget_stop("run-one", elapsed_seconds=10, requested_at=LATER)
    assert not history.request_budget_stop("run-one", elapsed_seconds=11, requested_at=LATER)
    history.close()
    reopened = UsageHistory(tmp_path)
    assert reopened.pending_budget_stops() == ("run-one",)
    with pytest.raises(UsageHistoryError):
        reopened.start(row(budget=DurationBudget(max_duration_seconds=20)))
    reopened.finish("run-one", status="cancelled", finished_at=LATER)
    assert reopened.pending_budget_stops() == ()
    assert reopened.view()["items"][0]["duration_seconds"] == 10
    reopened.close()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10", 86_401])
def test_duration_rejects_invalid_limits(value):
    with pytest.raises(ValidationError):
        DurationBudget(max_duration_seconds=value)


def test_duration_deadline_and_timezone():
    budget = DurationBudget(max_duration_seconds=60)
    assert datetime.fromisoformat(budget.deadline(START)) == datetime.fromisoformat(START) + timedelta(seconds=60)
    with pytest.raises(ValueError):
        budget.deadline("2026-09-27T10:00:00")
    assert datetime.fromisoformat(budget.deadline(START)).tzinfo == UTC


@pytest.mark.parametrize("failure", ["oversized", "excessive_scopes", "excessive_accounts", "invalid_json"])
def test_constructor_rejections_close_owned_boundary(tmp_path, monkeypatch, failure):
    boundaries = []
    monkeypatch.setattr(usage_module, "MAX_SCOPES", 1)
    monkeypatch.setattr(usage_module, "MAX_BYTES", 1024)
    payload = {
        "oversized": b" " * 1025,
        "excessive_scopes": json.dumps({"scopes": {"one": {"run_id": "one"}, "two": {"run_id": "two"}}}).encode(),
        "excessive_accounts": json.dumps({"accounts": {"one": 1, "two": 2}}).encode(),
        "invalid_json": b"invalid-json",
    }[failure]

    class Boundary:
        def __init__(self, *_args, **_kwargs):
            self.close_count = 0
            boundaries.append(self)

        def open_regular_file(self, _relative):
            return BytesIO(payload)

        def close(self):
            self.close_count += 1

    monkeypatch.setattr(usage_module, "WorkspaceBoundary", Boundary)
    with pytest.raises(UsageHistoryError):
        UsageHistory(tmp_path)
    assert len(boundaries) == 1
    assert boundaries[0].close_count == 1
