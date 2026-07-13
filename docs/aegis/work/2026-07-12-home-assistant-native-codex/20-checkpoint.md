# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 9 — add resumable uploads and ranged artifact downloads
- **Active slice:** Build the bounded 8 MiB chunk/session protocol and safe Range/ETag download contract entirely behind the Bridge
- **Completed:** approved spec and plan; Tasks 1–8; durable global replay, cross-file outbox recovery, privacy projections, and legacy v0 adapters
- **Evidence refs:** `90-evidence.md`; Task 8 implementation commit `c1f2307`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 9 RED contracts for resumable chunk sessions, checksums, restart/cancel recovery, quotas, and ranged artifact downloads

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `c1f2307`
- **Worktree status at checkpoint:** Task 8 implementation committed; README branding rewrite remains intentionally unstaged; Task 9 is the next implementation slice
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** Task 8 focused 322 passed/66 skipped; Windows full 814 passed/141 skipped; Linux Python 3.13 container 944 passed/1 skipped with one Starlette deprecation warning and the Windows-only updater test excluded
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, and concurrency now share one supervised app-server owner.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** `RuntimeBroker` is the only permitted HA turn owner; `BridgeRunner` remains an explicitly deprecated external rollback adapter and is rejected by HA app composition.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept monotonic global replay, bounded waits/retention, safe event projections, legacy import, serialized outbox ordering, and exactly-once state/event recovery across every injected crash seam on Windows and Linux. Real HA sandbox, proxy, backup, and App image behavior remain acceptance gates.
- **Decision:** continue.
