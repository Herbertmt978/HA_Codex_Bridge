#!/usr/local/bin/python
"""Private launcher for an attested, approved stdio MCP package revision.

The Bridge passes only catalogue identifiers. Package verification and the
root-created boot proof are both required before a worker can be returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import stat
import subprocess
import time


ATTESTATION_PATH = Path("/run/codex-bridge/stdio-worker-attestation.json")
SANDBOX_HELPER = Path("/usr/local/libexec/codex-bridge/stdio_sandbox.py")
PYTHON = "/usr/local/bin/python3.14"
PACKAGE_ROOT = Path("/opt/codex-stdio/packages")
ADMISSION_HELPER = Path("/usr/local/libexec/codex-bridge/worker_admission.py")
PROTOCOL = "stdio-worker-v2"
MAX_PROOF_BYTES = 8192
STARTUP_SECONDS = 12
MIN_AVAILABLE_MEMORY = 512 * 1024 * 1024
PACKAGE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
REVISION = re.compile(r"[0-9][A-Za-z0-9._-]{0,63}")
REQUIRED_CHECKS = {
    "non_root", "no_new_privileges", "seccomp",
    "capabilities_dropped", "procfs_absent", "isolated_hostname",
    "private_environment_absent", "only_stdio_descriptors",
    "app_data_absent", "ha_config_absent", "codex_home_absent",
    "bridge_run_absent", "sibling_packages_absent", "root_read_only",
    "tcp_denied", "ipv6_denied", "udp_denied",
    "unix_socket_denied", "fork_denied", "exec_denied",
    "mount_denied", "setns_denied", "ptrace_denied", "sysv_ipc_denied",
    "parent_environment_denied", "parent_descriptor_alias_denied",
    "parent_root_denied",
}


class WorkerUnavailable(RuntimeError):
    """The approved worker could not meet its startup boundary."""


# A process that could not be reaped keeps the App-wide resource lease until
# Bridge exits. Starting another large worker in that state would be unsafe.
_QUARANTINED_LEASES: list[int] = []


def _pidfd_dead(descriptor: int, timeout: float = 0) -> bool:
    return bool(select.select([descriptor], [], [], timeout)[0])


def _signal_pidfd(descriptor: int, number: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, number)
    except ProcessLookupError:
        pass


def _group_empty(pgid: int) -> bool:
    # Signal 0 changes no process. ESRCH is the kernel's proof that no member
    # of the original group remains; a reused group ID keeps the lease held.
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _open_pidfd(pid: int) -> int:
    return os.pidfd_open(pid)


def _close_pidfd(descriptor: int) -> None:
    os.close(descriptor)


@lru_cache(maxsize=1)
def _admission_module() -> object:
    """Load the fixed sibling without depending on the caller's import path."""
    parent = ADMISSION_HELPER.parent.lstat()
    metadata = ADMISSION_HELPER.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o022
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise WorkerUnavailable("stdio worker admission helper is unavailable")
    spec = importlib.util.spec_from_file_location("codex_bridge_worker_admission", ADMISSION_HELPER)
    if spec is None or spec.loader is None:
        raise WorkerUnavailable("stdio worker admission helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _acquire_lease() -> int:
    return _admission_module().acquire_worker_lease()


def _release_lease(descriptor: int) -> None:
    _admission_module().release_worker_lease(descriptor)


@dataclass
class WorkerProcess:
    process: subprocess.Popen[bytes]
    proof: dict[str, object]
    lease_fd: int | None = None
    leader_pidfd: int | None = None
    worker_pidfd: int | None = None

    def close(self) -> None:
        """Stop and reap the isolated process group, including descendants."""
        try:
            process = self.process
            for descriptor in (self.worker_pidfd, self.leader_pidfd):
                if descriptor is not None:
                    _signal_pidfd(descriptor, signal.SIGTERM)
            try:
                if process.poll() is None:
                    process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if self.worker_pidfd is not None and not _pidfd_dead(self.worker_pidfd, 2):
                _signal_pidfd(self.worker_pidfd, signal.SIGKILL)
            if self.leader_pidfd is not None and not _pidfd_dead(self.leader_pidfd):
                _signal_pidfd(self.leader_pidfd, signal.SIGKILL)
            try:
                if process.poll() is None:
                    process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if self.worker_pidfd is not None:
                _pidfd_dead(self.worker_pidfd, 3)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        finally:
            safe_to_release = (
                process.poll() is not None
                and (self.worker_pidfd is None or _pidfd_dead(self.worker_pidfd))
                and _group_empty(process.pid)
            )
            for descriptor in (self.worker_pidfd, self.leader_pidfd):
                if descriptor is not None:
                    _close_pidfd(descriptor)
            self.worker_pidfd = None
            self.leader_pidfd = None
            descriptor, self.lease_fd = self.lease_fd, None
            if descriptor is not None:
                if not safe_to_release:
                    _QUARANTINED_LEASES.append(descriptor)
                else:
                    _release_lease(descriptor)


def _read_bounded_line(stream: object, *, timeout: float = STARTUP_SECONDS) -> bytes:
    descriptor = stream.fileno()
    deadline = time.monotonic() + timeout
    result = bytearray()
    while not result.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
            raise WorkerUnavailable("stdio worker startup timed out")
        chunk = os.read(descriptor, 1)
        if not chunk or len(result) >= MAX_PROOF_BYTES:
            raise WorkerUnavailable("stdio worker proof is invalid")
        result.extend(chunk)
    return bytes(result)


def _strict_object(payload: bytes) -> dict[str, object]:
    if len(payload) > MAX_PROOF_BYTES:
        raise WorkerUnavailable("stdio worker proof is invalid")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise WorkerUnavailable("stdio worker proof is invalid")
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerUnavailable("stdio worker proof is invalid") from exc
    if not isinstance(document, dict):
        raise WorkerUnavailable("stdio worker proof is invalid")
    return document


def attestation_ready(path: Path = ATTESTATION_PATH) -> bool:
    """Read only a root-owned proof from this boot, without following links."""
    descriptor = -1
    try:
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or parent.st_mode & 0o022
        ):
            return False
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or not 1 <= metadata.st_size <= 4096
        ):
            return False
        payload = os.read(descriptor, metadata.st_size + 1)
        if len(payload) != metadata.st_size or os.read(descriptor, 1):
            return False
        document = _strict_object(payload)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, WorkerUnavailable):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return document == {
        "schema_version": 1,
        "worker_protocol": PROTOCOL,
        "architecture": "amd64",
        "boot_id": boot_id,
        "isolation": "ready",
    }


