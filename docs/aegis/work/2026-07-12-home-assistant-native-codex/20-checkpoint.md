# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 4 — enforce bounded resource use and crash-safe quota release
- **Active slice:** Add immutable resource limits and atomic reservations before integrating upload, artifact, archive, queue, run, event, and log paths
- **Completed:** approved spec and plan; Tasks 1–2; Task 3A descriptor-anchored boundary; Task 3B HA runtime profile, owned project/thread paths, confined runner workspaces, selected-only attachment descriptors, immutable artifact snapshots, and private archive lifecycle
- **Evidence refs:** `90-evidence.md`; Task 3 commits `ccfbb20`, `ee38aed`, `13baaeb`, `f2072b0`, `6f3ffb6`, `51e1fc9`, `12d648a`, `1e5b1f3`, `b175578`, `e07d212`, `e3a7e0a`, `e3b7c24`
- **Blocked on:** real HA inherited-descriptor and sandbox behavior remains an acceptance gate, not a blocker for resource-control implementation
- **Next step:** prove reservation races, ceilings, free-space protection, bounded state, archive abuse limits, cleanup, and crash recovery without changing the external legacy profile

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `e3b7c24`
- **Worktree status at checkpoint:** clean before these work-record files
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA-owned workspaces, uploads, artifacts, and archives are confined behind relative public locators and immutable descriptor snapshots.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** none beyond the approved App owner and bounded v0 carrier.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 3 implementation on supported Linux hosts; target-HA sandbox proof is deliberately reserved for acceptance.
- **Decision:** continue.
