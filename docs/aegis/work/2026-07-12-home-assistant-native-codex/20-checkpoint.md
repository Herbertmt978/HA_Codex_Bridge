# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 8 — add a durable global event journal and replay contract
- **Active slice:** Replace split JSON/JSONL event delivery with a SQLite WAL journal and durable outbox/reconciler
- **Completed:** approved spec and plan; Tasks 1–7; global auth/run exclusion, bounded app-server turns, HA approvals/questions, cancellation, restart recovery, and legacy HA runtime-owner rejection
- **Evidence refs:** `90-evidence.md`; Task 7 implementation commit `6583d9f`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 8 RED contracts for global cursors, replay/wait, retention, SQLite restart, and injected outbox crash points

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `6583d9f`
- **Worktree status at checkpoint:** Task 7 implementation committed; Task 8 is the next implementation slice
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** focused 251 passed/6 skipped; Windows Python 3.14.4 full 723 passed/139 skipped; Linux Python 3.13 container 851 passed/1 skipped with one Starlette deprecation warning and the Windows-only updater test excluded
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, and concurrency now share one supervised app-server owner.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** `RuntimeBroker` is the only permitted HA turn owner; `BridgeRunner` remains an explicitly deprecated external rollback adapter and is rejected by HA app composition.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept exact sandbox/path rejection, callback FIFO with early provider resolution, one queued prompt per chat, non-replayable steer outcome uncertainty, and serialized deletion/storage mutation on Windows and Linux host/container tests. Durable global event/outbox atomicity remains explicitly owned by Task 8; real HA sandbox behavior remains an acceptance gate.
- **Decision:** continue.
