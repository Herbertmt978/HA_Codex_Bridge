# MCP-05 development qualification

Candidate: App, Integration and panel 1.4.0; Bridge 0.11.0; Codex 0.155.1.
These are local and development-target results, not publication acceptance.

- Frontend: lint/build, 401 unit tests and 44 browser tests passed. Browser
  checks include light/dark chat, desktop/mobile connection editing, keyboard
  use, accessible status, failed-save recovery and credential clearing. Final
  screenshots were inspected.
- Linux: 1,999 Bridge tests, 356 Integration tests and eight root restore
  checks passed. Bridge reports 27 platform skips and two dependency warnings.
- Ruff, compileall, release/lock consistency, HACS, hassfest, reverse-proxy
  transport, workflow policy and ten Windows updater checks passed.
- Fresh amd64 and emulated ARM64 development images built successfully. The
  stable release continues to advertise amd64 only; no native ARM64 hardware
  qualification is claimed.
- The final amd64 image passed native HAOS development startup and sandbox
  checks plus real Codex add/edit/pause/resume/remove calls against disposable
  MCP fixtures. Retained and new chats could not call a paused server, resume
  restored calls against the edited destination, and pause survived restart.
  Stale revisions were rejected. Synthetic credentials were used throughout.

The full checks caught a missing readiness capability and release metadata drift;
both were corrected before a clean rerun. Disk pressure on the Linux worker and
development HAOS VM was resolved by removing only this task's disposable fixtures,
images and transfer archive. Backups and other tasks' resources were preserved.

No installed development App/Integration or production system was upgraded.
Signed image, provenance, SBOM and published-image verification remain required
after merge and publication.
