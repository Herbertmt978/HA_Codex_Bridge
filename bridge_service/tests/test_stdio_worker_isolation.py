"""Proof and syscall checks for the separate stdio worker boundary."""

from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


LIBEXEC = (
    Path(__file__).resolve().parents[2]
    / "codex_bridge_app/rootfs/usr/local/libexec/codex-bridge"
)


@pytest.fixture
def worker_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(LIBEXEC))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "resource", SimpleNamespace())
    return importlib.import_module("stdio_worker"), importlib.import_module("stdio_sandbox")


def test_worker_admission_helper_loads_without_libexec_on_python_path(
    worker_modules, monkeypatch, tmp_path,
):
    worker, _ = worker_modules
    helper = tmp_path / "worker_admission.py"
    helper.write_text(
        "def acquire_worker_lease():\n    return 42\n"
        "def release_worker_lease(descriptor):\n    assert descriptor == 42\n",
        encoding="utf-8",
    )
    original_lstat = Path.lstat

    def root_owned_lstat(path):
        details = original_lstat(path)
        if path in (tmp_path, helper):
            return SimpleNamespace(
                st_mode=details.st_mode & ~0o022,
                st_uid=0,
                st_nlink=1,
            )
        return details

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)
    monkeypatch.setattr(worker, "ADMISSION_HELPER", helper)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != str(LIBEXEC)])
    worker._admission_module.cache_clear()
    try:
        assert worker._acquire_lease() == 42
        worker._release_lease(42)
    finally:
        worker._admission_module.cache_clear()


def test_worker_admission_helper_rejects_writable_file(worker_modules, monkeypatch, tmp_path):
    worker, _ = worker_modules
    helper = tmp_path / "worker_admission.py"
    helper.write_text("raise RuntimeError('untrusted helper ran')\n", encoding="utf-8")
    original_lstat = Path.lstat

    def unsafe_lstat(path):
        details = original_lstat(path)
        if path == tmp_path:
            return SimpleNamespace(st_mode=0o40755, st_uid=0)
        if path == helper:
            return SimpleNamespace(st_mode=0o100666, st_uid=0, st_nlink=1)
        return details

    monkeypatch.setattr(Path, "lstat", unsafe_lstat)
    monkeypatch.setattr(worker, "ADMISSION_HELPER", helper)
    worker._admission_module.cache_clear()
    try:
        with pytest.raises(worker.WorkerUnavailable, match="admission helper is unavailable"):
            worker._admission_module()
    finally:
        worker._admission_module.cache_clear()


def test_worker_accepts_only_a_verified_fixed_python_module(worker_modules, monkeypatch):
    worker, sandbox = worker_modules
    import codex_bridge_service.stdio_package_catalogue as catalogue

    approved = SimpleNamespace(
        package_path=Path("/opt/codex-stdio/packages/bridge-time/1.0.0"),
        entrypoint=("python", "-m", "codex_bridge_time"),
    )
    monkeypatch.setattr(catalogue, "verify_package", lambda *_: approved)
    worker._verify_catalogue("bridge-time", "1.0.0")
    assert sandbox._module_from_package("bridge-time", "1.0.0") == (
        approved.package_path,
        "codex_bridge_time",
    )
    for rejected in (
        ("/bin/sh", "-c", "id"),
        ("python", "-m", "../outside"),
        ("python", "-m", "codex_bridge_time", "--extra"),
    ):
        approved.entrypoint = rejected
        with pytest.raises(worker.WorkerUnavailable):
            worker._verify_catalogue("bridge-time", "1.0.0")
        with pytest.raises(RuntimeError):
            sandbox._module_from_package("bridge-time", "1.0.0")
    approved.entrypoint = ("python", "-m", "codex_bridge_time")
    approved.package_path = Path("/tmp/other")
    with pytest.raises(worker.WorkerUnavailable):
        worker._verify_catalogue("bridge-time", "1.0.0")
    with pytest.raises(RuntimeError):
        sandbox._module_from_package("bridge-time", "1.0.0")


def test_worker_rejects_incomplete_or_spoofed_isolation_proof(worker_modules, monkeypatch):
    worker, _ = worker_modules
    proof = {
        "nonce": "a" * 32,
        "checks": dict.fromkeys(worker.REQUIRED_CHECKS, True),
        "pid": 1,
        "hostname": "codex-stdio-worker",
    }
    worker._validate_proof(proof, "a" * 32)
    canary_proof = {**proof, "checks": {**proof["checks"], "parent_descriptor_absent": True}}
    worker._validate_proof(canary_proof, "a" * 32, expect_canary=True)
    with pytest.raises(worker.WorkerUnavailable):
        worker._validate_proof(canary_proof, "a" * 32)
    with pytest.raises(worker.WorkerUnavailable):
        worker._validate_proof(proof, "a" * 32, expect_canary=True)
    for change in (
        {"nonce": "b" * 32},
        {"checks": {"seccomp": False}},
        {"namespace_pids": [12, 1]},
        {"pid": 12},
        {"hostname": "host"},
    ):
        with pytest.raises(worker.WorkerUnavailable):
            worker._validate_proof({**proof, **change}, "a" * 32)


