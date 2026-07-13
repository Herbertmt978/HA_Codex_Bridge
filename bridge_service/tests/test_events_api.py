from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.event_store import EventWaitCapacityError
from codex_bridge_service.models import RunMode, RuntimeProfile


AUTHORIZATION = {"Authorization": "Bearer secret"}
EXPECTED_BATCH_KEYS = {
    "events",
    "next_cursor",
    "minimum_cursor",
    "has_more",
    "heartbeat",
}
EXPECTED_EVENT_KEYS = {
    "cursor",
    "event_id",
    "scope",
    "thread_id",
    "event_type",
    "payload",
    "timestamp",
}


def _app(tmp_path: Path):
    return create_app(root_path=tmp_path / "state", auth_token="secret")


def _event_store(app):
    event_store = getattr(app.state, "event_store", None)
    assert event_store is not None, "Task 8 must compose one global event store"
    return event_store


def _append_event(
    app,
    *,
    operation_key: str,
    scope: str,
    event_type: str,
    payload: dict[str, object],
    thread_id: str | None = None,
):
    return _event_store(app).append(
        operation_key=operation_key,
        scope=scope,
        thread_id=thread_id,
        event_type=event_type,
        payload=payload,
    )


def _assert_event_shape(event: dict[str, Any]) -> None:
    assert set(event) == EXPECTED_EVENT_KEYS
    assert type(event["cursor"]) is int and event["cursor"] > 0
    assert isinstance(event["event_id"], str) and event["event_id"]
    assert event["scope"] in {"auth", "runtime", "thread"}
    if event["scope"] == "thread":
        assert isinstance(event["thread_id"], str) and event["thread_id"]
    else:
        assert event["thread_id"] is None
    assert isinstance(event["event_type"], str) and event["event_type"]
    assert isinstance(event["payload"], dict)
    assert isinstance(event["timestamp"], str) and event["timestamp"]


def _assert_batch_shape(payload: dict[str, Any]) -> None:
    assert set(payload) == EXPECTED_BATCH_KEYS
    assert isinstance(payload["events"], list)
    assert type(payload["next_cursor"]) is int and payload["next_cursor"] >= 0
    assert type(payload["minimum_cursor"]) is int
    assert payload["minimum_cursor"] >= 0
    assert type(payload["has_more"]) is bool
    assert type(payload["heartbeat"]) is bool
    for event in payload["events"]:
        _assert_event_shape(event)


