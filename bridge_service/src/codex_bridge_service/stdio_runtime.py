"""Load only the fixed, root-owned App stdio launcher after boot attestation."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


LAUNCHER = Path("/usr/local/libexec/codex-bridge/stdio_worker.py")
_MAX_REGISTRY = 128 * 1024


def saved_stdio_records_present(path: Path) -> bool:
    """Fail closed before rewriting native config when its worker is unavailable."""
    if path.is_symlink():
        return True
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_REGISTRY:
                return True
            raw = source.read(_MAX_REGISTRY + 1)
        value = json.loads(raw)
        return not (isinstance(value, dict) and value.get("version") == 1
                    and isinstance(value.get("servers"), dict)
                    and not value["servers"])
    except (OSError, ValueError, UnicodeError):
        return True


def load_worker_factory() -> Callable[[str, str], object] | None:
    if os.name != "posix" or os.uname().machine != "x86_64":
        return None
    try:
        details = LAUNCHER.lstat()
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != 0
                or details.st_mode & 0o022):
            return None
        spec = importlib.util.spec_from_file_location("codex_bridge_stdio_worker", LAUNCHER)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not module.attestation_ready():
            return None
        return module.start_worker
    except (OSError, AttributeError, ImportError, RuntimeError):
        return None
