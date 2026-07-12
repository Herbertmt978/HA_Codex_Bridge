from codex_bridge_service.codex_process import resolve_codex_home


def test_resolve_codex_home_prefers_bridge_override_then_standard_environment(
    tmp_path,
    monkeypatch,
) -> None:
    standard_home = tmp_path / "standard-codex-home"
    bridge_home = tmp_path / "bridge-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(standard_home))

    assert resolve_codex_home(None, "codex") == standard_home
    assert resolve_codex_home(str(bridge_home), "codex") == bridge_home


def test_resolve_codex_home_can_infer_home_from_sandbox_wrapper(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    wrapper = tmp_path / ".codex" / ".sandbox-bin" / "codex.exe"

    assert resolve_codex_home(None, str(wrapper)) == tmp_path / ".codex"
