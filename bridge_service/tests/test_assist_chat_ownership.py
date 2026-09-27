"""HA-owned chats cannot re-enter native project configuration through menus."""

from pathlib import Path

import pytest

from codex_bridge_service.models import RunMode
from codex_bridge_service.runtime_broker import RuntimeThreadOperationConflictError
from codex_bridge_service.storage import BridgeStorage
from test_runtime_broker import ValidatorBackedAppServer, _broker


@pytest.mark.parametrize("operation", ["fork", "move"])
@pytest.mark.parametrize("selection", [None, [], ["home"]])
def test_assist_fork_and_move_refuse_before_native_dispatch(tmp_path: Path, operation, selection):
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(name="Assist", root_path=str(tmp_path / "assist"))
    destination = storage.create_project(name="Other", root_path=str(tmp_path / "other"))
    thread = storage.create_task_thread(
        action_id="a" * 32, fingerprint="b" * 64, title="HA conversation",
        project_id=project.project_id, mode=RunMode.OBSERVE,
        assist_origin=True, assist_mcp_servers=selection,
    )
    record = storage.load_thread(thread.thread_id)
    record.codex_thread_id = "provider-assist-source"
    storage.save_thread(record)
    peer = ValidatorBackedAppServer()
    broker = _broker(storage, peer, provider_admission_check=lambda: True)
    try:
        with pytest.raises(RuntimeThreadOperationConflictError):
            if operation == "fork":
                broker.fork_thread(thread.thread_id)
            else:
                broker.move_thread_project(thread.thread_id, destination.project_id, thread.navigation_revision)
        assert not any(method == "thread/fork" for method, _params in peer.requests)
        assert storage.load_thread(thread.thread_id).assist_mcp_servers == selection
        assert storage.load_thread(thread.thread_id).project_id == project.project_id
        assert broker.gate.snapshot().auth_mutation_active is False
    finally:
        broker.close()