def _verify_catalogue(package_id: str, revision: str) -> None:
    if not PACKAGE_ID.fullmatch(package_id) or not REVISION.fullmatch(revision):
        raise WorkerUnavailable("invalid stdio package selection")
    from codex_bridge_service.stdio_package_catalogue import verify_package

    package = verify_package(package_id, revision)
    path = Path(package.package_path)
    if not path.is_relative_to(PACKAGE_ROOT) or path == PACKAGE_ROOT:
        raise WorkerUnavailable("package outside fixed catalogue")
    entrypoint = tuple(package.entrypoint)
    if (
        len(entrypoint) != 3
        or entrypoint[:2] != ("python", "-m")
        or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", entrypoint[2])
    ):
        raise WorkerUnavailable("invalid approved entrypoint")


def _resource_headroom(
    proc: Path = Path("/proc"), cgroup: Path = Path("/sys/fs/cgroup"),
    exclude: set[int] | None = None,
) -> None:
    """Refuse a worker while a sandbox peer or tight memory is visible."""
    excluded = exclude or set()
    entries = list(proc.glob("[0-9]*"))
    if len(entries) > 4096:
        raise WorkerUnavailable("stdio process visibility limit exceeded")
    for entry in entries:
        if int(entry.name) in excluded:
            continue
        try:
            profile = (entry / "attr/current").read_text().strip()
            command = (entry / "cmdline").read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WorkerUnavailable("stdio resource state is unreadable") from exc
        if (
            profile.endswith("//browser_bwrap (enforce)")
            or b"/usr/local/libexec/codex-bridge/browser_sandbox.py\0" in command
            or b"/opt/codex-browser/bin/bwrap\0" in command
        ):
            raise WorkerUnavailable("stdio worker unavailable while browser is active")
        if (
            profile.endswith("//stdio_bwrap (enforce)")
            or b"/usr/local/libexec/codex-bridge/stdio_sandbox.py\0" in command
            or b"/opt/codex-stdio/bin/bwrap\0" in command
        ):
            raise WorkerUnavailable("another stdio worker is already active")
    try:
        memory = (proc / "meminfo").read_text().splitlines()
        available = next(
            int(line.split()[1]) * 1024 for line in memory
            if line.startswith("MemAvailable:")
        )
        if available < MIN_AVAILABLE_MEMORY:
            raise WorkerUnavailable("stdio worker memory headroom is insufficient")
        maximum = (cgroup / "memory.max").read_text().strip()
        if maximum != "max":
            current = int((cgroup / "memory.current").read_text().strip())
            if int(maximum) - current < MIN_AVAILABLE_MEMORY:
                raise WorkerUnavailable("stdio worker cgroup headroom is insufficient")
    except (OSError, StopIteration, ValueError) as exc:
        raise WorkerUnavailable("stdio memory headroom is unavailable") from exc


