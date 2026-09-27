from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode


def test_selected_search_counts_all_retained_pages_and_excludes_other_chat_and_tools(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    selected = storage.create_thread(title="Selected", mode=RunMode.EDIT)
    other = storage.create_thread(title="Other", mode=RunMode.EDIT)
    for index in range(105):
        storage.append_thread_event(
            thread_id=selected.thread_id, event_type="message.created",
            payload={"role": "user", "text": f"Needle {index}"},
        )
    storage.append_thread_event(
        thread_id=other.thread_id, event_type="message.completed",
        payload={"role": "assistant", "text": "Needle in another chat"},
    )
    storage.append_thread_event(
        thread_id=selected.thread_id, event_type="codex.event",
        payload={"role": "assistant", "text": "Needle private tool output"},
    )
    client = TestClient(app)
    path = f"/threads/{selected.thread_id}/search"
    headers = {"Authorization": "Bearer secret"}
    first = client.get(path, params={"q": "needle", "limit": 100}, headers=headers)
    assert first.status_code == 200
    page = first.json()
    assert page["total_matching_messages"] == 105
    assert len(page["results"]) == 100
    assert page["has_more"] is True
    second = client.get(path, params={"q": "needle", "before_cursor": page["next_cursor"]}, headers=headers).json()
    assert len(second["results"]) == 5
    assert second["total_matching_messages"] == 105
    assert second["has_more"] is False
    sequences = [row["sequence"] for row in page["results"] + second["results"]]
    assert len(set(sequences)) == 105
    assert all(row["thread_id"] == selected.thread_id for row in page["results"] + second["results"])
    oldest = client.get(f"/threads/{selected.thread_id}/transcript/{sequences[-1]}", headers=headers)
    assert oldest.json()["text"] == "Needle 0"
    assert "private tool" not in first.text


def test_search_bounds_unicode_literal_queries_archive_and_deleted_chat(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Selected", mode=RunMode.EDIT)
    app.state.storage.append_thread_event(
        thread_id=thread.thread_id, event_type="message.completed",
        payload={"role": "assistant", "text": "Straße %_ literal"},
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    path = f"/threads/{thread.thread_id}/search"
    assert client.get(path, params={"q": "STRASSE"}, headers=headers).json()["total_matching_messages"] == 1
    assert client.get(path, params={"q": "%_"}, headers=headers).json()["total_matching_messages"] == 1
    assert client.get(path, params={"q": "missing"}, headers=headers).json()["total_matching_messages"] == 0
    assert client.get(path, params={"q": "literal"}).status_code == 401
    for params in ({"q": " "}, {"q": "x" * 257}, {"q": "x", "limit": 101}, {"q": "x", "before_cursor": 0}):
        assert client.get(path, params=params, headers=headers).status_code == 422
    app.state.storage.archive_thread(thread.thread_id)
    assert client.get(path, params={"q": "literal"}, headers=headers).json()["total_matching_messages"] == 1
    app.state.storage.delete_thread(thread.thread_id)
    assert client.get(path, params={"q": "literal"}, headers=headers).status_code == 404


def test_search_includes_retained_events_evicted_from_global_index_and_latest_changes(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    store = storage.event_store
    store.max_transcript_messages = 32
    thread = storage.create_thread(title="Retained", mode=RunMode.EDIT)
    other = storage.create_thread(title="Other", mode=RunMode.EDIT)
    sequences = []
    for index in range(75):
        event = storage.append_thread_event(
            thread_id=thread.thread_id, event_type="message.created",
            payload={"role": "user", "text": f"needle {index}"},
        )
        sequences.append(event.sequence)
    storage.append_thread_event(
        thread_id=thread.thread_id, event_type="message.updated",
        payload={"message_sequence": sequences[0], "role": "user", "text": "needle edited oldest"},
    )
    storage.append_thread_event(
        thread_id=thread.thread_id, event_type="message.removed",
        payload={"message_sequence": sequences[1]},
    )
    storage.append_thread_event(
        thread_id=thread.thread_id, event_type="message.updated",
        payload={"message_sequence": sequences[2], "role": "user", "text": "no longer matches"},
    )
    # Evict the edited oldest message again without compacting its retained events.
    for index in range(40):
        storage.append_thread_event(
            thread_id=other.thread_id, event_type="message.completed",
            payload={"role": "assistant", "text": f"other {index}"},
        )
    assert store.search_transcript_messages(query="needle", thread_ids=(thread.thread_id,), before_cursor=None, limit=100) == []
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    path = f"/threads/{thread.thread_id}/search"
    first = client.get(path, params={"q": "needle", "limit": 50}, headers=headers).json()
    second = client.get(path, params={"q": "needle", "limit": 50, "before_cursor": first["next_cursor"]}, headers=headers).json()
    assert first["total_matching_messages"] == second["total_matching_messages"] == 73
    assert first["complete"] is True  # all retained messages, despite global eviction
    found = [row["sequence"] for row in first["results"] + second["results"]]
    assert found == sorted(set(found), reverse=True)
    assert sequences[0] in found
    assert sequences[1] not in found and sequences[2] not in found
    oldest = client.get(f"/threads/{thread.thread_id}/transcript/{sequences[0]}", headers=headers)
    assert oldest.json()["text"] == "needle edited oldest"
    assert client.get(f"/threads/{thread.thread_id}/transcript/{sequences[1]}", headers=headers).status_code == 404


def test_retained_search_excludes_credentials_private_events_and_invalid_edits(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    storage.event_store.max_transcript_messages = 1
    thread = storage.create_thread(title="Safe", mode=RunMode.EDIT)
    for event_type, payload in [
        ("message.created", {"role": "user", "text": "needle safe"}),
        ("message.completed", {"role": "assistant", "text": "needle password=" + "x" * 24}),
        ("codex.event", {"role": "assistant", "text": "needle private"}),
        ("message.updated", {"message_sequence": True, "role": "user", "text": "needle invalid"}),
        ("message.created", {"role": "tool", "text": "needle hidden"}),
        ("message.created", {"role": "assistant", "text": "filler"}),
    ]:
        storage.append_thread_event(thread_id=thread.thread_id, event_type=event_type, payload=payload)
    result = TestClient(app).get(
        f"/threads/{thread.thread_id}/search", params={"q": "needle"},
        headers={"Authorization": "Bearer secret"},
    ).json()
    assert result["total_matching_messages"] == 1
    assert result["results"][0]["excerpt"] == "needle safe"


def test_selected_search_sequence_cursor_survives_later_message_edit(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    thread = storage.create_thread(title="Stable", mode=RunMode.EDIT)
    for index in range(4):
        storage.append_thread_event(thread_id=thread.thread_id, event_type="message.created",
                                    payload={"role": "user", "text": f"needle {index}"})
    client = TestClient(app)
    path = f"/threads/{thread.thread_id}/search"
    headers = {"Authorization": "Bearer secret"}
    first = client.get(path, params={"q": "needle", "limit": 2}, headers=headers).json()
    storage.append_thread_event(thread_id=thread.thread_id, event_type="message.updated",
                                payload={"message_sequence": 2, "role": "user", "text": "needle changed"})
    second = client.get(path, params={"q": "needle", "limit": 2, "before_cursor": first["next_cursor"]}, headers=headers).json()
    assert [row["sequence"] for row in first["results"] + second["results"]] == [5, 4, 3, 2]
    assert second["results"][-1]["excerpt"] == "needle changed"


def test_unicode_excerpt_uses_original_offsets_after_casefold_expansion():
    from codex_bridge_service.routes.transcript_search import _excerpt

    source = "ß" * 200 + " TARGET " + "z" * 500
    assert "TARGET" in _excerpt(source, "target")


def test_search_budget_returns_retryable_failure_without_partial_count(tmp_path, monkeypatch):
    from codex_bridge_service.event_store import ConversationSearchBudgetError

    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Budget", mode=RunMode.EDIT)

    def exhausted(**kwargs):
        raise ConversationSearchBudgetError("budget exhausted")

    monkeypatch.setattr(app.state.storage.event_store, "search_conversation_messages", exhausted)
    response = TestClient(app).get(f"/threads/{thread.thread_id}/search?q=needle",
                                  headers={"Authorization": "Bearer secret"})
    assert response.status_code == 503
    assert "total_matching_messages" not in response.json()


def test_search_budget_interrupts_sql_and_releases_capacity(tmp_path, monkeypatch):
    import codex_bridge_service.event_store as event_module

    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    thread = storage.create_thread(title="Deadline", mode=RunMode.EDIT)
    for index in range(80):
        storage.append_thread_event(thread_id=thread.thread_id, event_type="message.created",
                                    payload={"role": "user", "text": f"needle {index}"})
    calls = 0

    def elapsed():
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 3.0

    monkeypatch.setattr(event_module, "monotonic", elapsed)
    client = TestClient(app)
    path = f"/threads/{thread.thread_id}/search?q=needle"
    headers = {"Authorization": "Bearer secret"}
    assert client.get(path, headers=headers).status_code == 503
    monkeypatch.setattr(event_module, "monotonic", lambda: 0.0)
    assert client.get(path, headers=headers).json()["total_matching_messages"] == 80
    semaphore = storage.event_store._conversation_search_capacity
    assert semaphore.acquire(blocking=False)
    assert semaphore.acquire(blocking=False)
    assert client.get(path, headers=headers).status_code == 503
    semaphore.release()
    semaphore.release()


def test_retained_union_survives_restart_and_activity_compaction(tmp_path):
    from codex_bridge_service.event_store import BridgeEventStore

    database = tmp_path / "retained.sqlite3"
    store = BridgeEventStore(database, max_journal_bytes=512 * 1024)
    for index in range(140):
        store.append(operation_key=f"message:{index}", scope="thread", thread_id="selected",
                     event_type="message.created", payload={"role": "user", "text": f"needle {index}"})
    assert store.transcript_index_status()["complete"] is False
    store.close()
    store = BridgeEventStore(database, max_journal_bytes=512 * 1024)
    rows, total = store.search_conversation_messages(query="needle", thread_id="selected", before_cursor=None, limit=150)
    assert total == len(rows) == 140
    assert store.get_transcript_message("selected", 1)["text"] == "needle 0"
    store.compact(scope="thread", thread_id="selected", through_cursor=140, snapshot_cursor=140)
    rows, total = store.search_conversation_messages(query="needle", thread_id="selected", before_cursor=None, limit=150)
    assert total == len(rows) == 128
    assert store.get_transcript_message("selected", 1) is None
    store.purge_thread("selected")
    assert store.search_conversation_messages(query="needle", thread_id="selected", before_cursor=None, limit=150) == ([], 0)
    store.close()


def test_interleaved_original_anchor_survives_edits_compaction_and_reopen(tmp_path):
    from codex_bridge_service.event_store import BridgeEventStore

    app = create_app(root_path=tmp_path, auth_token="secret")
    storage = app.state.storage
    other = storage.create_thread(title="Other", mode=RunMode.EDIT)
    selected = storage.create_thread(title="Selected", mode=RunMode.EDIT)
    storage.append_thread_event(thread_id=other.thread_id, event_type="message.created",
                                payload={"role": "user", "text": "unrelated"})
    message = storage.append_thread_event(thread_id=selected.thread_id, event_type="message.created",
                                         payload={"role": "user", "text": "needle original"})
    storage.append_thread_event(thread_id=selected.thread_id, event_type="message.updated",
                                payload={"message_sequence": message.sequence, "role": "user", "text": "needle edited"})
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    path = f"/threads/{selected.thread_id}/search?q=needle"
    row = client.get(path, headers=headers).json()["results"][0]
    assert row["sequence"] == message.sequence == 2
    assert row["anchor_cursor"] == 4
    assert row["revision_cursor"] == 5
    store = storage.event_store
    store.compact(scope="thread", thread_id=selected.thread_id, through_cursor=5, snapshot_cursor=5)
    database = store.path
    store.close()
    storage.event_store = BridgeEventStore(database)
    assert client.get(path, headers=headers).json()["results"][0] == row
    fetched = client.get(f"/threads/{selected.thread_id}/transcript/{message.sequence}", headers=headers).json()
    assert fetched["anchor_cursor"] == 4 and fetched["revision_cursor"] == 5
    assert fetched["text"] == "needle edited"


def test_legacy_anchor_upgrade_is_idempotent_and_does_not_guess_update_cursor(tmp_path):
    import sqlite3

    from codex_bridge_service.event_store import BridgeEventStore

    database = tmp_path / "legacy.sqlite3"
    store = BridgeEventStore(database)
    store.append(operation_key="other", scope="thread", thread_id="other",
                 event_type="message.created", payload={"role": "user", "text": "other"})
    original = store.append(operation_key="original", scope="thread", thread_id="selected",
                            event_type="message.created", payload={"role": "user", "text": "needle original"})
    edit = store.append(operation_key="edit", scope="thread", thread_id="selected",
                        event_type="message.updated", payload={"message_sequence": 1, "role": "user", "text": "needle edited"})
    store.close()
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE transcript_messages DROP COLUMN anchor_cursor")
    # Backfill can demonstrate the true creation cursor while the event remains.
    store = BridgeEventStore(database)
    assert store.get_transcript_message("selected", 1)["anchor_cursor"] == original.cursor
    store.compact(scope="thread", thread_id="selected", through_cursor=edit.cursor, snapshot_cursor=edit.cursor)
    store.close()
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE transcript_messages DROP COLUMN anchor_cursor")
    # An update-only indexed record provides no proof of the original anchor.
    for _ in range(2):
        store = BridgeEventStore(database)
        message = store.get_transcript_message("selected", 1)
        assert message["anchor_cursor"] is None
        assert message["cursor"] == edit.cursor
        rows, total = store.search_conversation_messages(query="needle", thread_id="selected", before_cursor=None, limit=10)
        assert total == 1 and rows[0]["anchor_cursor"] is None
        store.close()


def test_public_context_listing_reuses_full_retained_projection_after_index_exhaustion(tmp_path):
    from codex_bridge_service.event_store import BridgeEventStore

    store = BridgeEventStore(tmp_path / "context-list.sqlite3", max_journal_bytes=512 * 1024)
    store.append(operation_key="other", scope="thread", thread_id="other",
                 event_type="message.created", payload={"role": "user", "text": "other chat"})
    for index in range(140):
        store.append(operation_key=f"selected:{index}", scope="thread", thread_id="selected",
                     event_type="message.created", payload={"role": "user", "text": f"public {index}"})
    store.append(operation_key="removed", scope="thread", thread_id="selected",
                 event_type="message.removed", payload={"message_sequence": 2})
    store.append(operation_key="private", scope="thread", thread_id="selected",
                 event_type="codex.event", payload={"role": "assistant", "text": "private context"})
    store.append(operation_key="credential", scope="thread", thread_id="selected",
                 event_type="message.completed", payload={"role": "assistant", "text": "password=" + "x" * 24})
    indexed = store.search_transcript_messages(query="public", thread_ids=("selected",), before_cursor=None, limit=200)
    assert not any(row["scope_sequence"] == 1 for row in indexed)
    first, total, coverage = store.list_conversation_messages("selected", limit=75)
    second, second_total, second_coverage = store.list_conversation_messages(
        "selected", before_sequence=first[-1]["scope_sequence"], limit=75,
    )
    assert total == second_total == 139
    assert coverage == second_coverage == {"complete": True}
    rows = first + second
    assert len(rows) == 139
    assert [row["scope_sequence"] for row in rows] == sorted({row["scope_sequence"] for row in rows}, reverse=True)
    assert rows[-1]["text"] == "public 0"
    assert rows[-1]["anchor_cursor"] == rows[-1]["cursor"] == 2
    assert all(row["thread_id"] == "selected" and row["role"] == "user" for row in rows)
    for kwargs in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"before_sequence": 0}, {"before_sequence": True}):
        try:
            store.list_conversation_messages("selected", **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid internal page bound accepted")
    store.close()
