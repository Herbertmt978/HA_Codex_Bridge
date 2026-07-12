# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 1 — API v1, independent versions, and typed readiness
- **Active slice:** Write failing API/version/readiness tests
- **Completed:** approved spec; implementation plan; plan review; ignored worktree path; isolated worktree; clean baseline verification
- **Evidence refs:** `90-evidence.md`; commits `e129f17`, `5e8e8a4`, `0853dd5`, `dde1e8b`
- **Blocked on:** target HA architecture and real sandbox result are later runtime facts, not blockers for Task 1
- **Next step:** dispatch Task 1 implementer with RED/GREEN and compatibility requirements

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Checkpoint commit before work record:** `dde1e8b`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; no production implementation has started.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to start Task 1, not sufficient for any runtime claim.
- **Decision:** continue.