def _validate_proof(
    proof: dict[str, object], nonce: str, *, expect_canary: bool = False,
) -> None:
    checks = proof.get("checks")
    required_checks = REQUIRED_CHECKS | ({"parent_descriptor_absent"} if expect_canary else set())
    if (
        set(proof) != {"nonce", "checks", "pid", "hostname"}
        or proof.get("nonce") != nonce
        or not isinstance(checks, dict)
        or set(checks) != required_checks
        or not all(value is True for value in checks.values())
        or proof.get("pid") != 1
        or proof.get("hostname") != "codex-stdio-worker"
    ):
        raise WorkerUnavailable("stdio worker isolation proof failed")


def _descendants(parent_pid: int, proc: Path = Path("/proc")) -> set[int]:
    # HAOS omits /proc/PID/task/TID/children. PPid in status also includes
    # children created by non-leader threads.
    entries = list(proc.glob("[0-9]*"))
    if len(entries) > 4096:
        raise WorkerUnavailable("stdio process visibility limit exceeded")
    children: dict[int, list[int]] = {}
    for entry in entries:
        try:
            lines = (entry / "status").read_text().splitlines()
        except FileNotFoundError:
            continue
        parents = [int(line.split()[1]) for line in lines if line.startswith("PPid:")]
        if len(parents) != 1:
            raise WorkerUnavailable("stdio process ancestry is unavailable")
        children.setdefault(parents[0], []).append(int(entry.name))
    found: set[int] = set()
    queue = [parent_pid]
    while queue:
        parent = queue.pop()
        for pid in children.get(parent, []):
            if pid not in found:
                found.add(pid)
                queue.append(pid)
        if len(found) > 64:
            raise WorkerUnavailable("stdio launcher tree exceeded bound")
    return found


def _private_pid_one(descendants: set[int]) -> int:
    candidates = []
    for pid in descendants:
        try:
            lines = (Path("/proc") / str(pid) / "status").read_text().splitlines()
            namespace_line = next(line for line in lines if line.startswith("NSpid:"))
            pids = [int(part) for part in namespace_line.split()[1:]]
        except (OSError, StopIteration, ValueError):
            continue
        if len(pids) >= 2 and pids[0] == pid and pids[-1] == 1:
            candidates.append(pid)
    if len(candidates) != 1:
        raise WorkerUnavailable("stdio private PID 1 is not unique")
    return candidates[0]


