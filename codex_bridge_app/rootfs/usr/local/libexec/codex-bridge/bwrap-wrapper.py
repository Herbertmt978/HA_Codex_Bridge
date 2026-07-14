#!/usr/local/bin/python
"""Launch Bubblewrap with a final-command namespace and netlink filter."""

from __future__ import annotations

from dataclasses import dataclass
import os
import struct
import sys


REAL_BWRAP = "/usr/local/bin/bwrap"
CLONE_NEWUSER = 0x10000000
AF_NETLINK = 16
LINUX_EPERM = 1
LINUX_ENOSYS = 38

# Values from Linux UAPI audit and syscall headers. The App currently ships
# for amd64; keeping the generic aarch64 table here makes the boundary fail
# closed if that architecture is enabled later.
AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7

BPF_LD_W_ABS = 0x20
BPF_JMP_JEQ_K = 0x15
BPF_JMP_JSET_K = 0x45
BPF_RET_K = 0x06
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000

SECCOMP_DATA_NR_OFFSET = 0
SECCOMP_DATA_ARCH_OFFSET = 4
SECCOMP_DATA_ARG0_OFFSET = 16


@dataclass(frozen=True)
class SyscallTable:
    audit_arch: int
    clone: int
    unshare: int
    setns: int
    socket: int
    clone3: int


SYSCALL_TABLES = {
    "x86_64": SyscallTable(
        audit_arch=AUDIT_ARCH_X86_64,
        clone=56,
        unshare=272,
        setns=308,
        socket=41,
        clone3=435,
    ),
    "aarch64": SyscallTable(
        audit_arch=AUDIT_ARCH_AARCH64,
        clone=220,
        unshare=97,
        setns=268,
        socket=198,
        clone3=435,
    ),
}


def _instruction(code: int, jt: int, jf: int, value: int) -> bytes:
    """Pack one little-endian Linux ``struct sock_filter`` instruction."""

    return struct.pack("<HBBI", code, jt, jf, value)


def _filter(table: SyscallTable) -> bytes:
    """Return a classic-BPF filter applied after Bubblewrap finishes setup."""

    denied = SECCOMP_RET_ERRNO | LINUX_EPERM
    unavailable = SECCOMP_RET_ERRNO | LINUX_ENOSYS
    return b"".join(
        (
            _instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_ARCH_OFFSET),
            _instruction(BPF_JMP_JEQ_K, 1, 0, table.audit_arch),
            _instruction(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
            _instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_NR_OFFSET),
            _instruction(BPF_JMP_JEQ_K, 0, 1, table.unshare),
            _instruction(BPF_RET_K, 0, 0, denied),
            _instruction(BPF_JMP_JEQ_K, 0, 1, table.setns),
            _instruction(BPF_RET_K, 0, 0, denied),
            # clone3 stores flags behind a userspace pointer. Returning ENOSYS
            # makes standard runtimes fall back to clone, whose flags we can
            # inspect without blocking ordinary process/thread creation.
            _instruction(BPF_JMP_JEQ_K, 0, 1, table.clone3),
            _instruction(BPF_RET_K, 0, 0, unavailable),
            _instruction(BPF_JMP_JEQ_K, 0, 3, table.socket),
            _instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_ARG0_OFFSET),
            _instruction(BPF_JMP_JEQ_K, 0, 1, AF_NETLINK),
            _instruction(BPF_RET_K, 0, 0, denied),
            _instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_NR_OFFSET),
            _instruction(BPF_JMP_JEQ_K, 0, 3, table.clone),
            _instruction(BPF_LD_W_ABS, 0, 0, SECCOMP_DATA_ARG0_OFFSET),
            _instruction(BPF_JMP_JSET_K, 0, 1, CLONE_NEWUSER),
            _instruction(BPF_RET_K, 0, 0, denied),
            _instruction(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW),
        )
    )


def _write_filter(payload: bytes) -> int:
    descriptor = os.memfd_create("codex-final-command-seccomp", flags=0)
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("seccomp filter write failed")
        view = view[written:]
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.set_inheritable(descriptor, True)
    return descriptor


def main() -> None:
    arguments = sys.argv[1:]
    if arguments in (["--help"], ["--version"]):
        os.execv(REAL_BWRAP, [REAL_BWRAP, *arguments])
    if "--disable-userns" in arguments:
        raise RuntimeError("--disable-userns is incompatible with the HAOS runtime")

    table = SYSCALL_TABLES.get(os.uname().machine)
    if table is None:
        raise RuntimeError("unsupported architecture for the Codex seccomp launcher")
    descriptor = _write_filter(_filter(table))
    os.execv(
        REAL_BWRAP,
        [REAL_BWRAP, "--add-seccomp-fd", str(descriptor), *arguments],
    )


if __name__ == "__main__":
    main()
