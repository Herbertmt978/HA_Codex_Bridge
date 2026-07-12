# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 3 — constrain HA workspaces and file operations
- **Active slice:** Build and verify the `WorkspaceBoundary` primitive before storage/route integration
- **Completed:** approved spec; implementation plan/review; isolated worktree/baseline; Task 1 API/readiness contract; Task 2 literal Codex environment allowlist, cross-platform path validation, credential-carrier hardening, and independent reviews
- **Evidence refs:** `90-evidence.md`; Task 2 commits `61ad49a`, `649af01`, `6982cd7`, `c37042a`
- **Blocked on:** target HA architecture and real sandbox result are later runtime facts, not blockers for Task 3
- **Next step:** implement hostile lexical/resolved/symlink/special-file tests and the reusable workspace boundary

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `c37042a`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; Codex subprocesses now receive a small explicit environment without changing the legacy external-owner fallback.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 2 and start Task 3; this environment boundary does not replace the later AppArmor/bubblewrap runtime gate.
- **Decision:** continue.
