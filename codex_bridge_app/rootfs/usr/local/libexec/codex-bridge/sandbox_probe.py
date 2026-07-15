#!/usr/bin/env python3
"""Fixed, non-secret probe executed through app-server's real tool sandbox."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys


WORKSPACE_PATTERN = re.compile(
    r"/config/workspaces/\.sandbox-self-test-([0-9a-f]{32})\Z"
)
NETWORK_TARGETS = (
    "supervisor",
    "homeassistant",
    "127.0.0.1",
    "192.168.1.1",
    "api.openai.com",
)
PATH_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.ENOENT})
SYSCALL_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.ENOSYS})
MAX_INHERITED_FDS = 256
# lsm_get_self_attr(2), capget(2), and prctl(2) are Linux-only kernel APIs.
# The syscall numbers below are the pinned asm-generic/x86_64 UAPI values used
# by HAOS (Linux 6.18); keep this probe proc-less because the Codex sandbox
# intentionally mounts an empty /proc.
MAX_LSM_CONTEXT_BYTES = 64 * 1024
CLONE_NEWUSER = 0x10000000
CLONE_THREAD = 0x00010000
LINUX_CAPABILITY_VERSION_3 = 0x20080522
LSM_ATTR_CURRENT = 100
LSM_ID_APPARMOR = 104
PR_GET_SECCOMP = 21
PR_CAPBSET_READ = 23
PR_GET_NO_NEW_PRIVS = 39
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_IS_SET = 1
CAPGET_SYSCALLS = {
    "aarch64": 90,
    "amd64": 125,
    "x86_64": 125,
}
LSM_GET_SELF_ATTR_SYSCALLS = {
    "aarch64": 459,
    "amd64": 459,
    "x86_64": 459,
}
PIVOT_ROOT_SYSCALLS = {
    "aarch64": 41,
    "amd64": 155,
    "x86_64": 155,
}
CLONE_SYSCALLS = {
    "aarch64": 220,
    "amd64": 56,
    "x86_64": 56,
}
CLONE3_SYSCALLS = {
    "aarch64": 435,
    "amd64": 435,
    "x86_64": 435,
}
SETNS_SYSCALLS = {
    "aarch64": 268,
    "amd64": 308,
    "x86_64": 308,
}


class ProbeError(RuntimeError):
    pass


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int32)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


class _LsmContext(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("length", ctypes.c_uint64),
        ("context_length", ctypes.c_uint64),
    ]


def _bounded_read(path: Path, maximum: int) -> bytes:
    with path.open("rb", buffering=0) as source:
        payload = source.read(maximum + 1)
    if len(payload) > maximum:
        raise ProbeError
    return payload


def _read_denied(path: Path) -> bool:
    try:
        _bounded_read(path, 64)
    except OSError as exc:
        return exc.errno in PATH_DENIED_ERRNOS
    return False


def _write_denied(path: Path) -> bool:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        return exc.errno in PATH_DENIED_ERRNOS
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return False


def _socket_creation_denied(
    family: socket.AddressFamily, kind: int = socket.SOCK_STREAM
) -> bool:
    try:
        candidate = socket.socket(family, kind)
    except OSError as exc:
        return exc.errno in SYSCALL_DENIED_ERRNOS
    candidate.close()
    return False


def _libc() -> ctypes.CDLL:
    library = ctypes.CDLL(None, use_errno=True)
    library.syscall.restype = ctypes.c_long
    library.prctl.restype = ctypes.c_int
    return library


def _capability_sets() -> dict[str, int] | None:
    syscall_number = CAPGET_SYSCALLS.get(os.uname().machine)
    if syscall_number is None:
        return None
    header = _CapabilityHeader(version=LINUX_CAPABILITY_VERSION_3, pid=0)
    data = (_CapabilityData * 2)()
    ctypes.set_errno(0)
    result = _libc().syscall(
        ctypes.c_long(syscall_number), ctypes.byref(header), ctypes.byref(data)
    )
    if result != 0:
        return None
    return {
        name: int(getattr(data[0], name)) | (int(getattr(data[1], name)) << 32)
        for name in ("effective", "permitted", "inheritable")
    }


def _prctl_value(option: int) -> int | None:
    ctypes.set_errno(0)
    result = _libc().prctl(
        ctypes.c_int(option),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
    )
    return result if result >= 0 else None


def _capability_range_zero(*, ambient: bool) -> bool:
    library = _libc()
    observed_capability = False
    for capability in range(64):
        ctypes.set_errno(0)
        if ambient:
            result = library.prctl(
                ctypes.c_int(PR_CAP_AMBIENT),
                ctypes.c_ulong(PR_CAP_AMBIENT_IS_SET),
                ctypes.c_ulong(capability),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
            )
        else:
            result = library.prctl(
                ctypes.c_int(PR_CAPBSET_READ),
                ctypes.c_ulong(capability),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
            )
        if result == 1:
            return False
        if result == 0:
            observed_capability = True
            continue
        if ctypes.get_errno() == errno.EINVAL:
            return observed_capability
        return False
    return observed_capability


def _apparmor_profile_matches(expected_profile: str) -> bool:
    syscall_number = LSM_GET_SELF_ATTR_SYSCALLS.get(os.uname().machine)
    if syscall_number is None:
        return False
    library = _libc()
    size = ctypes.c_uint32(0)
    ctypes.set_errno(0)
    first = library.syscall(
        ctypes.c_long(syscall_number),
        ctypes.c_uint(LSM_ATTR_CURRENT),
        ctypes.c_void_p(),
        ctypes.byref(size),
        ctypes.c_uint32(0),
    )
    if (
        first != -1
        or ctypes.get_errno() != errno.E2BIG
        or not ctypes.sizeof(_LsmContext) <= size.value <= MAX_LSM_CONTEXT_BYTES
    ):
        return False
    capacity = size.value
    payload = ctypes.create_string_buffer(capacity)
    ctypes.set_errno(0)
    result = library.syscall(
        ctypes.c_long(syscall_number),
        ctypes.c_uint(LSM_ATTR_CURRENT),
        ctypes.byref(payload),
        ctypes.byref(size),
        ctypes.c_uint32(0),
    )
    # The syscall returns the number of ``struct lsm_ctx`` records.  Walk the
    # variable-length records and require that count to match exactly.
    if result <= 0 or size.value > capacity:
        return False
    offset = 0
    records = 0
    apparmor_context: str | None = None
    while offset < size.value:
        if offset + ctypes.sizeof(_LsmContext) > size.value:
            return False
        header = _LsmContext.from_buffer_copy(
            payload.raw[offset : offset + ctypes.sizeof(_LsmContext)]
        )
        if (
            header.length < ctypes.sizeof(_LsmContext)
            or header.length % ctypes.alignment(_LsmContext) != 0
            or offset + header.length > size.value
            or header.context_length > header.length - ctypes.sizeof(_LsmContext)
        ):
            return False
        start = offset + ctypes.sizeof(_LsmContext)
        end = start + header.context_length
        context = payload.raw[start:end]
        if header.id == LSM_ID_APPARMOR:
            if apparmor_context is not None:
                return False
            # The UAPI recommends a NUL inside ctx_len, while HAOS AppArmor
            # currently reports the string length and leaves the aligned,
            # zero-filled record padding as the terminator.  Accept either
            # representation, but reject an unbounded or embedded NUL.
            padding = payload.raw[end : offset + header.length]
            if any(padding):
                return False
            if context.endswith(b"\0"):
                context = context[:-1]
            elif not padding:
                return False
            if b"\0" in context:
                return False
            try:
                apparmor_context = context.decode("ascii", errors="strict")
            except UnicodeDecodeError:
                return False
        offset += header.length
        records += 1
    return (
        offset == size.value
        and records == result
        and apparmor_context == f"{expected_profile} (enforce)"
    )


def _is_read_only(path: Path) -> bool:
    return bool(os.statvfs(path).f_flag & getattr(os, "ST_RDONLY", 1))


def _create_denied(path: Path) -> bool:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        return exc.errno in PATH_DENIED_ERRNOS | {errno.EROFS}
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        path.unlink()
    except OSError as exc:
        raise ProbeError from exc
    return False


def _no_inherited_sockets() -> bool:
    for descriptor in range(MAX_INHERITED_FDS):
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                return False
            continue
        if stat.S_ISSOCK(metadata.st_mode):
            return False
    return True


def _nested_user_namespace_denied() -> bool:
    try:
        child = os.fork()
    except OSError:
        return False
    if child == 0:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.unshare.argtypes = (ctypes.c_int,)
            libc.unshare.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = libc.unshare(CLONE_NEWUSER)
            denied = result == -1 and ctypes.get_errno() in SYSCALL_DENIED_ERRNOS
        except (AttributeError, OSError, TypeError, ValueError):
            os._exit(2)
        os._exit(0 if denied else 1)
    try:
        _, status = os.waitpid(child, 0)
    except OSError:
        return False
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def _clone_user_namespace_denied() -> bool:
    """Require the filter to reject clone's CLONE_NEWUSER flag."""

    syscall_number = CLONE_SYSCALLS.get(os.uname().machine)
    if syscall_number is None:
        return False
    ctypes.set_errno(0)
    result = _libc().syscall(
        ctypes.c_long(syscall_number),
        ctypes.c_ulong(CLONE_NEWUSER | CLONE_THREAD),
        ctypes.c_void_p(),
        ctypes.c_void_p(),
        ctypes.c_void_p(),
        ctypes.c_ulong(0),
    )
    return result == -1 and ctypes.get_errno() in {errno.EACCES, errno.EPERM}


