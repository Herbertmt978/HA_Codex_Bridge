from codex_bridge_service.diagnostics import BridgeDiagnosticsProbe
from codex_bridge_service.storage import BridgeStorage


def test_diagnostics_probe_reports_runtime_and_tools(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    probe = BridgeDiagnosticsProbe(
        storage=storage,
        codex_command="python",
        tool_names=("python", "definitely-missing-codex-bridge-tool"),
        cache_seconds=0,
    )

    diagnostics = probe.probe()

    assert diagnostics.bridge_version
    assert diagnostics.python_version
    assert diagnostics.service_started_at
    assert diagnostics.service_uptime_seconds is not None
    assert diagnostics.codex_cli_version
    assert diagnostics.tools[0].name == "python"
    assert diagnostics.tools[0].available is True
    assert diagnostics.tools[1].available is False


def test_diagnostics_probe_surfaces_latest_thread_error(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    thread = storage.create_thread(title="Broken run", mode="full-auto")
    saved = storage.load_thread(thread.thread_id)
    saved.status = "error"
    saved.last_error = "Codex failed"
    storage.save_thread(saved)

    diagnostics = BridgeDiagnosticsProbe(storage=storage, tool_names=(), cache_seconds=0).probe()

    assert diagnostics.last_error == "Codex failed"
