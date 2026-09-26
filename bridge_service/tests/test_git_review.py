import subprocess
import sys
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    thread = app.state.storage.create_thread(title="Git review", mode=RunMode.EDIT)
    workspace = app.state.storage.resolve_workspace_path(thread.workspace_path)
    _git(workspace, "init", "-q")
    (workspace / "sample.txt").write_text("before\n", encoding="utf-8")
    _git(workspace, "add", "sample.txt")
    _git(workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test", "commit", "-qm", "initial")
    return app, thread, workspace, TestClient(app), {"Authorization": "Bearer secret"}


def test_git_review_separates_staged_unstaged_and_returns_actual_patch(tmp_path) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    file = workspace / "sample.txt"
    file.write_text("staged version\n", encoding="utf-8")
    _git(workspace, "add", "sample.txt")
    file.write_text("working version\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("new untracked content\n", encoding="utf-8")

    staged = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=staged", headers=headers
    )
    staged_patch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "staged", "path": "sample.txt", "expected_state_token": staged.json()["state_token"]},
        headers=headers,
    )
    unstaged = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )
    unstaged_patch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "sample.txt", "expected_state_token": unstaged.json()["state_token"]},
        headers=headers,
    )
    untracked_patch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "untracked.txt"},
        headers=headers,
    )

    assert staged.status_code == unstaged.status_code == 200
    assert [entry["path"] for entry in staged.json()["files"]] == ["sample.txt"]
    assert [entry["path"] for entry in unstaged.json()["files"]] == [
        "sample.txt", "untracked.txt"
    ]
    assert "+staged version" in staged_patch.json()["files"][0]["patch"]
    assert "+working version" in unstaged_patch.json()["files"][0]["patch"]
    assert "+new untracked content" in untracked_patch.json()["files"][0]["patch"]
    assert staged.json()["state_token"] == staged_patch.json()["state_token"]


def test_git_review_accepts_multiple_refs_independent_of_directory_order(tmp_path) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    oid = _git(workspace, "rev-parse", "HEAD")
    refs = workspace / ".git" / "refs" / "heads"
    for name in ("zebra", "alpha", "z"):
        (refs / name).write_text(f"{oid}\n", encoding="ascii")
    nested = refs / "team"
    nested.mkdir()
    (nested / "x").write_text(f"{oid}\n", encoding="ascii")

    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert response.status_code == 200
    assert "state_token" in response.json()


def test_git_review_commit_and_branch_scopes_resolve_real_commit_diffs(tmp_path) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    base = _git(workspace, "rev-parse", "HEAD")
    root = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "commit", "commit_ref": base, "path": "sample.txt"},
        headers=headers,
    )
    (workspace / "sample.txt").write_text("committed change\n", encoding="utf-8")
    _git(workspace, "add", "sample.txt")
    _git(workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test", "commit", "-qm", "feature")
    head = _git(workspace, "rev-parse", "HEAD")

    commit = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "commit", "commit_ref": head, "path": "sample.txt"},
        headers=headers,
    )
    branch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "branch", "base_ref": base},
        headers=headers,
    )

    assert root.status_code == commit.status_code == branch.status_code == 200
    assert root.json()["base_ref"] is None
    assert "+before" in root.json()["files"][0]["patch"]
    assert commit.json()["base_ref"] == base
    assert commit.json()["head_ref"] == head
    assert "+committed change" in commit.json()["files"][0]["patch"]
    assert [item["path"] for item in branch.json()["files"]] == ["sample.txt"]


def test_git_review_commit_scope_compares_merge_to_first_parent(tmp_path) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    main_branch = _git(workspace, "symbolic-ref", "--short", "HEAD")

    _git(workspace, "checkout", "-qb", "feature")
    (workspace / "feature.txt").write_text("feature change\n", encoding="utf-8")
    _git(workspace, "add", "feature.txt")
    _git(
        workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test",
        "commit", "-qm", "feature",
    )

    _git(workspace, "checkout", main_branch)
    (workspace / "main.txt").write_text("main change\n", encoding="utf-8")
    _git(workspace, "add", "main.txt")
    _git(
        workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test",
        "commit", "-qm", "main",
    )
    first_parent = _git(workspace, "rev-parse", "HEAD")
    _git(
        workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test",
        "merge", "--no-ff", "feature", "-m", "merge feature",
    )
    merge_commit = _git(workspace, "rev-parse", "HEAD")

    listing = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "commit", "commit_ref": merge_commit},
        headers=headers,
    )
    patch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "commit", "commit_ref": merge_commit, "path": "feature.txt"},
        headers=headers,
    )

    assert listing.status_code == patch.status_code == 200
    assert listing.json()["base_ref"] == first_parent
    assert listing.json()["head_ref"] == merge_commit
    assert [item["path"] for item in listing.json()["files"]] == ["feature.txt"]
    assert "+feature change" in patch.json()["files"][0]["patch"]


