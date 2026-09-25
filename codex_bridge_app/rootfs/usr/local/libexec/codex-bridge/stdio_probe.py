#!/usr/local/bin/python
"""Checks made from inside the MCP-03 worker's final namespace."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import socket
import stat


def _inaccessible(path: str) -> bool:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return True
    else:
        os.close(descriptor)
        return False


def _prctl(number: int, option: int = 0) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    return int(libc.prctl(number, option, 0, 0, 0))


def _capabilities_zero() -> bool:
    class CapHeader(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class CapData(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    header = CapHeader(0x20080522, 0)
    data = (CapData * 2)()
    if libc.syscall(125, ctypes.byref(header), ctypes.byref(data)) != 0:
        return False
    if any(data[index].effective or data[index].permitted or data[index].inheritable for index in (0, 1)):
        return False
    for capability in range(64):
        result = _prctl(23, capability)
        if result == 0:
            continue
        if result == -1 and ctypes.get_errno() == errno.EINVAL and capability > 0:
            return True  # The kernel has no higher capability numbers.
        return False
    return True


def _stdio_only() -> bool:
    try:
        streams = [os.fstat(descriptor).st_mode for descriptor in (0, 1, 2)]
    except OSError:
        return False
    if not all(stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) for mode in streams):
        return False
    for descriptor in range(3, 4096):
        try:
            os.fstat(descriptor)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            return False
        else:
            return False
    return True


def isolation_checks(
    *, canary_fd: int | None = None, parent_pid: int,
) -> dict[str, object]:
    if parent_pid <= 1:
        raise RuntimeError("stdio parent is invalid")
    checks: dict[str, bool] = {
        "non_root": os.getuid() != 0,
        "no_new_privileges": _prctl(39) == 1,
        "seccomp": _prctl(21) == 2,
        "capabilities_dropped": _capabilities_zero(),
        "isolated_hostname": socket.gethostname() == "codex-stdio-worker",
        "private_environment_absent": not any(
            name in os.environ
            for name in (
                "SUPERVISOR_TOKEN",
                "CODEX_BRIDGE_AUTH_TOKEN",
                "OPENAI_API_KEY",
                "HOME_ASSISTANT_TOKEN",
            )
        ),
    }
    checks["only_stdio_descriptors"] = _stdio_only()
    for name, path in (
        ("app_data_absent", "/data"),
        ("ha_config_absent", "/config"),
        ("codex_home_absent", "/root"),
        ("bridge_run_absent", "/run/codex-bridge"),
        ("sibling_packages_absent", "/opt/codex-stdio/packages"),
    ):
        checks[name] = not Path(path).exists()
    try:
        Path("/stdio-boundary-write-probe").write_bytes(b"probe")
    except OSError:
        checks["root_read_only"] = True
    else:
        checks["root_read_only"] = False
    checks["procfs_absent"] = (
        Path("/proc").is_dir()
        and not any(Path("/proc").iterdir())
        and _inaccessible("/proc/self/status")
        and _inaccessible("/proc/1/fd/0")
    )
    if canary_fd is not None:
        checks["parent_descriptor_absent"] = _inaccessible(f"/dev/fd/{canary_fd}")
    for name, family, kind in (
        ("tcp_denied", socket.AF_INET, socket.SOCK_STREAM),
        ("ipv6_denied", socket.AF_INET6, socket.SOCK_STREAM),
        ("udp_denied", socket.AF_INET, socket.SOCK_DGRAM),
        ("unix_socket_denied", socket.AF_UNIX, socket.SOCK_STREAM),
    ):
        try:
            connection = socket.socket(family, kind)
        except OSError as exc:
            checks[name] = exc.errno in (errno.EPERM, errno.EACCES)
        else:
            connection.close()
            checks[name] = False
    libc = ctypes.CDLL(None, use_errno=True)
    for name, number, arguments in (
        ("fork_denied", 57, ()),
        ("exec_denied", 59, (0, 0, 0)),
        ("mount_denied", 165, (0, 0, 0, 0, 0)),
        ("setns_denied", 308, (-1, 0)),
        ("ptrace_denied", 101, (0, 0, 0, 0)),
        ("sysv_ipc_denied", 29, (0x43424D43, 4096, 0o666 | 0o1000)),
    ):
        ctypes.set_errno(0)
        checks[name] = libc.syscall(number, *arguments) == -1 and ctypes.get_errno() == errno.EPERM
    # The parent and a potential sibling must be absent from the fresh procfs.
    # Probe the aliases directly; a path that resolves despite the PID view is
    # a boundary failure even if the directory listing did not show it.
    checks["parent_environment_denied"] = all(
        _inaccessible(f"/proc/{pid}/environ") for pid in (parent_pid, 2)
    )
    checks["parent_descriptor_alias_denied"] = all(
        _inaccessible(f"/proc/{pid}/fd/0") for pid in (parent_pid, 2)
    )
    checks["parent_root_denied"] = all(
        _inaccessible(f"/proc/{pid}/root/data") for pid in (parent_pid, 2)
    )
    if not all(checks.values()):
        raise RuntimeError(
            "stdio boundary failed: "
            + ",".join(name for name, passed in checks.items() if not passed)
        )
    return {
        "checks": checks,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }
