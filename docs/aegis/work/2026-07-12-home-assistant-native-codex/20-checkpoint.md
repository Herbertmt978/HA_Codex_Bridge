# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 1B — wire API/build records into readiness and diagnostics
- **Active slice:** Add failing additive `/ready` and diagnostics tests
- **Completed:** approved spec; implementation plan/review; isolated worktree/baseline; Task 1A API negotiation, immutable build metadata, realistic credential rejection, spec review, and quality review
- **Evidence refs:** `90-evidence.md`; Task 1A commits `0739475`, `45597a5`, `afadc02`
- **Blocked on:** target HA architecture and real sandbox result are later runtime facts, not blockers for Task 1
- **Next step:** dispatch Task 1B implementer for additive route/diagnostics integration

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `afadc02`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; no production implementation has started.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 1A and start Task 1B; not sufficient for a complete readiness/runtime claim.
- **Decision:** continue.
