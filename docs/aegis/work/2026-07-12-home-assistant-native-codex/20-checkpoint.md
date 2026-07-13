# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 7 — add the global runtime gate, app-server turns, approvals, and questions
- **Active slice:** Establish the single auth/run lease and replace HA `codex exec --json` ownership with bounded app-server thread/turn brokering
- **Completed:** approved spec and plan; Tasks 1–5; Task 6 structured ChatGPT-only device auth, restart reconciliation, safe HA account/rate-limit probes, explicit cancel/logout, and legacy external isolation
- **Evidence refs:** `90-evidence.md`; Task 6 commit `0480f38`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write the runtime-gate RED contract first, including the carried Task 6 auth/run exclusion, then implement app-server turns/approvals/questions

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `0480f38`
- **Worktree status at checkpoint:** clean before these work-record edits
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** 115 passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA account/device authorization now uses the single app-server, and the next slice moves HA turns and interactive callbacks onto the same owner.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** the App auth coordinator and safe account/limit probes are implemented; the external CLI auth adapter remains bounded, while the HA legacy runner is retired in Task 7.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept Task 6 auth/account/limits behavior and HA route composition on Windows and Linux host/container tests; durable global events and auth/run exclusion remain explicitly owned by Tasks 8 and 7.
- **Decision:** continue.