def test_bounded_proof_rejects_duplicate_keys(worker_modules):
    worker, _ = worker_modules
    with pytest.raises(worker.WorkerUnavailable):
        worker._strict_object(b'{"nonce":"a","nonce":"b"}')
    with pytest.raises(worker.WorkerUnavailable):
        worker._strict_object(b"{" + b"a" * 8192 + b"}")


def test_process_tree_uses_haos_compatible_parent_status(worker_modules, tmp_path):
    worker, _ = worker_modules
    for pid, parent in ((100, 1), (101, 100), (102, 101), (103, 1)):
        entry = tmp_path / str(pid)
        entry.mkdir()
        (entry / "status").write_text(f"Name:\tpython\nPPid:\t{parent}\n")
    assert worker._descendants(100, tmp_path) == {101, 102}


def test_worker_refuses_browser_peer_and_insufficient_memory(worker_modules, tmp_path):
    worker, _ = worker_modules
    proc = tmp_path / "proc"
    cgroup = tmp_path / "cgroup"
    proc.mkdir()
    cgroup.mkdir()
    (proc / "meminfo").write_text("MemAvailable: 1048576 kB\n")
    (cgroup / "memory.max").write_text("1073741824\n")
    (cgroup / "memory.current").write_text("0\n")
    entry = proc / "42"
    (entry / "attr").mkdir(parents=True)
    (entry / "attr/current").write_text("codex_bridge (enforce)\n")
    (entry / "cmdline").write_bytes(b"python\0other.py\0")
    worker._resource_headroom(proc, cgroup)
    (entry / "attr/current").write_text("codex_bridge//browser_bwrap (enforce)\n")
    with pytest.raises(worker.WorkerUnavailable, match="browser is active"):
        worker._resource_headroom(proc, cgroup)
    (entry / "attr/current").write_text("codex_bridge//stdio_bwrap (enforce)\n")
    with pytest.raises(worker.WorkerUnavailable, match="already active"):
        worker._resource_headroom(proc, cgroup)
    worker._resource_headroom(proc, cgroup, exclude={42})
    (proc / "meminfo").write_text("MemAvailable: 262144 kB\n")
    with pytest.raises(worker.WorkerUnavailable, match="memory headroom"):
        worker._resource_headroom(proc, cgroup, exclude={42})
    (proc / "meminfo").write_text("MemAvailable: 1048576 kB\n")
    (cgroup / "memory.current").write_text("805306368\n")
    with pytest.raises(worker.WorkerUnavailable, match="cgroup headroom"):
        worker._resource_headroom(proc, cgroup, exclude={42})


def test_failed_startup_reaps_the_worker_and_keeps_private_env_out(worker_modules, monkeypatch):
    worker, _ = worker_modules
    events = []

    class Helper:
        def lstat(self):
            return SimpleNamespace(st_mode=0o100755, st_uid=0)

        def __str__(self):
            return "/usr/local/libexec/codex-bridge/stdio_sandbox.py"

    class Process:
        pid = 1234

        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.exited = False

        def poll(self):
            return 0 if self.exited else None

        def wait(self, timeout):
            self.exited = True
            events.append("reaped")
            return 0

    process = Process()

    def start(command, **options):
        assert command == [
            worker.PYTHON,
            str(worker.SANDBOX_HELPER),
            "--nonce", "a" * 32,
            "--package-id", "bridge-time",
            "--revision", "1.0.0",
        ]
        assert options["close_fds"] and options["start_new_session"]
        assert "SUPERVISOR_TOKEN" not in options["env"]
        events.append("started")
        return process

    monkeypatch.setattr(worker, "attestation_ready", lambda: True)
    monkeypatch.setattr(worker, "_acquire_lease", lambda: events.append("leased") or 9)
    monkeypatch.setattr(worker, "_release_lease", lambda descriptor: events.append(f"released:{descriptor}"))
    monkeypatch.setattr(worker, "_resource_headroom", lambda **_: None)
    monkeypatch.setattr(worker, "_verify_catalogue", lambda *_: None)
    monkeypatch.setattr(worker, "SANDBOX_HELPER", Helper())
    monkeypatch.setattr(worker.secrets, "token_hex", lambda _: "a" * 32)
    monkeypatch.setattr(worker.subprocess, "Popen", start)
    monkeypatch.setattr(worker, "_read_bounded_line", lambda *_: b'{"nonce":"wrong"}\n')
    monkeypatch.setattr(worker, "_open_pidfd", lambda pid: events.append(f"opened:{pid}") or 77)
    monkeypatch.setattr(worker, "_signal_pidfd", lambda fd, sig: events.append(f"signalled:{fd}:{sig}"))
    monkeypatch.setattr(worker, "_pidfd_dead", lambda *_: True)
    monkeypatch.setattr(worker, "_group_empty", lambda *_: True)
    monkeypatch.setattr(worker, "_close_pidfd", lambda fd: events.append(f"closed:{fd}"))
    with pytest.raises(worker.WorkerUnavailable, match="isolation proof failed"):
        worker.start_worker("bridge-time", "1.0.0")
    assert events == [
        "leased", "started", "opened:1234", f"signalled:77:{worker.signal.SIGTERM}",
        "reaped", "closed:77", "released:9",
    ]
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed


