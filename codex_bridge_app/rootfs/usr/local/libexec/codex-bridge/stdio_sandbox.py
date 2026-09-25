#!/usr/local/bin/python
"""Fixed, fail-closed namespace launcher for approved Python stdio MCP servers."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import resource
import runpy
import struct
import sys

sys.path.insert(0, "/usr/local/libexec/codex-bridge")
from stdio_probe import isolation_checks


BWRAP = "/opt/codex-stdio/bin/bwrap"
HELPER = "/usr/local/libexec/codex-bridge/stdio_sandbox.py"
PACKAGE_ROOT = Path("/opt/codex-stdio/packages")
MODULE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
MAX_PROOF_BYTES = 8192
MAX_CPU_SECONDS = 45
MAX_ADDRESS_SPACE = 256 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_DESCRIPTORS = 32


def _instruction(code: int, jt: int, jf: int, value: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, value)


def boundary_filter(*, deny_exec: bool = False) -> bytes:
    """Deny sockets, process creation and namespace escape on amd64.

    Bubblewrap applies the first filter to its final command. The Python
    helper adds the second filter before importing package code so it can
    also deny execve, which the first filter must allow for Python startup.
    """
    if os.uname().machine != "x86_64":
        raise RuntimeError("unsupported stdio architecture")
    forbidden = (
        29, 30, 31,
        41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55,
        56, 57, 58, 62, 64, 65, 66, 67, 68, 69, 70, 71,
        101, 109, 112, 155, 165, 166, 200, 220, 234,
        240, 241, 242, 243, 244, 245, 246, 248, 249, 250,
        272, 288, 298, 299, 303, 304, 307,
        308, 310, 311, 321, 323, 424, 425, 426, 427, 428, 429,
        430, 431, 432, 433, 434, 435, 438, 442,
    ) + ((59, 322) if deny_exec else ())
    instructions = [
        _instruction(0x20, 0, 0, 4),  # LD arch
        _instruction(0x15, 1, 0, 0xC000003E),  # x86_64 only
        _instruction(0x06, 0, 0, 0x80000000),  # kill other architecture
        _instruction(0x20, 0, 0, 0),  # LD syscall number
        _instruction(0x45, 0, 1, 0x40000000),  # kill x32 ABI
        _instruction(0x06, 0, 0, 0x80000000),
    ]
    for number in forbidden:
        instructions.extend(
            (_instruction(0x15, 0, 1, number), _instruction(0x06, 0, 0, 0x50000 | errno.EPERM))
        )
    instructions.append(_instruction(0x06, 0, 0, 0x7FFF0000))
    return b"".join(instructions)


def _install_exec_filter() -> None:
    filter_bytes = boundary_filter(deny_exec=True)
    instructions = (ctypes.c_ubyte * len(filter_bytes)).from_buffer_copy(filter_bytes)

    class SockFilterProgram(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    program = SockFilterProgram(len(filter_bytes) // 8, ctypes.addressof(instructions))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("stdio execution filter unavailable")


def _module_from_package(package_id: str, revision: str) -> tuple[Path, str]:
    from codex_bridge_service.stdio_package_catalogue import verify_package

    package = verify_package(package_id, revision)
    package_path = Path(package.package_path)
    if not package_path.is_relative_to(PACKAGE_ROOT) or package_path == PACKAGE_ROOT:
        raise RuntimeError("package outside fixed catalogue")
    entrypoint = tuple(package.entrypoint)
    if (
        len(entrypoint) != 3
        or entrypoint[:2] != ("python", "-m")
        or not MODULE_PATTERN.fullmatch(entrypoint[2])
    ):
        raise RuntimeError("package entrypoint is not a fixed Python module")
    return package_path, entrypoint[2]


def launch(
    *, nonce: str, probe: bool, package_id: str | None,
    revision: str | None, canary_fd: int | None,
) -> None:
    if os.getuid() == 0 or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise RuntimeError("invalid stdio launch")
    libc = ctypes.CDLL(None, use_errno=True)
    parent_pid = os.getppid()
    if parent_pid <= 1 or libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        raise RuntimeError("stdio parent unavailable")
    if probe:
        if package_id is not None or revision is not None:
            raise RuntimeError("invalid stdio probe")
        package_path = None
        module = None
    else:
        if package_id is None or revision is None:
            raise RuntimeError("missing approved package")
        package_path, module = _module_from_package(package_id, revision)
    if canary_fd is not None:
        if not probe or canary_fd < 100 or canary_fd > 1024:
            raise RuntimeError("invalid stdio attestation descriptor")
        os.fstat(canary_fd)
        os.close(canary_fd)
    filter_fd = os.memfd_create("stdio-boundary-seccomp", flags=0)
    try:
        os.write(filter_fd, boundary_filter())
        os.lseek(filter_fd, 0, os.SEEK_SET)
        os.set_inheritable(filter_fd, True)
        arguments = [
            BWRAP,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--hostname", "codex-stdio-worker",
            "--cap-drop", "ALL",
            "--add-seccomp-fd", str(filter_fd),
            "--dir", "/usr",
            "--dir", "/usr/share",
            "--dir", "/usr/local",
            "--dir", "/usr/local/bin",
            "--dir", "/usr/local/libexec",
            "--dir", "/usr/local/libexec/codex-bridge",
            "--dir", "/opt",
            "--dir", "/etc",
            "--dir", "/proc",
            "--dir", "/dev",
        ]
        for directory in ("/bin", "/lib", "/sbin", "/usr/lib", "/usr/local/lib"):
            if Path(directory).exists():
                arguments.extend(("--ro-bind", directory, directory))
        arguments.extend((
            "--ro-bind", "/usr/local/bin/python3.14", "/usr/local/bin/python3.14"
        ))
        arguments.extend((
            "--ro-bind", "/usr/share/zoneinfo", "/usr/share/zoneinfo"
        ))
        arguments.extend(
            (
                "--ro-bind", HELPER, HELPER,
                "--ro-bind", "/usr/local/libexec/codex-bridge/stdio_probe.py",
                "/usr/local/libexec/codex-bridge/stdio_probe.py",
                # HAOS cannot mount procfs inside this container. Leave an
                # empty directory: no host process or descriptor aliases are
                # exposed, and the parent verifies the worker via outer proc.
                "--dev", "/dev",
                "--size", "33554432", "--tmpfs", "/tmp",
            )
        )
        # A single verified package tree is visible, with no sibling catalogue.
        if package_path is None:
            arguments.extend(("--ro-bind", "/usr/local/libexec/codex-bridge", "/package"))
        else:
            arguments.extend(("--ro-bind", str(package_path), "/package"))
        arguments.extend(
            (
                "--remount-ro", "/",
                "--chdir", "/tmp",
                "--clearenv",
                "--setenv", "HOME", "/tmp",
                "--setenv", "TMPDIR", "/tmp",
                "--setenv", "LANG", "C.UTF-8",
                "--setenv", "PYTHONNOUSERSITE", "1",
                "--", "/usr/local/bin/python3.14", "-I", HELPER,
                "--inside", "--nonce", nonce, "--parent-pid", str(parent_pid),
            )
        )
        if probe:
            arguments.append("--probe")
            if canary_fd is not None:
                arguments.extend(("--canary-fd", str(canary_fd)))
        else:
            arguments.extend(("--module", module))
        os.execv(BWRAP, arguments)
    finally:
        os.close(filter_fd)


def inside(
    *, nonce: str, probe: bool, module: str | None,
    canary_fd: int | None, parent_pid: int,
) -> None:
    if os.getuid() == 0 or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise RuntimeError("invalid stdio namespace entry")
    if probe != (module is None) or (module is not None and not MODULE_PATTERN.fullmatch(module)):
        raise RuntimeError("invalid stdio package entry")
    if parent_pid <= 1:
        raise RuntimeError("invalid stdio parent")
    for name, value in (
        (resource.RLIMIT_CPU, MAX_CPU_SECONDS),
        (resource.RLIMIT_AS, MAX_ADDRESS_SPACE),
        (resource.RLIMIT_DATA, MAX_ADDRESS_SPACE),
        (resource.RLIMIT_FSIZE, MAX_FILE_BYTES),
        (resource.RLIMIT_NOFILE, MAX_DESCRIPTORS),
        (resource.RLIMIT_NPROC, 1),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(name, (value, value))
    _install_exec_filter()
    proof = isolation_checks(canary_fd=canary_fd, parent_pid=parent_pid)
    proof["nonce"] = nonce
    encoded = json.dumps(proof, separators=(",", ":")).encode()
    if len(encoded) > MAX_PROOF_BYTES:
        raise RuntimeError("stdio proof exceeded bound")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()
    if probe:
        if sys.stdin.buffer.readline(16) != b"close\n":
            raise RuntimeError("invalid stdio probe completion")
        return
    # No subprocess or native exec is permitted after this point. The approved
    # module runs in this interpreter, and inherits the locked syscall filters.
    sys.path.insert(0, "/package")
    sys.argv = [module]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--package-id")
    parser.add_argument("--revision")
    parser.add_argument("--module")
    parser.add_argument("--canary-fd", type=int)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.inside:
        if args.package_id is not None or args.revision is not None:
            parser.error("inside entry cannot select a package")
        if args.parent_pid is None:
            parser.error("inside entry requires its parent")
        inside(
            nonce=args.nonce, probe=args.probe, module=args.module,
            canary_fd=args.canary_fd, parent_pid=args.parent_pid,
        )
    else:
        if args.module is not None or args.parent_pid is not None:
            parser.error("outer launcher cannot select a module")
        launch(
            nonce=args.nonce, probe=args.probe,
            package_id=args.package_id, revision=args.revision,
            canary_fd=args.canary_fd,
        )


if __name__ == "__main__":
    main()
