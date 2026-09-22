# Chat presentation

The owner requested a closer match to the Windows Codex chat: plain assistant
text, no user avatar, no repeated Assistant heading or black avatar, and one
run-completion indicator. Continuous coding and merging is authorised.

Implemented: avatars removed from text and image responses; message roles remain
available to screen readers. Copy sits below completed responses. Partial and
queued messages keep their meaningful labels. Completed activity uses one
indicator, with the existing details popover when there is history to inspect.

Verification: 393 frontend unit tests and 42 browser tests passed. Browser checks
cover light/dark, desktop/mobile, keyboard access, copy placement, transparent
prose and a single completion indicator. Final screenshots were inspected.
The Windows helper did not expose the Codex app window, so the owner's supplied
desktop screenshots were the visual reference. Exact desktop parity is not claimed.

The Linux Integration suite passed 351 tests and root restore checks passed eight.
The first Bridge run reached the worker's free-space reserve. Disposable fixtures
from this task were removed, preserving source and logs. The clean rerun passed
1,983 Bridge tests with 27 platform skips and two dependency deprecation warnings;
the Integration and restore suites passed again.
Lint, build, Python checks and release/lock consistency pass. This panel-only
change does not add an API or change runtime permissions. It awaits publication
and inclusion in the next paired release; production has not been changed.

MCP-05 connection management remains the next feature in the authorised sequence.