def test_exited_leader_with_live_worker_keeps_admission_lease(worker_modules, monkeypatch):
    worker, _ = worker_modules
    events = []

    class ExitedLeader:
        pid = 4321
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self):
            return 0

    monkeypatch.setattr(worker, "_QUARANTINED_LEASES", [])
    monkeypatch.setattr(worker.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(worker, "_signal_pidfd", lambda fd, sig: events.append((fd, sig)))
    monkeypatch.setattr(worker, "_pidfd_dead", lambda fd, timeout=0: fd != 12)
    monkeypatch.setattr(worker, "_group_empty", lambda pgid: False)
    monkeypatch.setattr(worker, "_close_pidfd", lambda fd: events.append(("closed", fd)))
    monkeypatch.setattr(worker, "_release_lease", lambda fd: pytest.fail("released a live worker lease"))
    process = ExitedLeader()
    worker.WorkerProcess(
        process=process, proof={}, lease_fd=9, leader_pidfd=11, worker_pidfd=12,
    ).close()
    assert (12, worker.signal.SIGTERM) in events
    assert (12, worker.signal.SIGKILL) in events
    assert worker._QUARANTINED_LEASES == [9]
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed


def test_unaccounted_process_group_keeps_admission_lease(worker_modules, monkeypatch):
    worker, _ = worker_modules

    class ExitedLeader:
        pid = 4321
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self):
            return 0

    monkeypatch.setattr(worker, "_QUARANTINED_LEASES", [])
    monkeypatch.setattr(worker, "_group_empty", lambda pgid: False)
    monkeypatch.setattr(worker, "_release_lease", lambda fd: pytest.fail("released a live group lease"))
    worker.WorkerProcess(process=ExitedLeader(), proof={}, lease_fd=9).close()
    assert worker._QUARANTINED_LEASES == [9]


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux namespace launch")
def test_launch_uses_only_the_selected_read_only_package(worker_modules, monkeypatch, tmp_path):
    _, sandbox = worker_modules
    selected = Path("/opt/codex-stdio/packages/bridge-time/1.0.0")
    monkeypatch.setattr(sandbox, "_module_from_package", lambda *_: (selected, "codex_bridge_time"))
    monkeypatch.setattr(sandbox.ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(prctl=lambda *_: 0))
    monkeypatch.setattr(
        sandbox.os,
        "memfd_create",
        lambda *_args, **_kwargs: os.open(tmp_path / "filter", os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600),
        raising=False,
    )
    captured = []
    monkeypatch.setattr(sandbox.os, "execv", lambda path, args: captured.extend(args))
    sandbox.launch(
        nonce="a" * 32,
        probe=False,
        package_id="bridge-time",
        revision="1.0.0",
        canary_fd=None,
    )
    for required in (
        "--unshare-user", "--unshare-pid", "--as-pid-1", "--unshare-net", "--unshare-ipc",
        "--unshare-uts", "--hostname", "codex-stdio-worker",
        "--add-seccomp-fd", "--clearenv", "--remount-ro",
        "/proc", "/usr/local/bin/python3.14", "/package", "codex_bridge_time",
    ):
        assert required in captured
    assert captured.count(str(selected)) == 1
    assert "/data" not in captured and "/config" not in captured
    assert "/opt/codex-stdio/packages" not in captured
    assert "--proc" not in captured
    assert not any(captured[index:index + 3] == ["--bind", "/proc", "/proc"] for index in range(len(captured)))


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs")
def test_proc_alias_probe_rejects_openable_parent_or_sibling_descriptor(worker_modules):
    import stdio_probe

    descriptor = os.open("/dev/null", os.O_RDONLY)
    try:
        # An LSM path rule can miss a procfd symlink target. The probe must
        # detect an actually openable alias, not merely inspect its listing.
        assert not stdio_probe._inaccessible(f"/proc/self/fd/{descriptor}")
        assert stdio_probe._inaccessible(f"/proc/self/fd/{descriptor + 1000000}")
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux seccomp")
def test_second_filter_denies_socket_fork_and_exec(worker_modules):
    # This probes the kernel in a disposable child, not merely the BPF bytecode.
    script = """
import ctypes, errno, json, os, socket, stdio_sandbox
stdio_sandbox._install_exec_filter()
checks = {}
def sysv_ipc():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(29, 0x43424d43, 4096, 0o1666) == -1:
        raise OSError(ctypes.get_errno(), 'denied')
for name, action in (
    ('socket', lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
    ('fork', os.fork),
    ('exec', lambda: os.execv('/bin/true', ['/bin/true'])),
    ('sysv_ipc', sysv_ipc),
):
    try:
        action()
    except OSError as exc:
        checks[name] = exc.errno == errno.EPERM
    else:
        checks[name] = False
print(json.dumps(checks))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONPATH": str(LIBEXEC)},
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"socket": True, "fork": True, "exec": True, "sysv_ipc": True}
