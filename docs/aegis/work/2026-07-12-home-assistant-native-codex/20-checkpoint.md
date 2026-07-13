# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 11 — add the Home Assistant Integration test foundation and protocol client
- **Active slice:** Establish Core-side protocol/version authority, typed failures, redirect defense, timeouts, and streaming ownership before discovery and event work
- **Completed:** approved spec and plan; Tasks 1–10; one shared HA app-server now owns models, account/limits, authentication, turns, lifecycle recovery, and truthful readiness
- **Evidence refs:** `90-evidence.md`; Task 10 timing-test commit `f6faa5d`; Task 10 implementation commit `d4c786d`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 11 RED contracts for v0/v1/future API negotiation, redirect rejection, bounded requests, typed errors, and streamed response ownership

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `d4c786d`
- **Worktree status at checkpoint:** Task 10 implementation committed; README branding rewrite remains intentionally unstaged and isolated until Task 23; Task 11 is the next implementation slice
- **Baseline command:** `python -m pytest -q` from `bridge_service`
- **Baseline result:** Task 10 focused review suite 151 passed/3 skipped; Windows full 832 passed/175 skipped plus 98/98 broker tests after timing hardening; Linux Python 3.13 applicable coverage passed in two auditable shards (898 passed/1 skipped plus 98 passed) with the two Windows-only updater files excluded and one known Starlette deprecation warning; Ruff, `compileall`, and diff checks passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** one `CodexAppServerClient` is the HA process/credential owner for catalogue, account/limits, authentication, and `RuntimeBroker` turns; `BridgeRunner` plus subprocess probes remain explicitly deprecated external rollback adapters and are not composed in HA mode.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept shared runtime ownership, generation-aware model/limit recovery, direct-chat default reconciliation, bounded account/catalogue reads, version-mismatch diagnostics, reverse lifecycle cleanup, and fail-closed readiness across Windows and Linux. The real sandbox self-test remains intentionally fatal until Task 21; proxy, backup, Integration streaming, runtime attachment representation, App image behavior, and target-HA acceptance remain release gates.
- **Decision:** continue.
