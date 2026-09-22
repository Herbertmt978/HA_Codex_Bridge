# Chat follow-up evidence

The owner requested desktop-style chat presentation and visible tool activity.
The implementation changes rendering and bounded administrator event metadata;
the browser still communicates only with Home Assistant. No new execution,
filesystem, MCP or host permission is granted.

The existing event journal owns command previews. Recognised credential-bearing
commands are omitted before persistence. Unrecognised secrets can remain in
saved activity; the chat guide, security policy and release notes state that
limit. Conservative false positives retain the generic tool label.

Verification covers black/white bubbles in both themes, code-only copying,
single busy/terminal indicators, desktop column widths, mobile accessibility,
image-view deduplication, Unicode boundaries, filtering and event-store reopen.
The retained private logs include full Linux, frontend, browser, packaging,
transport, updater and native image checks. See the checkpoint for counts and
the release-metadata corrections verified by the affected suites.

The DEV probe runs the installed candidate wheel and real Codex app-server in
a disposable App container with the existing sandbox profile. It runs a confined
command and verifies schema-backed command/image projection. Image notifications
are deterministic fixtures; this does not claim a paid model viewed an image.
ARM64 has development build coverage only; native hardware qualification remains
open. Production and installed DEV versions remain unchanged.
