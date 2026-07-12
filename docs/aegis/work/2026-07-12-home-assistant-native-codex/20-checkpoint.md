# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 3B — integrate confined HA workspaces into storage, routes, and run paths
- **Active slice:** Add the explicit HA/external runtime profile and relative public-path adapters
- **Completed:** approved spec; implementation plan/review; Tasks 1–2; Task 3A descriptor-anchored `WorkspaceBoundary`, strict portable names, typed redaction, race/symlink/special-file defenses, Linux validation, and independent reviews
- **Evidence refs:** `90-evidence.md`; Task 3A commits `ccfbb20`, `ee38aed`, `13baaeb`, `f2072b0`, `6f3ffb6`
- **Blocked on:** target HA architecture and real sandbox result are later runtime facts, not blockers for Task 3
- **Next step:** integrate the accepted boundary without changing the external legacy storage contract

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `6f3ffb6`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; the reusable HA filesystem boundary is proven on Linux and deliberately refuses protected I/O on unsupported platforms.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 3A and integrate Task 3B; storage/routes are not yet confined until the next slice lands.
- **Decision:** continue.