def _setns_denied() -> bool:
    """Require setns to be rejected before its invalid descriptor is read."""

    syscall_number = SETNS_SYSCALLS.get(os.uname().machine)
    if syscall_number is None:
        return False
    ctypes.set_errno(0)
    result = _libc().syscall(
        ctypes.c_long(syscall_number), ctypes.c_int(-1), ctypes.c_int(0)
    )
    return result == -1 and ctypes.get_errno() in {errno.EACCES, errno.EPERM}


def _clone3_unavailable() -> bool:
    """Require clone3 to report ENOSYS so runtimes safely fall back to clone."""

    syscall_number = CLONE3_SYSCALLS.get(os.uname().machine)
    if syscall_number is None:
        return False
    ctypes.set_errno(0)
    result = _libc().syscall(
        ctypes.c_long(syscall_number), ctypes.c_void_p(), ctypes.c_size_t(0)
    )
    return result == -1 and ctypes.get_errno() == errno.ENOSYS


def _mount_operation_denied(operation: str, workspace: Path) -> bool:
    """Invoke one harmless mount operation and require a privilege denial."""

    try:
        child = os.fork()
    except OSError:
        return False
    if child == 0:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            missing = b"/tmp/.codex-sandbox-mount-target-must-not-exist"
            workspace_mount = os.fsencode(workspace)
            ctypes.set_errno(0)
            if operation == "mount":
                function = libc.mount
                function.argtypes = (
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_ulong,
                    ctypes.c_void_p,
                )
                function.restype = ctypes.c_int
                result = function(
                    workspace_mount, workspace_mount, None, 4096, None
                )
            elif operation == "umount":
                function = libc.umount2
                function.argtypes = (ctypes.c_char_p, ctypes.c_int)
                function.restype = ctypes.c_int
                result = function(workspace_mount, 0)
            elif operation == "pivot_root":
                syscall_number = PIVOT_ROOT_SYSCALLS.get(os.uname().machine)
                if syscall_number is None:
                    os._exit(2)
                function = libc.syscall
                function.restype = ctypes.c_long
                result = function(
                    ctypes.c_long(syscall_number),
                    ctypes.c_char_p(missing),
                    ctypes.c_char_p(missing),
                )
            else:
                os._exit(2)
            denied = result == -1 and ctypes.get_errno() in SYSCALL_DENIED_ERRNOS
        except (AttributeError, OSError, TypeError, ValueError):
            os._exit(2)
        os._exit(0 if denied else 1)
    try:
        _, status = os.waitpid(child, 0)
    except OSError:
        return False
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def _mount_denied(workspace: Path) -> bool:
    return _mount_operation_denied("mount", workspace)


