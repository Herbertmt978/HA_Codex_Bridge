# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 2 — replace inherited subprocess environments with a literal allowlist
- **Active slice:** Write hostile-environment tests for every Codex subprocess caller
- **Completed:** approved spec; implementation plan/review; isolated worktree/baseline; Task 1 API negotiation, immutable build metadata, authenticated typed readiness, additive diagnostics, independent spec review, and independent quality review
- **Evidence refs:** `90-evidence.md`; Task 1 commits `0739475`, `45597a5`, `afadc02`, `e690cdd`
- **Blocked on:** target HA architecture and real sandbox result are later runtime facts, not blockers for Task 2
- **Next step:** implement and verify the Codex subprocess environment allowlist test-first

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `e690cdd`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; the API/readiness foundation is complete and remains additive to the legacy external Bridge.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 1 and start Task 2; static readiness does not yet prove runtime or sandbox health.
- **Decision:** continue.
