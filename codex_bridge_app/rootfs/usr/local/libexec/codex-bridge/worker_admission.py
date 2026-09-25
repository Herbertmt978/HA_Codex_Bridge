"""One App-local resource lease for Chromium and isolated stdio workers.

The lease stays open for the entire browser session or stdio worker lifetime,
so a second launcher cannot pass a separate, racy process scan.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat


LEASE_PATH = Path("/run/codex-bridge/interactive-worker.lock")
ROOT_UID = 0


class WorkerLeaseUnavailable(OSError):
    """The App cannot safely admit another large helper process."""


def acquire_worker_lease(path: Path = LEASE_PATH) -> int:
    """Return a private descriptor held until the helper is fully reaped."""
    descriptor = -1
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != ROOT_UID
                or parent.st_mode & 0o022):
            raise WorkerLeaseUnavailable("worker admission directory is unsafe")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
                or details.st_uid != ROOT_UID or details.st_gid != os.getgid()
                or stat.S_IMODE(details.st_mode) != 0o640):
            raise WorkerLeaseUnavailable("worker admission file is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, descriptor = descriptor, -1
        return result
    except (OSError, ValueError) as exc:
        raise WorkerLeaseUnavailable("worker resources are unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def release_worker_lease(descriptor: int) -> None:
    os.close(descriptor)
