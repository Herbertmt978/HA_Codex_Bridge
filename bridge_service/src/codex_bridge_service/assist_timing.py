"""Content-free step timings for Assist turns, printed to the App log.

Each line holds only step names and millisecond gaps: never identifiers,
prompts, answers, account details or MCP data. It lets voice latency be
measured on a real install without enabling any other logging.
"""

from __future__ import annotations

from collections.abc import Callable
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
        print(self.summary(status), flush=True)


def _milliseconds(seconds: float) -> int:
    return max(0, round(seconds * 1000))
