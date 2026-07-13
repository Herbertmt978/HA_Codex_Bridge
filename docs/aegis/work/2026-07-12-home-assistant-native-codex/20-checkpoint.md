# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 15 — establish a reproducible frontend build and hostile-content tests
- **Active slice:** Replace the generated-only panel with deterministic source modules, a resumable browser upload client, safe DOM boundaries, and unit/E2E authority
- **Completed:** approved spec and plan; Tasks 1–14; authenticated HA HTTP views now stream resumable uploads and ranged artifacts without HA temp files or whole-payload buffers
- **Evidence refs:** `90-evidence.md`; Task 14 implementation commit `72b7454`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** inventory the generated panel and write Task 15 RED build, protocol, resumable-upload, safe-DOM, XSS, and build-integrity contracts

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `72b7454`
- **Worktree status at checkpoint:** Task 14 implementation committed; README branding rewrite remains intentionally unstaged and isolated until Task 23; Task 15 is the next implementation slice
- **Baseline commands:** root `python -m pytest -q` for the HA Integration; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --ignore=tests/test_update_script.py -p pytest_asyncio.plugin -p pytest_timeout` from `bridge_service` for the legacy service suite on Linux
- **Baseline result:** Integration 157/157 on Linux, including 41 focused Task 14 HTTP contracts; 66 Bridge upload/artifact/security contracts passed; the prior full Bridge baseline remains 996 passed/1 skipped with the PowerShell updater module excluded; Ruff, `compileall`, diff checks, HA task-leak checks, and the 100 MiB RSS/tracemalloc/temp-file smoke passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** one `CodexAppServerClient` is the HA process/credential owner for catalogue, account/limits, authentication, and `RuntimeBroker` turns; `BridgeRunner` plus subprocess probes remain explicitly deprecated external rollback adapters and are not composed in HA mode.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept shared runtime ownership, generation-aware model/limit recovery, direct-chat default reconciliation, bounded account/catalogue reads, version-mismatch diagnostics, reverse lifecycle cleanup, and fail-closed readiness across Windows and Linux. The real sandbox self-test remains intentionally fatal until Task 21; proxy, backup, Integration streaming, runtime attachment representation, App image behavior, and target-HA acceptance remain release gates.
- **Decision:** continue.
