import json

from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import DEFAULT_MODEL, DEFAULT_THINKING_LEVEL, CodexAccountRecord, RunRecord


class FakeRunner:
    def __init__(self, storage) -> None:
        self.storage = storage
        self.calls: list[tuple[str, str]] = []

    def submit_prompt(self, thread_id: str, prompt: str) -> RunRecord:
        self.calls.append((thread_id, prompt))
        record = self.storage.load_thread(thread_id)
        record.status = "running"
        record.active_run_id = "run_fake123"
        self.storage.save_thread(record)
        self.storage.append_thread_event(
            thread_id=thread_id,
            event_type="message.created",
            payload={
                "run_id": "run_fake123",
                "role": "user",
                "text": prompt,
            },
        )
        return RunRecord(
            run_id="run_fake123",
            thread_id=thread_id,
            status="running",
        )


class FakeAccountProbe:
    def probe(self) -> CodexAccountRecord:
        return CodexAccountRecord(
            available=True,
            auth_mode="chatgpt",
            email="person@example.com",
            name="Person Example",
            account_id="acc_123",
            plan_type="pro",
            organization_title="Personal",
            updated_at="2026-05-09T10:00:00Z",
        )


def test_health_project_create_and_status_require_token(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret", account_probe=FakeAccountProbe())
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.post("/projects", json={"name": "No token", "root_path": str(tmp_path / "nope")}).status_code == 401
    assert client.get("/status").status_code == 401

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "With token",
            "root_path": str(tmp_path / "projects" / "with-token"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    saved_path = tmp_path / "projects" / f"{payload['project_id']}.json"
    saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))

    assert payload["name"] == "With token"
    assert saved_path.exists()
    assert saved_payload["project_id"] == payload["project_id"]
    assert saved_payload["root_path"] == payload["root_path"]

    status_response = client.get(
        "/status",
        headers={"Authorization": "Bearer secret"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["models"][0] == DEFAULT_MODEL
    assert status_response.json()["thinking_levels"][2] == DEFAULT_THINKING_LEVEL
    assert status_response.json()["account"]["email"] == "person@example.com"
    assert status_response.json()["account"]["plan_type"] == "pro"


def test_project_routes_list_browse_create_folder_and_update(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "VM Work",
            "root_path": str(tmp_path / "vm-work"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    project_id = project_response.json()["project_id"]

    folder_response = client.post(
        "/projects/folders",
        headers={"Authorization": "Bearer secret"},
        json={
            "parent_path": str(tmp_path / "vm-work"),
            "folder_name": "notes",
        },
    )
    browse_response = client.get(
        f"/projects/browse?path={tmp_path / 'vm-work'}",
        headers={"Authorization": "Bearer secret"},
    )
    update_response = client.patch(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "VM Work Updated",
            "default_model": "gpt-5.5",
            "default_thinking_level": "high",
        },
    )
    list_response = client.get(
        "/projects",
        headers={"Authorization": "Bearer secret"},
    )

    assert folder_response.status_code == 201
    assert folder_response.json()["name"] == "notes"
    assert browse_response.status_code == 200
    assert browse_response.json()["path"] == str(tmp_path / "vm-work")
    assert browse_response.json()["directories"][0]["name"] == "notes"
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "VM Work Updated"
    assert update_response.json()["default_model"] == "gpt-5.5"
    assert list_response.status_code == 200
    assert any(project["project_id"] == project_id for project in list_response.json())


def test_project_archive_restore_and_delete_routes(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Disposable project",
            "root_path": str(tmp_path / "disposable"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    project_id = project_response.json()["project_id"]
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Child thread",
            "project_id": project_id,
            "mode": "full-auto",
        },
    )

    archive_response = client.post(
        f"/projects/{project_id}/archive",
        headers={"Authorization": "Bearer secret"},
    )
    restore_response = client.post(
        f"/projects/{project_id}/restore",
        headers={"Authorization": "Bearer secret"},
    )
    delete_response = client.delete(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
    )
    deleted_project_response = client.patch(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
        json={"name": "gone"},
    )
    deleted_thread_response = client.get(
        f"/threads/{thread_response.json()['thread_id']}",
        headers={"Authorization": "Bearer secret"},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["archived_at"] is not None
    assert restore_response.status_code == 200
    assert restore_response.json()["archived_at"] is None
    assert delete_response.status_code == 204
    assert deleted_project_response.status_code == 404
    assert deleted_thread_response.status_code == 404


def test_thread_create_upload_and_event_stream_require_token_and_persist(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Upload project",
            "root_path": str(tmp_path / "upload-project"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Upload target",
            "project_id": project_response.json()["project_id"],
            "mode": "full-auto",
        },
    )
    thread_payload = thread_response.json()
    thread_id = thread_payload["thread_id"]

    assert (
        client.post(
            f"/threads/{thread_id}/attachments",
            files={"file": ("notes.txt", b"blocked", "text/plain")},
        ).status_code
        == 401
    )
    assert client.get(f"/threads/{thread_id}/events").status_code == 401

    upload_response = client.post(
        f"/threads/{thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("../notes.txt", b"hello from api", "text/plain")},
    )

    assert upload_response.status_code == 201
    upload_payload = upload_response.json()

    saved_path = tmp_path / "threads" / f"{thread_id}.json"
    saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))
    attachment_path = tmp_path / "uploads" / thread_id / "notes.txt"

    assert thread_payload["project_name"] == "Upload project"
    assert thread_payload["effective_model"] == DEFAULT_MODEL
    assert thread_payload["effective_thinking_level"] == DEFAULT_THINKING_LEVEL
    assert upload_payload["filename"] == "notes.txt"
    assert upload_payload["mime_type"] == "text/plain"
    assert upload_payload["stored_path"] == str(attachment_path)
    assert attachment_path.read_bytes() == b"hello from api"
    assert saved_payload["attachments"][0]["attachment_id"] == upload_payload["attachment_id"]
    assert saved_payload["attachments"][0]["stored_path"] == str(attachment_path)

    events_response = client.get(
        f"/threads/{thread_id}/events",
        headers={"Authorization": "Bearer secret"},
    )

    assert events_response.status_code == 200
    assert events_response.headers["content-type"].startswith("text/event-stream")
    assert "event: thread.created" in events_response.text
    assert "event: attachment.added" in events_response.text
    assert '"filename": "notes.txt"' in events_response.text

    replay_response = client.get(
        f"/threads/{thread_id}/events?after=1",
        headers={"Authorization": "Bearer secret"},
    )

    assert replay_response.status_code == 200
    assert "event: thread.created" not in replay_response.text
    assert "event: attachment.added" in replay_response.text


def test_thread_listing_update_prompt_replay_and_artifact_download_routes(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Prompt project",
            "root_path": str(tmp_path / "prompt-project"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Prompt target",
            "project_id": project_response.json()["project_id"],
            "mode": "full-auto",
        },
    )
    thread_payload = thread_response.json()
    thread_id = thread_payload["thread_id"]

    list_response = client.get(
        "/threads",
        headers={"Authorization": "Bearer secret"},
    )
    get_response = client.get(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )
    update_response = client.patch(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
        json={
            "model_override": "gpt-5.5",
            "thinking_override": "high",
        },
    )

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert update_response.status_code == 200
    assert list_response.json()[0]["thread_id"] == thread_id
    assert get_response.json()["thread_id"] == thread_id
    assert update_response.json()["effective_model"] == "gpt-5.5"
    assert update_response.json()["effective_thinking_level"] == "high"

    assert client.post(
        f"/threads/{thread_id}/prompts",
        json={"prompt": "No token"},
    ).status_code == 401

    prompt_response = client.post(
        f"/threads/{thread_id}/prompts",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "Summarise the upload"},
    )

    assert prompt_response.status_code == 202
    assert prompt_response.json() == {
        "run_id": "run_fake123",
        "thread_id": thread_id,
        "status": "running",
    }

    replay_response = client.get(
        f"/threads/{thread_id}/events/replay?after=1",
        headers={"Authorization": "Bearer secret"},
    )

    assert replay_response.status_code == 200
    assert replay_response.json()[0]["event_type"] == "thread.updated"
    assert replay_response.json()[-1]["event_type"] == "message.created"
    assert replay_response.json()[-1]["payload"]["text"] == "Summarise the upload"

    artifact_path = tmp_path / "prompt-project" / "reply.md"
    artifact_path.write_text("hello", encoding="utf-8")
    artifacts = app.state.storage.sync_thread_artifacts(thread_id)

    artifacts_response = client.get(
        f"/threads/{thread_id}/artifacts",
        headers={"Authorization": "Bearer secret"},
    )
    archive_response = client.post(
        f"/threads/{thread_id}/artifacts/workspace-archive",
        headers={"Authorization": "Bearer secret"},
    )
    download_response = client.get(
        f"/threads/{thread_id}/artifacts/{artifacts[0].artifact_id}",
        headers={"Authorization": "Bearer secret"},
    )

    assert artifacts_response.status_code == 200
    assert artifacts_response.json()[0]["filename"] == "reply.md"
    assert archive_response.status_code == 201
    assert archive_response.json()["filename"].endswith(".zip")
    assert download_response.status_code == 200
    assert download_response.text == "hello"


def test_thread_archive_restore_delete_and_include_archived_routes(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Direct target",
            "mode": "full-auto",
        },
    )
    thread_id = thread_response.json()["thread_id"]

    archive_response = client.post(
        f"/threads/{thread_id}/archive",
        headers={"Authorization": "Bearer secret"},
    )
    active_response = client.get(
        "/threads",
        headers={"Authorization": "Bearer secret"},
    )
    archived_response = client.get(
        "/threads?include_archived=true",
        headers={"Authorization": "Bearer secret"},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["archived_at"] is not None
    assert active_response.json() == []
    assert archived_response.json()[0]["thread_id"] == thread_id

    restore_response = client.post(
        f"/threads/{thread_id}/restore",
        headers={"Authorization": "Bearer secret"},
    )
    delete_response = client.delete(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )
    get_deleted_response = client.get(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )

    assert restore_response.status_code == 200
    assert restore_response.json()["archived_at"] is None
    assert delete_response.status_code == 204
    assert get_deleted_response.status_code == 404


def test_thread_attachment_upload_accepts_relative_path(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Folder upload",
            "mode": "full-auto",
        },
    )
    thread_id = thread_response.json()["thread_id"]

    upload_response = client.post(
        f"/threads/{thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        data={"relative_path": "src/vba/Module1.bas"},
        files={"file": ("Module1.bas", b"Attribute VB_Name = \"Module1\"", "text/plain")},
    )

    assert upload_response.status_code == 201
    assert upload_response.json()["relative_path"] == "src/vba/Module1.bas"
