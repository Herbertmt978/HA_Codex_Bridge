# Chat follow-up checkpoint

The owner requested black user bubbles with white text; code-only Copy;
one active/completed indicator; both desktop side columns 15% narrower; and
desktop-style thinking, tool, file-change and image-view activity. They then
explicitly requested command details in the Home Assistant administrator UI.

Implemented on `Herb/chat-bubbles-and-working-state` from released 1.4.0:

- Plain assistant prose retained; user bubbles black/white, including queued labels.
- Removed the whole-message Copy handler/control; fenced code copying retained.
- One activity control shows live runtime type and existing file counters.
- Desktop side columns reduced 15%, central reading width increased to 960px;
  mobile drawers retained. Compact desktop context drawer also reduced 15%.
- Completed image-view items are counted once by item ID; image paths remain private.
- Expandable bounded command previews projected before event persistence. Commands
  with recognised credential patterns are omitted. This is a best-effort display
  filter, not a universal secret detector; review that expanded disclosure before
  publishing. Raw output/environment remain omitted. See `docs/chat-activity.md`.

Frontend lint/build, 405 unit and 45 browser checks passed, including desktop/
mobile, light/dark, accessibility, code copying and command details. Twenty new
Python display-filter tests passed on Windows. Ruff/release-lock checks passed.
The first full Linux run hit the worker's disk reserve; completed task-owned
fixtures were removed and the full suite rerun: 2,020 Bridge tests passed with
27 skips, 356 Integration tests passed, and eight root restore tests passed.
Independent review identified Unicode truncation, now fixed with a regression
test. DOM insertion uses textContent. The intentionally conservative filtering
remains: benign assignments/data flags may also suppress a command preview.
Documentation explicitly covers the risk of unrecognised secrets being retained
in chat activity. Browser checks preceded the Unicode-only correction; the full
unit suite and build were rerun after it.

Version metadata remains 1.4.0: these changes are not published or installed.
Remaining release work: qualify the App image/native event projection, prepare a versioned release,
finish all applicable local gates, then push/PR/review/merge with the user's
GitHub action intervals. Do not reuse an immutable published version.

Production and DEV installations were not changed. CT105 was started from
stopped solely for Linux checks and verified stopped again afterwards. DEV103 was not started.
Preserve the original checkout's unrelated edits. Never redeem a reset credit
without explicit permission for that credit.