def _verify_external(
    pid: int, parent_profile: str, uid: int, gid: int, proof: dict[str, object],
) -> None:
    """Check the worker from the parent's procfs, which is not mounted inside."""
    proc = Path("/proc") / str(pid)
    status = dict(
        line.split(":", 1)
        for line in (proc / "status").read_text().splitlines()
        if ":" in line
    )
    profile = (proc / "attr/current").read_text().strip()
    if profile != parent_profile + "//stdio_bwrap (enforce)":
        raise WorkerUnavailable("stdio child AppArmor profile is unavailable")
    if not all(int(value) == uid for value in status["Uid"].split()):
        raise WorkerUnavailable("stdio worker identity changed")
    if not all(int(value) == gid for value in status["Gid"].split()):
        raise WorkerUnavailable("stdio worker group changed")
    if (
        status["NoNewPrivs"].strip() != "1"
        or status["Seccomp"].strip() != "2"
        or int(status["Seccomp_filters"]) < 2
        or any(
            int(status[name].strip(), 16) != 0
            for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        )
    ):
        raise WorkerUnavailable("stdio worker syscall boundary is unavailable")
    outer_pids = [int(part) for part in status["NSpid"].split()]
    if len(outer_pids) < 2 or outer_pids[0] != pid or outer_pids[-1] != 1:
        raise WorkerUnavailable("stdio worker PID namespace is unavailable")
    for name, identity in (("uid_map", uid), ("gid_map", gid)):
        child_map = (proc / name).read_text().strip().splitlines()
        parent_map = (Path("/proc/self") / name).read_text().strip().splitlines()
        if (
            child_map == parent_map
            or len(child_map) != 1
            or [int(part) for part in child_map[0].split()] != [identity, identity, 1]
        ):
            raise WorkerUnavailable("stdio worker user namespace is unavailable")
    if proof["hostname"] == socket.gethostname():
        raise WorkerUnavailable("stdio worker UTS namespace is unavailable")
    interfaces = {
        line.split(":", 1)[0].strip()
        for line in (proc / "net/dev").read_text().splitlines()[2:]
        if ":" in line
    }
    if interfaces != {"lo"}:
        raise WorkerUnavailable("stdio worker network namespace is unavailable")
    if {entry.name for entry in (proc / "fd").iterdir()} != {"0", "1", "2"}:
        raise WorkerUnavailable("stdio worker inherited a descriptor")
    mounts: dict[str, tuple[set[str], str]] = {}
    for line in (proc / "mountinfo").read_text().splitlines():
        left, _, right = line.partition(" - ")
        fields = left.split()
        mounts[fields[4]] = (set(fields[5].split(",")), right.split()[0])
    if not all(
        "ro" in mounts.get(path, (set(), ""))[0]
        for path in ("/", "/usr/local/lib", "/usr/share/zoneinfo", "/package")
    ):
        raise WorkerUnavailable("stdio runtime mounts are not read-only")
    if any(path == "/proc" or path.startswith("/proc/") for path in mounts):
        raise WorkerUnavailable("stdio procfs was mounted inside the worker")
    if not (proc / "limits").read_text().count("Max core file size") == 1:
        raise WorkerUnavailable("stdio limits are unavailable")


def start_worker(package_id: str, revision: str) -> WorkerProcess:
    """Start one verified module and return its MCP stdin/stdout/stderr pipes.

    No path, command, argument or environment is accepted from a caller. The
    helper emits a separate bounded proof before any package code is imported.
    A failed launch is always killed and reaped before this function raises.
    """
    if not attestation_ready():
        raise WorkerUnavailable("stdio worker isolation is unavailable")
    lease_fd: int | None = None
    process: subprocess.Popen[bytes] | None = None
    leader_pidfd: int | None = None
    worker_pidfd: int | None = None
    try:
        lease_fd = _acquire_lease()
        _resource_headroom()
        _verify_catalogue(package_id, revision)
        metadata = SANDBOX_HELPER.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            raise WorkerUnavailable("stdio worker launcher is unavailable")
        nonce = secrets.token_hex(16)
        process = subprocess.Popen(
            [
                PYTHON,
                str(SANDBOX_HELPER),
                "--nonce", nonce,
                "--package-id", package_id,
                "--revision", revision,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        leader_pidfd = _open_pidfd(process.pid)
        proof = _strict_object(_read_bounded_line(process.stdout))
        _validate_proof(proof, nonce)
        parent_profile = Path("/proc/self/attr/current").read_text().strip()
        if not parent_profile.endswith(" (enforce)") or "//" in parent_profile:
            raise WorkerUnavailable("stdio parent AppArmor profile is unavailable")
        descendants = _descendants(process.pid)
        pid = _private_pid_one(descendants)
        worker_pidfd = _open_pidfd(pid)
        _verify_external(
            pid, parent_profile.removesuffix(" (enforce)"),
            os.getuid(), os.getgid(), proof,
        )
        _resource_headroom(exclude=descendants | {process.pid})
        if process.poll() is not None:
            raise WorkerUnavailable("stdio worker exited during startup")
        return WorkerProcess(
            process=process, proof=proof, lease_fd=lease_fd,
            leader_pidfd=leader_pidfd, worker_pidfd=worker_pidfd,
        )
    except BaseException as exc:
        if process is not None:
            WorkerProcess(
                process=process, proof={}, lease_fd=lease_fd,
                leader_pidfd=leader_pidfd, worker_pidfd=worker_pidfd,
            ).close()
        elif lease_fd is not None:
            _release_lease(lease_fd)
        if isinstance(exc, WorkerUnavailable):
            raise
        raise WorkerUnavailable("stdio worker could not start") from exc
