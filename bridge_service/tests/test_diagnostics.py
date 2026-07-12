import importlib.metadata

from codex_bridge_service import __version__
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


def test_diagnostics_bridge_version_identifies_loaded_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.4.19")
    probe = BridgeDiagnosticsProbe(
        storage=BridgeStorage(root_path=tmp_path / "bridge"),
        tool_names=(),
    )

    assert probe._bridge_version() == __version__


def test_diagnostics_probe_surfaces_latest_thread_error(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    thread = storage.create_thread(title="Broken run", mode="full-auto")
    saved = storage.load_thread(thread.thread_id)
    saved.status = "error"
    saved.last_error = "Codex failed"
    storage.save_thread(saved)

    diagnostics = BridgeDiagnosticsProbe(storage=storage, tool_names=(), cache_seconds=0).probe()

    assert diagnostics.last_error == "Codex failed"


def test_diagnostics_probe_clears_stale_error_after_newer_healthy_thread(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    broken = storage.create_thread(title="Broken run", mode="full-auto")
    saved = storage.load_thread(broken.thread_id)
    saved.status = "error"
    saved.last_error = "Codex failed"
    storage.save_thread(saved)
    storage.create_thread(title="Recovered run", mode="full-auto")

    diagnostics = BridgeDiagnosticsProbe(storage=storage, tool_names=(), cache_seconds=0).probe()

    assert diagnostics.last_error is None


def test_diagnostics_subprocesses_do_not_inherit_bridge_secrets(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")

    class Completed:
        stdout = "ok"
        stderr = ""
        returncode = 0

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return Completed()

    monkeypatch.setattr("codex_bridge_service.diagnostics.subprocess.run", fake_run)

    BridgeDiagnosticsProbe(
        storage=storage,
        codex_command="codex",
        tool_names=(),
        cache_seconds=0,
    ).probe()

    assert calls
    assert all(call.get("env") is not None for call in calls)
    assert all("CODEX_BRIDGE_AUTH_TOKEN" not in call["env"] for call in calls)
