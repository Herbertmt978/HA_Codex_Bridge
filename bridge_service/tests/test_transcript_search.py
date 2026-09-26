from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.event_store import BridgeEventStore
from codex_bridge_service.models import RunMode


def test_transcript_search_finds_visible_assistant_text_and_excludes_hidden_events(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Searchable chat", mode=RunMode.FULL_AUTO)
    storage = app.state.storage
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.completed",
        payload={"role": "assistant", "text": "The hidden-in-response phrase is stored here"},
    )
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="codex.event",
        payload={"role": "assistant", "text": "hidden runtime phrase"},
    )
    client = TestClient(app)

    response = client.get(
        "/search/transcript",
        params={"q": "hidden-in-response"},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {
                "thread_id": thread.thread_id,
                "title": "Searchable chat",
                "archived_at": None,
                "role": "assistant",
                    "sequence": 2,
                "excerpt": "The hidden-in-response phrase is stored here",
            }
        ],
        "has_more": False,
        "next_cursor": None,
        "complete": True,
        "oldest_indexed_cursor": 2,
        "maximum_text_bytes": 128 * 1024 * 1024,
        "maximum_messages": 131072,
    }


def test_transcript_search_filters_archived_and_deleted_content_immediately(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Archived chat", mode=RunMode.FULL_AUTO)
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.created",
        payload={"role": "user", "text": "searchable phrase"},
    )
    app.state.storage.archive_thread(thread.thread_id)
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    hidden = client.get("/search/transcript?q=searchable", headers=headers)
    visible = client.get(
        "/search/transcript?q=searchable&include_archived=true", headers=headers
    )
    app.state.storage.delete_thread(thread.thread_id)
    deleted = client.get(
        "/search/transcript?q=searchable&include_archived=true", headers=headers
    )

    assert hidden.status_code == 200
    assert hidden.json()["results"] == []
    assert len(visible.json()["results"]) == 1
    assert visible.json()["results"][0]["sequence"] == 2
    assert deleted.status_code == 200
    assert deleted.json()["results"] == []