def test_git_review_labels_binary_and_large_diffs_and_detects_state_drift(tmp_path) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    (workspace / "binary.bin").write_bytes(b"old\x00data")
    _git(workspace, "add", "binary.bin")
    _git(workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test", "commit", "-qm", "binary")
    (workspace / "binary.bin").write_bytes(b"new\x00data")
    listing = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )
    binary = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "binary.bin"},
        headers=headers,
    )
    assert listing.status_code == binary.status_code == 200, binary.text
    assert binary.json()["files"][0]["binary"] is True
    assert binary.json()["files"][0]["patch"] is None

    (workspace / "sample.txt").write_text("x" * 100_000, encoding="utf-8")
    large = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "sample.txt"},
        headers=headers,
    )
    assert large.status_code == 200
    assert large.json()["files"][0]["large"] is True
    assert large.json()["files"][0]["patch_truncated"] is True

    stale = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "sample.txt", "expected_state_token": listing.json()["state_token"]},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "git_state_changed"


def test_git_review_rejects_external_git_directory_and_requires_auth(tmp_path) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    denied = client.get(f"/threads/{thread.thread_id}/git-review?scope=unstaged")
    assert denied.status_code == 401

    outside = tmp_path / "outside"
    outside.mkdir()
    _git(workspace, "init", "--separate-git-dir", str(outside / "gitdir"), "-q", str(workspace))
    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "git_metadata_unsupported"


def test_git_review_never_runs_repo_configured_helpers_or_attribute_filters(tmp_path) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    marker = tmp_path / "helper-ran.txt"
    helper = tmp_path / "helper.py"
    helper.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{helper}"'
    _git(workspace, "config", "core.fsmonitor", command)
    _git(workspace, "config", "filter.レビュー.clean", command)
    _git(workspace, "config", "filter.レビュー.required", "true")
    external_attributes = tmp_path / "attributes"
    external_attributes.write_text("sample.txt filter=レビュー\n", encoding="utf-8")
    _git(workspace, "config", "core.attributesFile", str(external_attributes))
    included = tmp_path / "included-git-config"
    _git(workspace, "config", "--file", str(included), "filter.included.clean", command)
    _git(workspace, "config", "--file", str(included), "filter.included.required", "true")
    _git(workspace, "config", "include.path", str(included))
    (workspace / ".gitattributes").write_text(
        "sample.txt filter=included\n", encoding="utf-8"
    )
    (workspace / "sample.txt").write_text("after helper guard\n", encoding="utf-8")

    result = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert result.status_code == 200
    assert marker.exists() is False


def test_git_review_snapshot_ignores_config_mutated_during_review(tmp_path, monkeypatch) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    marker = tmp_path / "race-helper-ran.txt"
    helper = tmp_path / "race-helper.py"
    helper.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{helper}"'
    (workspace / ".gitattributes").write_text(
        "sample.txt filter=race\n", encoding="utf-8"
    )
    _git(workspace, "config", "filter.race.clean", command)
    (workspace / "sample.txt").write_text("filtered control\n", encoding="utf-8")
    _git(workspace, "add", "sample.txt")
    assert marker.exists() is True
    marker.unlink()
    _git(workspace, "config", "--unset-all", "filter.race.clean")
    (workspace / ".git" / "config").write_text(
        "[core]\n\tbare = false\n", encoding="utf-8"
    )
    original = module._name_status
    snapshot_dirs = []

    def mutate_source_after_snapshot(repo, scope, base_ref, commit_ref):
        snapshot_dirs.append(module._GIT_DIR.get())
        _git(workspace, "config", "filter.race.clean", command)
        return original(repo, scope, base_ref, commit_ref)

    monkeypatch.setattr(module, "_name_status", mutate_source_after_snapshot)
    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert response.status_code == 200
    assert marker.exists() is False
    assert snapshot_dirs and snapshot_dirs[0] is not None
    assert snapshot_dirs[0].exists() is False


def test_git_review_rejects_ref_changes_during_snapshot(tmp_path, monkeypatch) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    def change_ref():
        branch = _git(workspace, "symbolic-ref", "--short", "HEAD")
        ref_path = workspace / ".git" / "refs" / "heads" / branch
        ref_path.write_text("0" * 40 + "\n", encoding="ascii")

    if os.name == "nt":
        original = module._copy_metadata_tree_path

        def mutate_ref(source, target, budget, deadline):
            result = original(source, target, budget, deadline)
            if source.name == "refs":
                change_ref()
            return result

        monkeypatch.setattr(module, "_copy_metadata_tree_path", mutate_ref)
    else:
        original = module._copy_metadata_tree

        def mutate_ref(source_fd, target_root, name, budget, deadline):
            result = original(source_fd, target_root, name, budget, deadline)
            if name == "refs":
                change_ref()
            return result

        monkeypatch.setattr(module, "_copy_metadata_tree", mutate_ref)
    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "git_state_changed"


def test_git_review_snapshot_preserves_source_index_timestamp(tmp_path) -> None:
    _app, _thread, workspace, _client, _headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    source_index = workspace / ".git" / "index"
    source_mtime_ns = source_index.stat().st_mtime_ns

    with module._private_git_snapshot(workspace):
        snapshot_index = module._GIT_DIR.get() / "index"
        assert snapshot_index.stat().st_mtime_ns == source_mtime_ns

    assert source_index.stat().st_mtime_ns == source_mtime_ns


