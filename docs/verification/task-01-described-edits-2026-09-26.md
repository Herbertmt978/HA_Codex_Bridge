# TASK-01 reviewed text edits, 26 September 2026

## Scope and boundary

The paired 1.8.6 candidate adds a selected-task description flow for one title
or instruction change. Current and proposed values appear before Save. The
request carries only that field and the expected revision. Timing, destination,
model, reasoning, notifications and host grants remain omitted and unchanged.
Task text supplies no permission acknowledgement. Conversational timing edits,
pause and cancellation remain open in issue #112.

The existing Windows Node/Python tools and running Docker engine were available;
temporary builds and evidence use the fixed local D: drive. Both managed HA VMs
were already running and remain running. No new home service, hardware, account
credential or host grant is required. The browser still talks only to Home
Assistant; HA owns due callbacks and the Bridge owns durable claims.

## Review and focused evidence

Independent review reproduced a late-response draft replacement and a pending
run lost during a text-only update. Request identity and draft-generation checks
now prevent abandoned preflights issuing writes or late replies erasing newer
drafts. The canonical Bridge update retains its stored pending occurrence when
the schedule field is omitted. Explicit schedule edits retain recalculation.

The real-store regressions cover overdue once, interval and RRULE occurrences,
claims with the new revision, stale claims, missed-window outcomes, persistence,
paused/completed tasks and explicit timing replacements. All 78 automation
module tests passed, including the 18 new cases. The parser/edit suites passed
42 cases, including uncertain save reconciliation without a second write.
Opening, reviewing and revising changes now assign keyboard focus.

A second independent review found no remaining verified defect in this scope.
This advisory review does not substitute for release or native acceptance.

## Release checks

Fresh panel lint, 537 unit tests and 96 browser cases passed. Phone and desktop
screenshots were inspected. The browser cases include the selected-task partial
payload, retained draft during HA updates, keyboard transitions, narrow-screen
layout and WCAG checks. Generated assets were rebuilt through the owning build.

Ruff, compilation, release projection and Codex lock checks passed. The staged
amd64 App image built and its non-root installed-package, write confinement and
worker-admission checks passed. Hassfest reports no invalid Integration. HACS
passes all nine checks against published main; the candidate check remains a
GitHub gate. All three reverse-proxy transport profiles passed and their own
containers were removed. The Windows updater module passed its 10 cases.

The PC reboot interrupted the initial full Linux run; its partial result is
excluded. Git connectivity and the saved source passed recovery checks. D: is
healthy; Windows still reports the previously recorded C: repair warning.
The remaining checks moved to the approved Proxmox worker, started from stopped.
Its isolated, pinned test environment passed 2,213 Bridge tests with 27 skips,
eight root restore checks and all 417 Integration tests. The runner's temporary
directory and detached interrupt handling were corrected without product changes.

Publication, signed-image verification and managed HA acceptance remain pending.
Installed DEV and production stay on paired 1.8.5 until their respective managed
update and readback are recorded. The worker must return to its initial stopped
state after its task-owned checks finish.
