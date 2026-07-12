# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 5 — build the supervised Codex app-server transport
- **Active slice:** Replace independent HA-side Codex subprocess ownership with one bounded, generation-aware JSONL app-server client
- **Completed:** approved spec and plan; Tasks 1–3; Task 4 immutable limits, atomic disk/transient reservations, descriptor-rooted accounting, authenticated raw-ingress ceilings, safe archive preflight, immutable download snapshots, and typed quota failures
- **Evidence refs:** `90-evidence.md`; Task 4 commits `a92e137`, `21978b7`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor and sandbox behavior remains an acceptance gate
- **Next step:** prove app-server initialization, concurrent request correlation, notifications, server callbacks, bounded dispatch, malformed input, timeout/overload behavior, restart generations, and process-group shutdown

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `21978b7`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA-owned storage now has immutable ceilings and atomic reservations, and the next slice establishes one structured Codex process owner for account and run traffic.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 4 storage/archive resource enforcement on supported Linux hosts; runtime queues/timeouts, event compaction, and rotated service logs remain explicitly owned by Tasks 7, 8, and 20.
- **Decision:** continue.
