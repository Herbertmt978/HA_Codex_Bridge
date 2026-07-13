# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 14 — stream resumable files through authenticated HA HTTP views
- **Active slice:** Forward upload sessions, fixed chunks, completion/cancel, and ranged downloads without HA temp files or whole-payload buffering
- **Completed:** approved spec and plan; Tasks 1–13; API v1 now has one config-entry-owned event consumer with bounded replay, fan-out, gap recovery, and admin-only HA WebSockets
- **Evidence refs:** `90-evidence.md`; Task 13 implementation commit `60e08fe`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 14 RED contracts for authenticated resumable uploads, bounded upstream/downstream streaming, cancellation, safe headers, and ranged artifact resume

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `60e08fe`
- **Worktree status at checkpoint:** Task 13 implementation committed; README branding rewrite remains intentionally unstaged and isolated until Task 23; Task 14 is the next implementation slice
- **Baseline commands:** root `python -m pytest -q` for the HA Integration; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --ignore=tests/test_update_script.py -p pytest_asyncio.plugin -p pytest_timeout` from `bridge_service` for the legacy service suite on Linux
- **Baseline result:** Integration 116/116 on Linux, including 68 focused Task 13 contracts; Bridge remained 996 passed/1 skipped at the last service regression with the PowerShell updater module excluded; Ruff, `compileall`, panel `node --check`, diff checks, and HA task-leak checks passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** one `CodexAppServerClient` is the HA process/credential owner for catalogue, account/limits, authentication, and `RuntimeBroker` turns; `BridgeRunner` plus subprocess probes remain explicitly deprecated external rollback adapters and are not composed in HA mode.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept shared runtime ownership, generation-aware model/limit recovery, direct-chat default reconciliation, bounded account/catalogue reads, version-mismatch diagnostics, reverse lifecycle cleanup, and fail-closed readiness across Windows and Linux. The real sandbox self-test remains intentionally fatal until Task 21; proxy, backup, Integration streaming, runtime attachment representation, App image behavior, and target-HA acceptance remain release gates.
- **Decision:** continue.
