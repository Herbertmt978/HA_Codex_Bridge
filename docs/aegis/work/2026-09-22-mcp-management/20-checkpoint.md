# MCP connection management

The owner authorised continued implementation, verification, merging and releases.
MCP-05 is implemented for paired App, Integration and panel 1.4.0 with Bridge
0.11.0 and unchanged Codex 0.155.1. Publication remains a separate verified step.
Production has not been changed. Stable App images remain amd64-only.

Native enabled state owns pause. Configuration leases exclude active/queued
turns. Opaque revisions reject stale forms and native compare-and-swap prevents
overwriting a concurrent edit. Failed reload restores the previous definition;
uncertain recovery blocks new work until restart.

Destination edits require pause and explicit keep/replace/remove authentication
consent. Relayed endpoints retain their native binding and use a private recovery
journal. Startup recovers an interrupted edit before relay activation. Secrets
never prefill or enter browser persistence. Old Apps retain their existing
controls and display update guidance. Native OAuth may need sign-in after a URL
change; editing does not revoke provider credentials.

Native amd64 development acceptance exercised add, pause, edit, resume, remove,
retained/new thread calls, stale revisions and paused restart with synthetic
bearer/header fixtures. No model prompt, production credential or installed App
upgrade was involved. Private write/reload failure and concurrency checks are in
the Bridge suite. Browser checks exercise desktop/mobile, keyboard access,
secret clearing, failed save/retry and accessible status.

The separate chat presentation PR #118 is merged. This release includes its plain
assistant prose, removed avatars/headings and single accessible completion status.
Windows screenshots supplied by the owner are the visual reference; exact desktop
feature parity is not claimed.

Local qualification is complete; see [evidence](90-evidence.md). Submit the
reviewed PR after the posting interval. All CI and actionable review findings
must pass before merging.
Then verify signed release images, SBOM/provenance and published native behaviour.
MCP-06 is next only if account usage permits. Never redeem reset credits without
explicit per-credit authorisation.

DEV VM103 and Linux worker CT105 were originally stopped and are currently
running for this task. Restore their original state after checking for other
active users/tasks. Disposable fixtures from this task were removed to preserve
the worker's disk reserve; source and logs were retained. No backups were added.
