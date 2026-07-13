import importlib.metadata

from codex_bridge_service import __version__
from codex_bridge_service.build_info import BuildInfo
from codex_bridge_service.diagnostics import BridgeDiagnosticsProbe
from codex_bridge_service.models import RunMode, RuntimeProfile
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


def test_diagnostics_subprocesses_use_isolated_codex_environment(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path / "bridge")
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-realistic-secret-carrier")
    monkeypatch.setenv("HTTPS_PROXY", "https://user:secret@proxy.invalid:8443")

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
    assert all("OPENAI_API_KEY" not in call["env"] for call in calls)
    assert all("HTTPS_PROXY" not in call["env"] for call in calls)
    assert all("PATH" in call["env"] for call in calls)


def test_diagnostics_probe_reports_validated_build_information(tmp_path) -> None:
    build_info = BuildInfo(
        app_version="0.6.0",
        bridge_version="0.6.1",
        codex_version="0.144.1",
        image_revision="a" * 40,
        architecture="aarch64",
        release_lock_digest="b" * 64,
    )
    probe = BridgeDiagnosticsProbe(
        storage=BridgeStorage(root_path=tmp_path / "bridge"),
        build_info=build_info,
        tool_names=(),
        cache_seconds=60,
    )

    first = probe.probe()
    cached = probe.probe()

    assert first.app_version == "0.6.0"
    assert first.bridge_version == "0.6.1"
    assert first.api_current == 1
    assert first.api_minimum == 1
    assert first.api_maximum == 1
    assert first.bundled_codex_version == "0.144.1"
    assert first.image_revision == "a" * 40
    assert first.architecture == "aarch64"
    assert first.release_lock_digest == "b" * 64
    assert cached.app_version == first.app_version
    assert cached.bridge_version == first.bridge_version
    assert cached.bundled_codex_version == first.bundled_codex_version
    assert cached.image_revision == first.image_revision
    assert cached.architecture == first.architecture
    assert cached.release_lock_digest == first.release_lock_digest


def test_diagnostics_probe_falls_back_to_package_bridge_version(tmp_path) -> None:
    probe = BridgeDiagnosticsProbe(
        storage=BridgeStorage(root_path=tmp_path / "bridge"),
        build_info=BuildInfo(bridge_version="not-semver"),
        tool_names=(),
    )

    assert probe.probe().bridge_version == __version__


def test_home_assistant_diagnostics_redact_paths_errors_and_report_version_match(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = BridgeStorage(
        root_path=tmp_path / "bridge",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace,
    )
    original_profile = storage.runtime_profile
    storage.runtime_profile = RuntimeProfile.EXTERNAL_LEGACY
    try:
        project = storage.create_project(
            name="Diagnostics",
            root_path=str(workspace / "diagnostics"),
        )
        thread = storage.create_thread(
            title="Diagnostics",
            mode=RunMode.EDIT,
            project_id=project.project_id,
        )
        saved = storage.load_thread(thread.thread_id)
        saved.last_error = "Bearer private-secret /data/codex-home/auth.json"
        storage.save_thread(saved)
    finally:
        storage.runtime_profile = original_profile
    probe = BridgeDiagnosticsProbe(
        storage=storage,
        build_info=BuildInfo(codex_version="0.144.3"),
        runtime_version_provider=lambda: "0.144.3",
        tool_names=("python",),
        cache_seconds=0,
    )

    storage.runtime_profile = RuntimeProfile.EXTERNAL_LEGACY
    try:
        diagnostics = probe.probe()
    finally:
        storage.runtime_profile = original_profile
    serialized = diagnostics.model_dump_json()

    assert diagnostics.active_codex_version == "0.144.3"
    assert diagnostics.codex_version_match is True
    assert diagnostics.python_executable is None
    assert diagnostics.tools[0].available is True
    assert diagnostics.tools[0].path is None
    assert diagnostics.tools[0].version is None
    assert "private-secret" not in serialized
    assert "auth.json" not in serialized
    assert str(tmp_path) not in serialized
