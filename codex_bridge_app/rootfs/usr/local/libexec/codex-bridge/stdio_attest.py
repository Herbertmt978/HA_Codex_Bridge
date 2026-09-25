#!/usr/local/bin/python
"""Root-owned boot proof for the separate stdio MCP worker boundary."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import secrets
import signal
import stat
import subprocess
import sys
import time

from stdio_worker import (
    ATTESTATION_PATH,
    PROTOCOL,
    PYTHON,
    SANDBOX_HELPER,
    _descendants,
    _private_pid_one,
    _read_bounded_line,
    _strict_object,
    _verify_external,
    _validate_proof,
)


def attest() -> None:
    if os.getuid() != 0:
        raise RuntimeError("stdio attestation requires root")
    target = ATTESTATION_PATH
    parent = target.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise RuntimeError("stdio attestation directory is unsafe")
    target.unlink(missing_ok=True)
    if os.uname().machine != "x86_64":
        raise RuntimeError("stdio isolation is not qualified for this architecture")
    profile = Path("/proc/self/attr/current").read_text().strip()
    if not profile.endswith(" (enforce)") or "//" in profile:
        raise RuntimeError("stdio parent AppArmor profile is unavailable")
    profile = profile.removesuffix(" (enforce)")
    for path in (
        SANDBOX_HELPER,
        SANDBOX_HELPER.with_name("stdio_probe.py"),
        SANDBOX_HELPER.with_name("stdio_worker.py"),
        Path("/opt/codex-stdio/bin/bwrap"),
    ):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError("stdio boundary file is unsafe")
    account = pwd.getpwnam("codexbridge")
    nonce = secrets.token_hex(16)
    canary_path = Path("/data") / (".stdio-proof-" + nonce)
    canary_fd = -1
    duplicate = -1
    child = None
    descendants: set[int] = set()
    try:
        canary_fd = os.open(
            canary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
        )
        os.write(canary_fd, secrets.token_bytes(32))
        duplicate = fcntl.fcntl(canary_fd, fcntl.F_DUPFD, 100)
        child = subprocess.Popen(
            [PYTHON, str(SANDBOX_HELPER), "--nonce", nonce, "--probe", "--canary-fd", str(duplicate)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(duplicate,),
            user=account.pw_uid,
            group=account.pw_gid,
            extra_groups=[],
            start_new_session=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        os.close(duplicate)
        duplicate = -1
        proof = _strict_object(_read_bounded_line(child.stdout, timeout=30))
        _validate_proof(proof, nonce, expect_canary=True)
        descendants = _descendants(child.pid)
        namespace_pid = _private_pid_one(descendants)
        _verify_external(namespace_pid, profile, account.pw_uid, account.pw_gid, proof)
        child.stdin.write(b"close\n")
        child.stdin.flush()
        if child.wait(timeout=5) != 0:
            raise RuntimeError("stdio acceptance worker did not exit cleanly")
        deadline = time.monotonic() + 3
        while any(Path(f"/proc/{pid}").exists() for pid in descendants) and time.monotonic() < deadline:
            time.sleep(0.05)
        if any(Path(f"/proc/{pid}").exists() for pid in descendants):
            raise RuntimeError("stdio worker descendants were not reaped")
        document = {
            "schema_version": 1,
            "worker_protocol": PROTOCOL,
            "architecture": "amd64",
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "isolation": "ready",
        }
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        print("Stdio worker isolation checks passed.")
    finally:
        if duplicate >= 0:
            os.close(duplicate)
        if canary_fd >= 0:
            os.close(canary_fd)
        canary_path.unlink(missing_ok=True)
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--disabled"] and os.getuid() == 0:
            ATTESTATION_PATH.unlink(missing_ok=True)
        elif not sys.argv[1:]:
            attest()
        else:
            raise RuntimeError("invalid stdio attestation operation")
    except BaseException:
        if os.getuid() == 0:
            ATTESTATION_PATH.unlink(missing_ok=True)
        raise
