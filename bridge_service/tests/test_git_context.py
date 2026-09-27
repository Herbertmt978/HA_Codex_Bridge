"""Actual read-only repository context, independent of transcript labels."""

import subprocess

from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode


def git(workspace, *args):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(workspace), *args], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def repository(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="test-secret")
    thread = app.state.storage.create_thread(title="A misleading branch title", mode=RunMode.EDIT)
    workspace = app.state.storage.resolve_workspace_path(thread.workspace_path)
    git(workspace, "init", "-q", "-b", "main")
    (workspace / "example.txt").write_text("Initial\n", encoding="utf-8")
    git(workspace, "add", "example.txt")
    git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Initial")
    return app, thread, workspace, TestClient(app), {"Authorization": "Bearer test-secret"}


def test_context_refreshes_real_branch_dirty_and_base_without_writing(tmp_path):
    _, thread, workspace, client, headers = repository(tmp_path)
    path = f"/threads/{thread.thread_id}/git-context"
    index_before = (workspace / ".git/index").read_bytes()
    clean = client.get(path, params={"base_ref": "main"}, headers=headers)
    assert clean.status_code == 200
    assert clean.json() == {
        "repository": True, "workspace_path": workspace.relative_to(tmp_path).as_posix(),
        "repository_path": ".", "files": [], "files_truncated": False, "branch": "main", "detached": False,
        "dirty": False, "changed_files": 0,
        "base_name": "main", "base_available": True,
    }
    assert (workspace / ".git/index").read_bytes() == index_before
    git(workspace, "switch", "-qc", "actual-branch")
    (workspace / "example.txt").write_text("Changed\n", encoding="utf-8")
    (workspace / "new.txt").write_text("New\n", encoding="utf-8")
    changed = client.get(path, params={"base_ref": "missing-base"}, headers=headers).json()
    assert changed["branch"] == "actual-branch"
    assert changed["dirty"] is True
    assert changed["changed_files"] == 2
    assert changed["base_available"] is False
    git(workspace, "add", "example.txt")
    assert client.get(path, headers=headers).json()["changed_files"] == 2


def test_detached_and_non_git_are_explicit_and_separate_workspaces_do_not_bleed(tmp_path):
    app, thread, workspace, client, headers = repository(tmp_path)
    git(workspace, "checkout", "-q", "--detach", "HEAD")
    detached = client.get(f"/threads/{thread.thread_id}/git-context", headers=headers).json()
    assert detached["detached"] is True
    assert detached["branch"] is None
    other = app.state.storage.create_thread(title="main", mode=RunMode.EDIT)
    response = client.get(f"/threads/{other.thread_id}/git-context", headers=headers)
    assert response.status_code == 200
    assert response.json()["repository"] is False
    assert response.json()["dirty"] is None


def test_auth_and_external_metadata_fail_closed(tmp_path):
    _, thread, workspace, client, headers = repository(tmp_path)
    path = f"/threads/{thread.thread_id}/git-context"
    assert client.get(path).status_code == 401
    git(workspace, "status", "--short")
    git_dir = workspace / ".git"
    moved = workspace / "metadata"
    git_dir.rename(moved)
    git_dir.write_text(f"gitdir: {moved}\n", encoding="utf-8")
    result = client.get(path, headers=headers)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] in {"git_metadata_unsupported", "git_metadata_unsafe"}
    assert str(moved) not in result.text


def test_file_status_rename_and_independent_repository_identity(tmp_path):
    app, thread, workspace, client, headers = repository(tmp_path)
    git(workspace, "mv", "example.txt", "renamed.txt")
    (workspace / "renamed.txt").write_text("Changed\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("New\n", encoding="utf-8")
    result = client.get(f"/threads/{thread.thread_id}/git-context", headers=headers).json()
    records = {item["path"]: item for item in result["files"]}
    assert result["changed_files"] == 2
    assert records["renamed.txt"] == {"path": "renamed.txt", "status": "RM", "original_path": "example.txt"}
    assert records["untracked.txt"]["status"] == "??"
    other = app.state.storage.create_thread(title="Same name", mode=RunMode.EDIT)
    other_workspace = app.state.storage.resolve_workspace_path(other.workspace_path)
    git(other_workspace, "init", "-q", "-b", "other")
    other_result = client.get(f"/threads/{other.thread_id}/git-context", headers=headers).json()
    assert other_result["branch"] == "other"
    assert other_result["files"] == []
    assert other_result["workspace_path"] != result["workspace_path"]
    assert other_result["repository_path"] == result["repository_path"] == "."


def test_file_list_is_bounded_and_does_not_discover_nested_repository(tmp_path):
    _, thread, workspace, client, headers = repository(tmp_path)
    for index in range(205):
        (workspace / f"new-{index}.txt").write_text("New\n", encoding="utf-8")
    result = client.get(f"/threads/{thread.thread_id}/git-context", headers=headers).json()
    assert result["changed_files"] == 205
    assert len(result["files"]) == 200
    assert result["files_truncated"] is True
    # A nested repository is a file entry, never an independently discovered root.
    nested = workspace / "nested"
    nested.mkdir()
    git(nested, "init", "-q", "-b", "nested-branch")
    assert client.get(f"/threads/{thread.thread_id}/git-context", headers=headers).json()["branch"] == "main"


def test_status_rejects_control_text_without_exposing_private_root(tmp_path):
    _, thread, workspace, client, headers = repository(tmp_path)
    result = client.get(f"/threads/{thread.thread_id}/git-context", headers=headers)
    assert str(tmp_path) not in result.text
    result = client.get(f"/threads/{thread.thread_id}/git-context", params={"base_ref": "bad\nref"}, headers=headers)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "git_ref_invalid"


def test_context_path_rejects_controls_traversal_and_absolute_names():
    import pytest

    from codex_bridge_service.routes.git_review import GitReviewError, _context_path

    for value in ("bad\nname", "bad\tname", "bad\u202ename", "../other", "/private/file", "bad\\name"):
        with pytest.raises(GitReviewError):
            _context_path(value)


def test_untracked_directory_counts_actual_files(tmp_path):
    _, thread, workspace, client, headers = repository(tmp_path)
    directory = workspace / "new"
    directory.mkdir()
    (directory / "one.txt").write_text("One\n", encoding="utf-8")
    (directory / "two.txt").write_text("Two\n", encoding="utf-8")
    result = client.get(f"/threads/{thread.thread_id}/git-context", headers=headers).json()
    assert result["changed_files"] == 2
    assert {item["path"] for item in result["files"]} == {"new/one.txt", "new/two.txt"}
