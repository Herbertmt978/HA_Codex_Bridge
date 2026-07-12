# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 6 — replace CLI login parsing with ChatGPT-only auth coordination
- **Active slice:** Build a structured, revisioned device-login state machine on the single locked app-server client while retaining the external legacy adapter
- **Completed:** approved spec and plan; Tasks 1–4; Task 5 exact Codex 0.139.0 schema lock, bounded/generation-aware bidirectional JSONL transport, sanitized process ownership, graceful process-group shutdown, and HA lifespan ownership
- **Evidence refs:** `90-evidence.md`; Task 5 commits `9c27c69`, `19cad27`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** turn the new auth-coordinator RED suite green, then replace HA auth routes/probes without allowing a second Codex owner

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `19cad27`
- **Worktree status at checkpoint:** only the intentional untracked Task 6 RED test plus these work-record edits
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA mode now owns one schema-locked app-server transport, and the next slice moves account/device authorization onto that owner.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** the approved App transport is implemented; external CLI auth/model/run owners remain bounded legacy adapters until their scheduled Task 6/7/10 retirement.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 5 transport behavior on Windows and Linux host/container tests, including a live native 0.139.0 handshake; protected HA process and sandbox proof remains a later release gate.
- **Decision:** continue.
