"""Reject a browser whose observed kernel boundary differs from its proof."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def attester(monkeypatch):
    if not hasattr(__import__("os"), "getuid"):
        pytest.skip("root browser attestation is Linux-only")
    monkeypatch.syspath_prepend(
        str(
            Path(__file__).resolve().parents[2]
            / "codex_bridge_app/rootfs/usr/local/libexec/codex-bridge"
        )
    )
    return importlib.import_module("browser_attest")


def observed_process(tmp_path, **overrides):
    process = tmp_path / "10"
    (process / "attr").mkdir(parents=True)
    (process / "attr/current").write_text("test_app//browser_bwrap (enforce)\n")
    (process / "uid_map").write_text("100 100 1\n")
    fields = {
        "Uid": "100 100 100 100",
        "NoNewPrivs": "1",
        "Seccomp": "2",
        "Seccomp_filters": "2",
        "NSpid": "10 2",
    }
    fields.update(
        {
            name: "0000000000000000"
            for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        }
    )
    fields.update(overrides)
    (process / "status").write_text(
        "\n".join(f"{key}:\t{value}" for key, value in fields.items())
    )
    return process


@pytest.mark.parametrize(
    "changes",
    [
        {"Uid": "0 0 0 0"},
        {"NoNewPrivs": "0"},
        {"Seccomp": "0"},
        {"CapEff": "1"},
        {"CapPrm": "1"},
        {"CapAmb": "1"},
        {"CapInh": "1"},
        {"CapBnd": "1"},
        {"NSpid": "10"},
    ],
)
def test_rejects_missing_kernel_boundary(attester, tmp_path, changes):
    observed_process(tmp_path, **changes)
    with pytest.raises(AssertionError):
        attester.inspect_process(10, "test_app", 100, proc_root=tmp_path)


@pytest.mark.parametrize(
    "filename,value",
    [
        ("attr/current", "test_app (enforce)"),
        ("attr/current", "test_app//browser_bwrap (complain)"),
        ("uid_map", "0 0 65536"),
    ],
)
def test_rejects_wrong_profile_or_broad_uid_mapping(
    attester, tmp_path, filename, value
):
    process = observed_process(tmp_path)
    (process / filename).write_text(value)
    with pytest.raises(AssertionError):
        attester.inspect_process(10, "test_app", 100, proc_root=tmp_path)


def test_nested_renderer_bounding_set_does_not_grant_usable_capabilities(
    attester, tmp_path
):
    observed_process(tmp_path, CapBnd="ffffffff", NSpid="10 2 1", Seccomp_filters="3")
    result = attester.inspect_process(
        10, "test_app", 100, renderer=True, proc_root=tmp_path
    )
    assert result == {"namespace_pids": [10, 2, 1], "seccomp_filters": 3}
