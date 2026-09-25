"""Chromium and stdio must share one process-lifetime resource admission."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux flock admission")


def _module():
    path = (Path(__file__).resolve().parents[2] / "codex_bridge_app" / "rootfs"
            / "usr" / "local" / "libexec" / "codex-bridge" / "worker_admission.py")
    spec = importlib.util.spec_from_file_location("worker_admission", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resource_lease_denies_overlap_and_releases_after_close(tmp_path, monkeypatch) -> None:
    admission = _module()
    monkeypatch.setattr(admission, "ROOT_UID", os.getuid())
    root = tmp_path / "worker"
    root.mkdir(mode=0o750)
    lease = root / "interactive-worker.lock"
    lease.touch(mode=0o640)
    lease.chmod(0o640)
    first = admission.acquire_worker_lease(lease)
    try:
        with pytest.raises(admission.WorkerLeaseUnavailable):
            admission.acquire_worker_lease(lease)
    finally:
        admission.release_worker_lease(first)
    second = admission.acquire_worker_lease(lease)
    admission.release_worker_lease(second)


def test_resource_lease_rejects_symlink(tmp_path, monkeypatch) -> None:
    admission = _module()
    monkeypatch.setattr(admission, "ROOT_UID", os.getuid())
    root = tmp_path / "worker"
    root.mkdir(mode=0o750)
    target = tmp_path / "target"
    target.write_text("", encoding="ascii")
    lease = root / "interactive-worker.lock"
    lease.symlink_to(target)
    with pytest.raises(admission.WorkerLeaseUnavailable):
        admission.acquire_worker_lease(lease)
