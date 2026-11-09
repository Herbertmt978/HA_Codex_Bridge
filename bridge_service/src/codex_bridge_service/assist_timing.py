"""Content-free step timings for Assist turns, printed to the App log.

Each line holds only step names and millisecond gaps: never identifiers,
prompts, answers, account details or MCP data. It lets voice latency be
measured on a real install without enabling any other logging.
"""

from __future__ import annotations

from collections.abc import Callable
import queue
import threading
from time import monotonic

STEPS = (
    ("worker", "worker started"),
    ("config", "isolation config"),
    ("thread", "Codex session"),
    ("turn", "turn sent"),
    ("started", "run started"),
    ("first_output", "first output"),
    ("answer", "answer complete"),
    ("turn_completed", "turn completed"),
    ("unloaded", "session unloaded"),
    ("completed", "run recorded"),
)
_LABELS = dict(STEPS)
_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
# Lines wait here for one writer thread, so a slow or broken log pipe can never
# block or fail run cleanup. A full queue drops the line.
_LINES: queue.Queue[str] = queue.Queue(maxsize=64)
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None


class AssistTimer:
    """Record when each step is reached after the prompt was accepted."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._marks: dict[str, float] = {}
        self.resumed = False

    def mark(self, step: str, *, latest: bool = False) -> None:
        """Keep the first time a step is reached, or the last for a repeated one."""

        if step in _LABELS and (latest or step not in self._marks):
            self._marks[step] = self._clock()

    def summary(self, status: str) -> str:
        previous = self._start
        parts = []
        for step, label in STEPS:
            reached = self._marks.get(step)
            if reached is None:
                continue
            parts.append(f"{label} +{_milliseconds(reached - previous)}")
            previous = reached
        outcome = status if status in _STATUSES else "ended"
        session = "resumed" if self.resumed else "new"
        steps = ", ".join(parts) if parts else "no steps reached"
        return (
            f"Assist turn {outcome} in {_milliseconds(previous - self._start)} ms "
            f"({session} Codex session): {steps} (ms)"
        )

    def report(self, status: str) -> None:
        """Queue the summary for the App log without blocking or raising."""

        try:
            _queue_line(self.summary(status))
        except Exception:  # noqa: BLE001 - timing output is best-effort
            pass


def _queue_line(line: str) -> None:
    global _writer
    try:
        _LINES.put_nowait(line)
    except queue.Full:
        return
    with _writer_lock:
        # A writer that has stopped is replaced, so lines never queue up unread.
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_write_lines, name="assist-timing-log", daemon=True)
            _writer.start()


def _write_lines() -> None:
    while True:
        line = _LINES.get()
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001 - a broken log pipe must not stop the writer
            pass
        finally:
            _LINES.task_done()


def _milliseconds(seconds: float) -> int:
    return max(0, round(seconds * 1000))
