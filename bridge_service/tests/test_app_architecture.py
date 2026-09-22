"""Architecture selection must never mislabel or bypass the sandbox contract."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "codex_bridge_app/rootfs/usr/local/libexec/codex-bridge/runtime_architecture.py"


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location("app_runtime_architecture", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("machine,architecture", [("x86_64", "amd64"), ("aarch64", "aarch64")])
def test_runtime_uses_actual_machine_and_root_owned_contract(helper, monkeypatch, machine, architecture):
    monkeypatch.setattr(helper.os, "uname", lambda: SimpleNamespace(machine=machine), raising=False)
    def read(*, require_root):
        assert require_root is True
        return {"architecture": architecture}, b""
    monkeypatch.setattr(helper, "read_sandbox_contract", read)
    assert helper.runtime_architecture() == architecture


@pytest.mark.parametrize("machine,contract", [
    ("armv7l", ({"architecture": "aarch64"}, b"")),
    ("aarch64", ({"architecture": "amd64"}, b"")),
    ("x86_64", ({"architecture": "aarch64"}, b"")),
    ("aarch64", None),
])
def test_unknown_mismatched_or_missing_contract_fails_closed(helper, monkeypatch, machine, contract):
    monkeypatch.setattr(helper.os, "uname", lambda: SimpleNamespace(machine=machine), raising=False)
    monkeypatch.setattr(helper, "read_sandbox_contract", lambda **_: contract)
    with pytest.raises(RuntimeError, match="does not match"):
        helper.runtime_architecture()
