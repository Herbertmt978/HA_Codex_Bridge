"""Watch the whole browser process tree from outside its namespaces."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from threading import Event, Thread

MAX_BROWSER_MEMORY_BYTES = 1_500 * 1024 * 1024
MAX_BROWSER_PROCESSES = 64


def process_tree(pid: int, proc: Path = Path("/proc")) -> set[int]:
    children: dict[int, list[int]] = {}
    entries = list(proc.glob("[0-9]*"))
    if len(entries) > 4096:
        raise RuntimeError("browser process visibility limit exceeded")
    for entry in entries:
        try:
            # HAOS omits /proc/PID/task/TID/children. PPid in status also
            # includes children created by a non-leader thread.
            status = (entry / "status").read_text()
        except FileNotFoundError:
            continue
        parent = next(
            int(line.split()[1])
            for line in status.splitlines()
            if line.startswith("PPid:")
        )
        children.setdefault(parent, []).append(int(entry.name))
    pending = [pid]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > MAX_BROWSER_PROCESSES:
            break
        pending.extend(children.get(current, []))
    return seen


def process_tree_usage(pid: int, proc: Path = Path("/proc")) -> tuple[int, int]:
    seen = process_tree(pid, proc)
    memory = 0
    for current in seen:
        try:
            status = (proc / str(current) / "status").read_text()
            for line in status.splitlines():
                if line.startswith(("VmRSS:", "VmSwap:")):
                    memory += int(line.split()[1]) * 1024
        except FileNotFoundError:
            continue
    return len(seen), memory


class BrowserResourceGuard:
    """A sampled aggregate bound, alongside inherited kernel resource limits."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._stop = Event()
        self.exceeded = False
        self._thread = Thread(
            target=self._watch, daemon=True, name="browser-resource-guard"
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _watch(self) -> None:
        while not self._stop.wait(0.1) and self._process.poll() is None:
            try:
                processes, memory = process_tree_usage(self._process.pid)
                if (
                    processes <= MAX_BROWSER_PROCESSES
                    and memory <= MAX_BROWSER_MEMORY_BYTES
                ):
                    continue
            except (OSError, ValueError, RuntimeError, StopIteration):
                # An unreadable process tree removes the resource guarantee.
                pass
            self.exceeded = True
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
