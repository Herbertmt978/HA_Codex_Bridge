"""Fixed negative checks inside the browser's final namespace boundary."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import socket
import stat


def isolation_checks(
    parent_pid: int, canary_fd: int | None = None
) -> dict[str, object]:
    status = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        status[key] = value.strip()
    checks = {
        "non_root": os.getuid() != 0,
        "capabilities_dropped": all(
            int(status[name], 16) == 0
            for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        ),
        "no_new_privileges": status["NoNewPrivs"] == "1",
        "seccomp_filter": status["Seccomp"] == "2",
        "loopback_only": {name for _, name in socket.if_nameindex()} == {"lo"},
        "private_environment_absent": not any(
            name in os.environ
            for name in (
                "SUPERVISOR_TOKEN",
                "CODEX_BRIDGE_AUTH_TOKEN",
                "OPENAI_API_KEY",
            )
        ),
    }
    inherited = []
    for path in Path("/proc/self/fd").iterdir():
        try:
            descriptor = int(path.name)
            metadata = os.fstat(descriptor)
        except OSError:
            continue
        inherited.append((descriptor, metadata.st_mode))
    checks["only_control_descriptors"] = all(
        (descriptor in (3, 4) and stat.S_ISFIFO(mode))
        or (descriptor in (0, 1, 2) and (stat.S_ISCHR(mode) or stat.S_ISFIFO(mode)))
        for descriptor, mode in inherited
    )
    if canary_fd is not None:
        try:
            descriptor = os.open(
                f"/proc/{parent_pid}/fd/{canary_fd}", os.O_RDONLY | os.O_NONBLOCK
            )
        except OSError:
            checks["private_parent_fd_denied"] = True
        else:
            os.close(descriptor)
            checks["private_parent_fd_denied"] = False
    try:
        Path("/browser-boundary-write-probe").write_bytes(b"probe")
    except OSError:
        checks["root_read_only"] = True
    else:
        checks["root_read_only"] = False
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        mounts[fields[4]] = fields[5].split(",")
    checks["system_mounts_read_only"] = all(
        "ro" in mounts.get(name, []) for name in ("/", "/usr", "/lib", "/bin", "/sbin")
    )
    for name, path in {
        "bridge_files_absent": "/data/bridge",
        "chatgpt_files_absent": "/data/codex-home",
        "workspace_absent": "/config/workspaces",
        "attestation_absent": "/run/codex-bridge",
        "supervisor_socket_absent": "/run/docker.sock",
    }.items():
        checks[name] = not Path(path).exists()
    for name, family, address in (
        ("direct_public_denied", socket.AF_INET, ("1.1.1.1", 443)),
        ("supervisor_denied", socket.AF_INET, ("172.30.32.2", 80)),
        ("lan_denied", socket.AF_INET, ("192.168.1.1", 80)),
        ("metadata_denied", socket.AF_INET, ("169.254.169.254", 80)),
        ("ipv6_denied", socket.AF_INET6, ("2606:4700:4700::1111", 443)),
    ):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as stream:
                stream.settimeout(0.2)
                stream.connect(address)
        except OSError:
            checks[name] = True
        else:
            checks[name] = False
    for name, family, kind in (
        ("udp_denied", socket.AF_INET, socket.SOCK_DGRAM),
        ("packet_socket_denied", socket.AF_PACKET, socket.SOCK_RAW),
    ):
        try:
            stream = socket.socket(family, kind)
        except OSError:
            checks[name] = True
        else:
            stream.close()
            checks[name] = False
    libc = ctypes.CDLL(None, use_errno=True)
    for name, number, arguments in (
        ("mount_denied", 165, (b"none", b"/tmp", b"tmpfs", 0, 0)),
        ("setns_denied", 308, (-1, 0)),
        ("ptrace_denied", 101, (0, 0, 0, 0)),
        ("pidfd_getfd_denied", 438, (-1, 0, 0)),
    ):
        ctypes.set_errno(0)
        checks[name] = (
            libc.syscall(number, *arguments) == -1 and ctypes.get_errno() == errno.EPERM
        )
    # A mounted view of the parent's procfs must not provide file handles or
    # private process state, even for other processes with the same Unix UID.
    candidates = list(Path("/proc").glob("[0-9]*"))
    for name, suffix in (
        ("parent_environment_denied", "environ"),
        ("parent_root_denied", "root/data"),
    ):
        denied = True
        for candidate in candidates:
            try:
                descriptor = os.open(candidate / suffix, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            else:
                os.close(descriptor)
                denied = False
                break
        checks[name] = denied
    if not all(checks.values()):
        failed = ",".join(name for name, passed in checks.items() if not passed)
        raise RuntimeError("browser boundary failed: " + failed)
    return {
        "checks": checks,
        "pid": int(status["NSpid"].split()[0]),
        "namespace_pids": [int(pid) for pid in status["NSpid"].split()],
        "namespaces": {
            name: os.readlink("/proc/self/ns/" + name)
            for name in ("user", "net", "mnt", "pid")
        },
    }
