"""Actual HA workspace/project confinement; central Linux lane only."""

import os

import pytest

from codex_bridge_service.chat_context import ChatContextError, ChatContextSelection, preview_chat_context
from codex_bridge_service.models import RuntimeProfile, RunMode
from codex_bridge_service.storage import BridgeStorage

pytestmark = pytest.mark.skipif(os.name == "nt", reason="real descriptor-confined HA workspace validation requires POSIX")


@pytest.mark.parametrize("target", ["source", "destination"])
def test_source_and_destination_reject_wrong_project_workspace(tmp_path, target):
    storage = BridgeStorage(root_path=tmp_path / "state", runtime_profile=RuntimeProfile.HOME_ASSISTANT, workspace_root=tmp_path / "workspaces")
    try:
        source_project = storage.create_project(name="Source", root_path="projects/source")
        destination_project = storage.create_project(name="Destination", root_path="projects/destination")
        source = storage.create_thread(title="Source", project_id=source_project.project_id, mode=RunMode.EDIT)
        destination = storage.create_thread(title="Destination", project_id=destination_project.project_id, mode=RunMode.EDIT)
        storage.append_thread_event(thread_id=source.thread_id, event_type="message.completed", payload={"role": "assistant", "text": "public source"})
        chosen = ChatContextSelection(source_thread_id=source.thread_id)
        assert preview_chat_context(storage, destination.thread_id, chosen).text.endswith("public source")
        victim = source if target == "source" else destination
        record = storage.load_thread(victim.thread_id)
        record.workspace_path = destination.workspace_path if target == "source" else source.workspace_path
        # Hostile on-disk metadata simulates corrupted/revoked project binding.
        storage._thread_path(victim.thread_id).write_text(record.model_dump_json(), encoding="utf-8")
        with pytest.raises(ChatContextError, match="inaccessible"):
            preview_chat_context(storage, destination.thread_id, chosen)
    finally:
        storage.event_store.close()


def test_source_workspace_symlink_does_not_expand_grant(tmp_path):
    storage = BridgeStorage(root_path=tmp_path / "state", runtime_profile=RuntimeProfile.HOME_ASSISTANT, workspace_root=tmp_path / "workspaces")
    try:
        project = storage.create_project(name="Source", root_path="projects/source")
        source = storage.create_thread(title="Source", project_id=project.project_id, mode=RunMode.EDIT)
        destination = storage.create_thread(title="Destination", mode=RunMode.EDIT)
        storage.append_thread_event(thread_id=source.thread_id, event_type="message.completed", payload={"role": "assistant", "text": "public source"})
        workspace = storage.resolve_workspace_path(source.workspace_path)
        moved = workspace.with_name("source-original")
        workspace.rename(moved)
        outside = tmp_path / "outside"
        outside.mkdir()
        workspace.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ChatContextError, match="inaccessible"):
            preview_chat_context(storage, destination.thread_id, ChatContextSelection(source_thread_id=source.thread_id))
    finally:
        storage.event_store.close()
