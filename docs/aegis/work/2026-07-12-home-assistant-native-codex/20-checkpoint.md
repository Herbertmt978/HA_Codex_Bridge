# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 10 — share app-server models, account data, and fatal readiness
- **Active slice:** Make one supervised app-server client own models, account/limits, turns, lifecycle recovery, and truthful readiness
- **Completed:** approved spec and plan; Tasks 1–9; durable global replay, cross-file outbox recovery, private resumable uploads, ranged artifacts, and legacy v0 adapters
- **Evidence refs:** `90-evidence.md`; Task 9 implementation commit `ed9e6f9`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 10 RED contracts for one shared runtime owner, lifecycle recovery, catalogue reconciliation, version mismatch, and fatal readiness

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `ed9e6f9`
- **Worktree status at checkpoint:** Task 9 implementation committed; README branding rewrite remains intentionally unstaged; Task 10 is the next implementation slice
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** Task 9 upload suite 28 passed on Linux; Windows full 816 passed/172 skipped; Linux Python 3.13 container 977 passed/1 skipped with one Starlette deprecation warning and the Windows-only updater test excluded; 32 MiB memory smoke peaked at 2.04 MiB traced/2.00 MiB RSS growth
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** `RuntimeBroker` is the only permitted HA turn owner; `BridgeRunner` remains an explicitly deprecated external rollback adapter and is rejected by HA app composition.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept monotonic global replay, bounded waits/retention, safe event projections, serialized outbox ordering, resumable checksum-bound private uploads, and ranged artifact downloads across injected crash/race seams on Windows and Linux. Real HA sandbox, proxy, backup, Integration streaming, runtime attachment representation, and App image behavior remain acceptance gates.
- **Decision:** continue.