def test_transcript_search_requires_bridge_auth_and_bounds_results(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    first = app.state.storage.create_thread(title="One", mode=RunMode.FULL_AUTO)
    second = app.state.storage.create_thread(title="Two", mode=RunMode.FULL_AUTO)
    for thread in (first, second):
        app.state.storage.append_thread_event(
            thread_id=thread.thread_id,
            event_type="message.created",
            payload={"role": "user", "text": "same phrase"},
        )
    client = TestClient(app)

    denied = client.get("/search/transcript?q=same")
    limited = client.get(
        "/search/transcript?q=same&limit=1",
        headers={"Authorization": "Bearer secret"},
    )

    assert denied.status_code == 401
    assert limited.status_code == 200
    assert len(limited.json()["results"]) == 1
    assert limited.json()["has_more"] is True
    cursor = limited.json()["next_cursor"]
    next_page = client.get(
        "/search/transcript",
        params={"q": "same", "limit": 1, "before_cursor": cursor},
        headers={"Authorization": "Bearer secret"},
    )
    assert next_page.status_code == 200
    assert len(next_page.json()["results"]) == 1
    assert next_page.json()["has_more"] is False


def test_compacted_transcript_stays_searchable_and_can_be_loaded_by_anchor(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Compacted", mode=RunMode.FULL_AUTO)
    storage = app.state.storage
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.completed",
        payload={"role": "assistant", "text": "older compacted answer"},
    )
    with storage.event_store._connect() as connection:
        cursor = connection.execute(
            "SELECT MAX(cursor) FROM events WHERE thread_id = ?", (thread.thread_id,)
        ).fetchone()[0]
    storage.event_store.compact(
        scope="thread",
        thread_id=thread.thread_id,
        through_cursor=cursor,
        snapshot_cursor=cursor,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    results = client.get("/search/transcript?q=compacted", headers=headers)
    sequence = results.json()["results"][0]["sequence"]
    message = client.get(
        f"/threads/{thread.thread_id}/transcript/{sequence}", headers=headers
    )

    assert results.status_code == 200
    assert results.json()["results"][0]["sequence"] == 2
    assert message.status_code == 200
    assert message.json()["text"] == "older compacted answer"


def test_transcript_search_excludes_credential_like_visible_messages(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Sensitive", mode=RunMode.FULL_AUTO)
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.created",
        payload={"role": "user", "text": "api_key=abcdefghijklmnopqrstu"},
    )
    client = TestClient(app)

    result = client.get(
        "/search/transcript?q=abcdefghijklmnop",
        headers={"Authorization": "Bearer secret"},
    )

    assert result.status_code == 200
    assert result.json()["results"] == []


def test_queue_edit_updates_original_search_anchor_and_loaded_message(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Edited draft", mode=RunMode.FULL_AUTO)
    storage = app.state.storage
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.created",
        payload={"role": "user", "text": "draft original"},
    )
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.updated",
        payload={"role": "user", "text": "draft corrected", "message_sequence": 2},
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    old = client.get("/search/transcript?q=original", headers=headers).json()
    updated = client.get("/search/transcript?q=corrected", headers=headers).json()
    message = client.get(
        f"/threads/{thread.thread_id}/transcript/2", headers=headers
    ).json()

    assert old["results"] == []
    assert updated["results"][0]["sequence"] == 2
    assert message["text"] == "draft corrected"


def test_cancelled_queue_draft_is_removed_from_search_index(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Cancelled draft", mode=RunMode.FULL_AUTO)
    storage = app.state.storage
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.created",
        payload={"role": "user", "text": "cancelled secret phrase"},
    )
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.removed",
        payload={"message_sequence": 2},
    )

    result = TestClient(app).get(
        "/search/transcript?q=cancelled",
        headers={"Authorization": "Bearer secret"},
    )

    assert result.status_code == 200
    assert result.json()["results"] == []


def test_transcript_index_evicts_old_rows_without_blocking_journal_writes(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Bounded", mode=RunMode.FULL_AUTO)
    event_store = app.state.storage.event_store
    event_store.max_transcript_bytes = 14

    for text in ("first message", "second message"):
        app.state.storage.append_thread_event(
            thread_id=thread.thread_id,
            event_type="message.created",
            payload={"role": "user", "text": text},
        )
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="run.completed",
        payload={"run_id": "run-1"},
    )

    client = TestClient(app)
    response = client.get(
        "/search/transcript?q=message", headers={"Authorization": "Bearer secret"}
    )

    assert response.status_code == 200
    assert response.json()["complete"] is False
    assert response.json()["maximum_text_bytes"] == 14
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["sequence"] == 3


def test_update_replacing_oldest_row_does_not_double_subtract_quota(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Quota update", mode=RunMode.FULL_AUTO)
    event_store = app.state.storage.event_store
    event_store.max_transcript_bytes = 20
    for text in ("0123456789", "abcdefghij"):
        app.state.storage.append_thread_event(
            thread_id=thread.thread_id,
            event_type="message.created",
            payload={"role": "user", "text": text},
        )
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.updated",
        payload={
            "role": "user",
            "text": "123456789012345",
            "message_sequence": 2,
        },
    )

    with event_store._connect() as connection:
        actual_bytes = connection.execute(
            "SELECT SUM(length(CAST(text AS BLOB))) FROM transcript_messages"
        ).fetchone()[0]
        recorded_bytes = connection.execute(
            "SELECT used_bytes FROM transcript_state WHERE singleton = 1"
        ).fetchone()[0]

    assert actual_bytes == recorded_bytes == 15


def test_sustained_visible_traffic_compacts_and_reopens_without_blocking(tmp_path) -> None:
    database = tmp_path / "bounded-journal.sqlite3"
    store = BridgeEventStore(database, max_journal_bytes=512 * 1024)
    final_cursor = 0
    for index in range(350):
        record = store.append(
            operation_key=f"message:{index}",
            scope="thread",
            thread_id="thread-traffic",
            event_type="message.created",
            payload={"role": "user", "text": f"visible needle {index:04d}"},
        )
        final_cursor = record.cursor
    activity = store.append(
        operation_key="activity:after-transcript-traffic",
        scope="runtime",
        event_type="runtime.status",
        payload={"state": "ready"},
    )
    store.compact(
        scope="thread",
        thread_id="thread-traffic",
        through_cursor=final_cursor,
        snapshot_cursor=final_cursor,
    )
    store.close()

    reopened = BridgeEventStore(database, max_journal_bytes=512 * 1024)
    results = reopened.search_transcript_messages(
        query="needle", thread_ids=("thread-traffic",), before_cursor=None, limit=400
    )
    assert len(results) == 128
    assert reopened.transcript_index_status()["complete"] is False
    assert reopened.get_transcript_message("thread-traffic", 350)["text"] == "visible needle 0349"
    physical_bytes = sum(
        path.stat().st_size
        for path in (
            reopened.path,
            reopened.path.with_name(f"{reopened.path.name}-wal"),
            reopened.path.with_name(f"{reopened.path.name}-shm"),
        )
        if path.exists()
    )
    assert physical_bytes <= 512 * 1024 + 256 * 1024
    assert reopened.replay(after_cursor=activity.cursor - 1, scopes=("runtime",)).events[0] == activity
    reopened.purge_thread("thread-traffic")
    assert reopened.search_transcript_messages(
        query="needle", thread_ids=("thread-traffic",), before_cursor=None, limit=10
    ) == []
    reopened.close()