def test_git_review_rejects_source_index_timestamp_change_during_snapshot(
    tmp_path, monkeypatch
) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    index = workspace / ".git" / "index"
    if os.name == "nt":
        original = module._copy_metadata_path

        def change_index_timestamp(source, target_root, name, budget, deadline, *, optional=False):
            result = original(
                source, target_root, name, budget, deadline, optional=optional
            )
            if name == "index":
                info = index.stat()
                os.utime(index, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return result

        monkeypatch.setattr(module, "_copy_metadata_path", change_index_timestamp)
    else:
        original = module._copy_metadata_file

        def change_index_timestamp(source_fd, target_root, name, budget, deadline, *, optional=False):
            result = original(
                source_fd, target_root, name, budget, deadline, optional=optional
            )
            if name == "index":
                info = index.stat()
                os.utime(index, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return result

        monkeypatch.setattr(module, "_copy_metadata_file", change_index_timestamp)

    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "git_state_changed"


def test_git_review_repeated_reads_keep_same_size_racy_change(tmp_path, monkeypatch) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    binary = workspace / "binary.bin"
    fixed_ns = 1_700_000_000_000_000_000
    binary.write_bytes(b"old\0data")
    os.utime(binary, ns=(fixed_ns, fixed_ns))
    _git(workspace, "add", "binary.bin")
    _git(
        workspace, "-c", "user.name=Reviewer", "-c", "user.email=reviewer@example.test",
        "commit", "-qm", "binary",
    )
    index = workspace / ".git" / "index"
    os.utime(index, ns=(fixed_ns, fixed_ns))
    binary.write_bytes(b"new\0data")
    os.utime(binary, ns=(fixed_ns, fixed_ns))

    original_prefix = module._git_prefix

    def use_racy_stat_control(repo):
        return original_prefix(repo) + [
            "-c", "core.checkStat=minimal", "-c", "core.trustctime=false",
        ]

    monkeypatch.setattr(module, "_git_prefix", use_racy_stat_control)
    listing = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )
    patch = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "binary.bin"},
        headers=headers,
    )

    assert listing.status_code == patch.status_code == 200
    assert "binary.bin" in [item["path"] for item in listing.json()["files"]]
    assert patch.json()["files"][0]["binary"] is True


def test_git_review_never_imports_alternates_added_during_snapshot(tmp_path, monkeypatch) -> None:
    app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    alternate_repo = tmp_path / "alternate-repo"
    alternate_repo.mkdir()
    _git(alternate_repo, "init", "-q")
    (workspace / ".git" / "objects" / "info").mkdir(exist_ok=True)
    if os.name == "nt":
        original = module._copy_metadata_tree_path

        def race_alternates(source, target, budget, deadline):
            if source.name == "objects":
                (workspace / ".git" / "objects" / "info" / "alternates").write_text(
                    str(alternate_repo / ".git" / "objects"), encoding="utf-8"
                )
            result = original(source, target, budget, deadline)
            if source.name == "objects":
                assert not (target / "info").exists()
            return result

        monkeypatch.setattr(module, "_copy_metadata_tree_path", race_alternates)
    else:
        original = module._copy_metadata_tree

        def race_alternates(source_fd, target_root, name, budget, deadline):
            if name == "objects":
                (workspace / ".git" / "objects" / "info" / "alternates").write_text(
                    str(alternate_repo / ".git" / "objects"), encoding="utf-8"
                )
            result = original(source_fd, target_root, name, budget, deadline)
            if name == "objects":
                assert not (target_root / "objects" / "info").exists()
            return result

        monkeypatch.setattr(module, "_copy_metadata_tree", race_alternates)
    response = client.get(
        f"/threads/{thread.thread_id}/git-review?scope=unstaged", headers=headers
    )

    assert response.status_code == 200
    assert (module._GIT_DIR.get()) is None


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX descriptor-anchored worktree paths")
def test_git_review_stays_on_opened_workspace_if_path_is_replaced_during_review(
    tmp_path, monkeypatch
) -> None:
    _app, thread, workspace, client, headers = _repo(tmp_path)
    module = importlib.import_module("codex_bridge_service.routes.git_review")
    (workspace / "sample.txt").write_text("inside authorised change\n", encoding="utf-8")
    outside = tmp_path / "replacement"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside secret", encoding="utf-8")
    original = module._name_status

    def replace_path(repo, scope, base_ref, commit_ref):
        moved = workspace.with_name(workspace.name + "-held")
        workspace.rename(moved)
        workspace.symlink_to(outside, target_is_directory=True)
        return original(repo, scope, base_ref, commit_ref)

    monkeypatch.setattr(module, "_name_status", replace_path)
    response = client.get(
        f"/threads/{thread.thread_id}/git-review",
        params={"scope": "unstaged", "path": "sample.txt"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["path"] == "sample.txt"
    assert "+inside authorised change" in response.json()["files"][0]["patch"]
    assert "outside.txt" not in response.text
    assert "outside secret" not in response.text