def _umount_denied(workspace: Path) -> bool:
    return _mount_operation_denied("umount", workspace)


def _pivot_root_denied(workspace: Path) -> bool:
    return _mount_operation_denied("pivot_root", workspace)


def _validate_paths(arguments: argparse.Namespace) -> None:
    match = WORKSPACE_PATTERN.fullmatch(arguments.workspace)
    if match is None:
        raise ProbeError
    nonce = match.group(1)
    expected = {
        "private_sentinel": f"/data/bridge/.sandbox-self-test-{nonce}",
        "auth_sentinel": f"/data/codex-home/.sandbox-auth-{nonce}",
        "outside_sentinel": f"/config/.sandbox-self-test-{nonce}",
        "sibling_sentinel": f"/config/workspaces/.sandbox-sibling-{nonce}",
        "runtime_sentinel": f"/run/codex-bridge/.sandbox-self-test-{nonce}",
    }
    if any(getattr(arguments, name) != value for name, value in expected.items()):
        raise ProbeError
    if (
        len(arguments.expected_profile) > 240
        or not arguments.expected_profile.endswith(
            "codex_bridge//codex_bwrap"
        )
        or not re.fullmatch(
            r"[A-Za-z0-9_.-]+(?://[A-Za-z0-9_.-]+){1}",
            arguments.expected_profile,
        )
    ):
        raise ProbeError