@pytest.mark.parametrize("path", ["/events/replay", "/events/wait"])
def test_global_event_endpoints_require_the_bridge_bearer_token(
    tmp_path: Path,
    path: str,
) -> None:
    client = TestClient(_app(tmp_path))

    missing = client.get(path)
    wrong = client.get(
        path,
        headers={"Authorization": "Bearer do-not-echo-this-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert "do-not-echo-this-token" not in wrong.text


@pytest.mark.parametrize(
    ("path", "extra_params"),
    [
        ("/events/replay", []),
        ("/events/wait", [("timeout_seconds", "0")]),
    ],
)
def test_global_replay_and_wait_return_the_canonical_v1_batch(
    tmp_path: Path,
    path: str,
    extra_params: list[tuple[str, str]],
) -> None:
    app = _app(tmp_path)
    _append_event(
        app,
        operation_key="api-runtime-ready",
        scope="runtime",
        event_type="runtime.ready",
        payload={"ready": True},
    )

    response = TestClient(app).get(
        path,
        headers=AUTHORIZATION,
        params=[("after", "0"), ("scope", "runtime"), *extra_params],
    )

    assert response.status_code == 200
    payload = response.json()
    _assert_batch_shape(payload)
    assert payload["heartbeat"] is False
    assert payload["has_more"] is False
    assert [event["event_type"] for event in payload["events"]] == ["runtime.ready"]
    assert payload["next_cursor"] == payload["events"][-1]["cursor"]


def test_global_replay_filters_auth_runtime_and_thread_scopes(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    _append_event(
        app,
        operation_key="api-auth-1",
        scope="auth",
        event_type="auth.status_changed",
        payload={"state": "logged_out", "revision": 1},
    )
    _append_event(
        app,
        operation_key="api-runtime-1",
        scope="runtime",
        event_type="runtime.ready",
        payload={"ready": True},
    )
    _append_event(
        app,
        operation_key="api-thread-a",
        scope="thread",
        thread_id="thr_a",
        event_type="message.delta",
        payload={"text": "A"},
    )
    _append_event(
        app,
        operation_key="api-thread-b",
        scope="thread",
        thread_id="thr_b",
        event_type="message.delta",
        payload={"text": "B"},
    )
    client = TestClient(app)

    auth_and_runtime = client.get(
        "/events/replay",
        headers=AUTHORIZATION,
        params=[
            ("after", "0"),
            ("scope", "auth"),
            ("scope", "runtime"),
        ],
    )
    thread_a = client.get(
        "/events/replay",
        headers=AUTHORIZATION,
        params=[
            ("after", "0"),
            ("scope", "thread"),
            ("thread_id", "thr_a"),
        ],
    )

    assert auth_and_runtime.status_code == 200
    assert thread_a.status_code == 200
    auth_runtime_payload = auth_and_runtime.json()
    thread_payload = thread_a.json()
    _assert_batch_shape(auth_runtime_payload)
    _assert_batch_shape(thread_payload)
    assert [event["scope"] for event in auth_runtime_payload["events"]] == [
        "auth",
        "runtime",
    ]
    assert [event["thread_id"] for event in thread_payload["events"]] == ["thr_a"]
    cursors = [
        event["cursor"]
        for event in auth_runtime_payload["events"] + thread_payload["events"]
    ]
    assert len(cursors) == len(set(cursors))


@pytest.mark.parametrize("thread_filter", ["", "thr_bad%7Ffilter"])
def test_global_replay_rejects_invalid_thread_filters(
    tmp_path: Path,
    thread_filter: str,
) -> None:
    client = TestClient(_app(tmp_path))

    response = client.get(
        f"/events/replay?thread_id={thread_filter}",
        headers=AUTHORIZATION,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "invalid_event_filter"}


def test_global_replay_honours_a_bounded_limit_and_resumes_without_duplicates(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    for index in range(3):
        _append_event(
            app,
            operation_key=f"api-bounded-{index}",
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": index + 1},
        )
    client = TestClient(app)

    first = client.get(
        "/events/replay",
        headers=AUTHORIZATION,
        params={"after": 0, "scope": "runtime", "limit": 2},
    )

    assert first.status_code == 200
    first_payload = first.json()
    _assert_batch_shape(first_payload)
    assert len(first_payload["events"]) == 2
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"] == first_payload["events"][-1]["cursor"]

    second = client.get(
        "/events/replay",
        headers=AUTHORIZATION,
        params={
            "after": first_payload["next_cursor"],
            "scope": "runtime",
            "limit": 2,
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    _assert_batch_shape(second_payload)
    assert len(second_payload["events"]) == 1
    assert second_payload["has_more"] is False
    first_ids = {event["event_id"] for event in first_payload["events"]}
    assert second_payload["events"][0]["event_id"] not in first_ids


def test_wait_returns_an_empty_heartbeat_batch_at_the_current_cursor(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    _append_event(
        app,
        operation_key="api-heartbeat-anchor",
        scope="runtime",
        event_type="runtime.ready",
        payload={"ready": True},
    )
    client = TestClient(app)
    replay = client.get(
        "/events/replay",
        headers=AUTHORIZATION,
        params={"after": 0, "scope": "runtime"},
    )
    assert replay.status_code == 200
    cursor = replay.json()["next_cursor"]

    heartbeat = client.get(
        "/events/wait",
        headers=AUTHORIZATION,
        params={
            "after": cursor,
            "scope": "runtime",
            "timeout_seconds": 0.01,
        },
    )

    assert heartbeat.status_code == 200
    payload = heartbeat.json()
    _assert_batch_shape(payload)
    assert payload["events"] == []
    assert payload["next_cursor"] == cursor
    assert payload["has_more"] is False
    assert payload["heartbeat"] is True


def test_wait_returns_429_when_subscription_capacity_is_exhausted(
    tmp_path: Path,
) -> None:
    class SaturatedEventStore:
        def wait(self, **_kwargs):
            raise EventWaitCapacityError("capacity exhausted")

    app = _app(tmp_path)
    app.state.event_store = SaturatedEventStore()

    response = TestClient(app).get(
        "/events/wait",
        headers=AUTHORIZATION,
        params={"after": 0, "timeout_seconds": 0},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"]["code"] == "event_wait_capacity_exhausted"


@pytest.mark.parametrize("path", ["/events/replay", "/events/wait"])
def test_expired_global_cursor_returns_410_with_structured_snapshot_guidance(
    tmp_path: Path,
    path: str,
) -> None:
    try:
        from codex_bridge_service.event_store import EventCursorExpiredError
    except ModuleNotFoundError:
        pytest.fail("Task 8 event cursor errors are not implemented", pytrace=False)

    error = EventCursorExpiredError(
        requested_cursor=2,
        minimum_cursor=7,
        snapshot_cursor=11,
        scope="thread",
        thread_id="thr_gap",
    )

    class ExpiredEventStore:
        def replay(self, **_kwargs):
            raise error

        def wait(self, **_kwargs):
            raise error

    app = _app(tmp_path)
    app.state.event_store = ExpiredEventStore()

    response = TestClient(app).get(
        path,
        headers=AUTHORIZATION,
        params={
            "after": 2,
            "scope": "thread",
            "thread_id": "thr_gap",
            "timeout_seconds": 0,
        },
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["code"] == "event_cursor_expired"
    assert detail["minimum_cursor"] == 7
    assert isinstance(detail["snapshot"], dict)
    assert detail["snapshot"]["required"] is True
    assert detail["snapshot"]["cursor"] == 11


@pytest.mark.parametrize("path", ["/events/replay", "/events/wait"])
def test_real_compaction_returns_410_from_global_event_endpoints(
    tmp_path: Path,
    path: str,
) -> None:
    app = _app(tmp_path)
    thread = app.state.storage.create_thread(title="Compact", mode=RunMode.EDIT)
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.delta",
        payload={"text": "retained"},
    )
    journal = app.state.event_store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=(thread.thread_id,),
    ).events
    created, retained_global = journal
    app.state.event_store.compact(
        scope="thread",
        thread_id=thread.thread_id,
        through_cursor=created.cursor,
        snapshot_cursor=retained_global.cursor,
    )

    with TestClient(app) as client:
        response = client.get(
            path,
            headers=AUTHORIZATION,
            params={
                "after": 0,
                "scope": "thread",
                "thread_id": thread.thread_id,
                "timeout_seconds": 0,
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "event_cursor_expired"


def test_v0_thread_replay_returns_410_when_its_sequence_was_compacted(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    thread = app.state.storage.create_thread(title="Legacy gap", mode=RunMode.EDIT)
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.delta",
        payload={"text": "retained"},
    )
    journal = app.state.event_store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=(thread.thread_id,),
    ).events
    created, retained_global = journal
    app.state.event_store.compact(
        scope="thread",
        thread_id=thread.thread_id,
        through_cursor=created.cursor,
        snapshot_cursor=retained_global.cursor,
    )

    with TestClient(app) as client:
        response = client.get(
            f"/threads/{thread.thread_id}/events/replay",
            headers=AUTHORIZATION,
            params={"after": 0},
        )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["code"] == "thread_event_cursor_expired"
    assert detail["minimum_sequence"] == 2
    assert detail["snapshot"]["cursor"] == retained_global.cursor


class AuthLifecycleAppServer:
    generation = 1

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def register_notification_handler(self, method: str, handler) -> None:
        self.handlers[method] = handler

    def request(
        self,
        method: str,
        params: object = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        del params, timeout_seconds
        assert method == "account/read"
        return {"account": None, "requiresOpenaiAuth": True}


def test_auth_events_replay_before_any_chat_exists(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=AuthLifecycleAppServer,
        runner_factory=lambda _storage: object(),
    )

    with TestClient(app) as client:
        assert client.get("/threads", headers=AUTHORIZATION).json() == []
        response = client.get(
            "/events/replay",
            headers=AUTHORIZATION,
            params={"after": 0, "scope": "auth"},
        )

        assert response.status_code == 200
        payload = response.json()
        _assert_batch_shape(payload)
        assert payload["events"]
        latest = payload["events"][-1]
        assert latest["scope"] == "auth"
        assert latest["thread_id"] is None
        assert latest["event_type"] == "auth.status_changed"
        assert latest["payload"]["state"] == "logged_out"
        assert latest["payload"]["auth_required"] is True
        assert "email" not in json.dumps(payload)


def test_v1_auth_event_projection_omits_device_login_material(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    _append_event(
        app,
        operation_key="api-auth-private",
        scope="auth",
        event_type="auth.status_changed",
        payload={
            "revision": 1,
            "state": "login_running",
            "busy": True,
            "auth_required": True,
            "auth_mode": "chatgpt",
            "plan_type": "pro",
            "updated_at": "2026-07-13T12:00:00Z",
            "message": "private auth detail",
            "verification_uri": "https://example.invalid/device",
            "login_url": "https://example.invalid/device",
            "user_code": "SECRET-CODE",
            "output_tail": ["SECRET-CODE"],
        },
    )

    response = TestClient(app).get(
        "/events/replay",
        headers=AUTHORIZATION,
        params={"after": 0, "scope": "auth"},
    )

    assert response.status_code == 200
    event_payload = response.json()["events"][0]["payload"]
    assert event_payload == {
        "revision": 1,
        "state": "login_running",
        "busy": True,
        "auth_required": True,
        "auth_mode": "chatgpt",
        "plan_type": "pro",
        "updated_at": "2026-07-13T12:00:00Z",
    }
    assert "SECRET-CODE" not in response.text


def test_per_thread_v0_replay_remains_a_list_shaped_sequence_adapter(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    project = app.state.storage.create_project(
        name="Legacy adapter",
        root_path=str(tmp_path / "workspace"),
    )
    thread = app.state.storage.create_thread(
        title="Legacy replay",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.delta",
        payload={"text": "second"},
    )

    response = TestClient(app).get(
        f"/threads/{thread.thread_id}/events/replay",
        headers=AUTHORIZATION,
        params={"after": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert set(payload[0]) == {
        "event_id",
        "thread_id",
        "sequence",
        "event_type",
        "payload",
        "timestamp",
    }
    assert payload[0]["thread_id"] == thread.thread_id
    assert payload[0]["sequence"] == 2
    assert payload[0]["event_type"] == "message.delta"
    assert "events" not in payload[0]
    assert "next_cursor" not in payload[0]