def probe(arguments: argparse.Namespace) -> dict[str, object]:
    _validate_paths(arguments)
    workspace = Path(arguments.workspace)
    if Path.cwd().resolve(strict=True) != workspace.resolve(strict=True):
        raise ProbeError

    capability_sets = _capability_sets()
    marker = workspace / ".sandbox-probe-write"
    descriptor = os.open(
        marker,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.write(descriptor, b"sandbox-ok")
    finally:
        os.close(descriptor)
    workspace_write = marker.read_bytes() == b"sandbox-ok"
    marker.unlink()
    system_read = b"root:" in _bounded_read(Path("/etc/passwd"), 64 * 1024)

    ipv4_denied = _socket_creation_denied(socket.AF_INET)
    root_marker = Path("/etc") / f".codex-{workspace.name}"
    checks = {
        "apparmor_tool_profile": _apparmor_profile_matches(
            arguments.expected_profile
        ),
        "zero_permitted_capabilities": capability_sets is not None
        and capability_sets["permitted"] == 0,
        "zero_effective_capabilities": capability_sets is not None
        and capability_sets["effective"] == 0,
        "zero_inheritable_capabilities": capability_sets is not None
        and capability_sets["inheritable"] == 0,
        "zero_ambient_capabilities": _capability_range_zero(ambient=True),
        "zero_bounding_capabilities": _capability_range_zero(ambient=False),
        "no_new_privileges": _prctl_value(PR_GET_NO_NEW_PRIVS) == 1,
        "seccomp_filter": _prctl_value(PR_GET_SECCOMP) == 2,
        "root_filesystem_write_denied": _create_denied(root_marker),
        "workspace_mount_read_write": not _is_read_only(workspace),
        "workspace_write": workspace_write and not marker.exists(),
        "system_read": system_read,
        "bridge_token_denied": _read_denied(Path("/data/bridge-token")),
        "bridge_state_denied": _read_denied(Path(arguments.private_sentinel)),
        "auth_state_denied": _read_denied(Path(arguments.auth_sentinel)),
        "outside_workspace_denied": _read_denied(Path(arguments.outside_sentinel)),
        "sibling_workspace_read_denied": _read_denied(
            Path(arguments.sibling_sentinel)
        ),
        "sibling_workspace_write_denied": _write_denied(
            Path(arguments.sibling_sentinel)
        ),
        "attestation_state_denied": _read_denied(Path(arguments.runtime_sentinel)),
        "supervisor_environment_absent": "SUPERVISOR_TOKEN" not in os.environ,
        "inherited_sockets_absent": _no_inherited_sockets(),
        "nested_user_namespace_denied": _nested_user_namespace_denied(),
        "clone_user_namespace_denied": _clone_user_namespace_denied(),
        "setns_denied": _setns_denied(),
        "clone3_unavailable": _clone3_unavailable(),
        "mount_denied": _mount_denied(workspace),
        "umount_denied": _umount_denied(workspace),
        "pivot_root_denied": _pivot_root_denied(workspace),
        "ipv4_network_denied": ipv4_denied,
        "supervisor_network_denied": ipv4_denied,
        "homeassistant_network_denied": ipv4_denied,
        "loopback_network_denied": ipv4_denied,
        "lan_network_denied": ipv4_denied,
        "openai_network_denied": ipv4_denied,
        "ipv6_network_denied": _socket_creation_denied(socket.AF_INET6),
        "netlink_network_denied": _socket_creation_denied(
            socket.AF_NETLINK, socket.SOCK_RAW
        ),
    }
    if not all(checks.values()):
        raise ProbeError
    return {"schema_version": 1, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--private-sentinel", required=True)
    parser.add_argument("--auth-sentinel", required=True)
    parser.add_argument("--outside-sentinel", required=True)
    parser.add_argument("--sibling-sentinel", required=True)
    parser.add_argument("--runtime-sentinel", required=True)
    parser.add_argument("--expected-profile", required=True)
    try:
        result = probe(parser.parse_args(argv))
    except (OSError, UnicodeError, ValueError, ProbeError):
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
