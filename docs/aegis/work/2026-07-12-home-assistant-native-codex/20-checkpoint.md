# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 12 — implement Supervisor discovery, stable identity, and token rotation
- **Active slice:** Make App discovery the primary zero-copy setup path while retaining an explicit capability-limited external v0 fallback
- **Completed:** approved spec and plan; Tasks 1–11; the HA client now has immutable protocol authority, authenticated readiness negotiation, bounded transport, typed failures, and caller-owned streaming
- **Evidence refs:** `90-evidence.md`; Task 11 implementation commit `c3749bf`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 12 RED contracts for Supervisor discovery, stable UUID identity, rediscovery token/address rotation, external v0 setup, and process-lifetime HA registrations

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `c3749bf`
- **Worktree status at checkpoint:** Task 11 implementation committed; README branding rewrite remains intentionally unstaged and isolated until Task 23; Task 12 is the next implementation slice
- **Baseline commands:** root `python -m pytest -q` for the HA Integration; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --ignore=tests/test_update_script.py -p pytest_asyncio.plugin -p pytest_timeout` from `bridge_service` for the legacy service suite on Linux
- **Baseline result:** Integration 59/59 on Linux and Windows; Bridge 996 passed/1 skipped on Linux with the PowerShell updater module excluded; one known Starlette deprecation warning; Ruff, `compileall`, and diff checks passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** one `CodexAppServerClient` is the HA process/credential owner for catalogue, account/limits, authentication, and `RuntimeBroker` turns; `BridgeRunner` plus subprocess probes remain explicitly deprecated external rollback adapters and are not composed in HA mode.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept shared runtime ownership, generation-aware model/limit recovery, direct-chat default reconciliation, bounded account/catalogue reads, version-mismatch diagnostics, reverse lifecycle cleanup, and fail-closed readiness across Windows and Linux. The real sandbox self-test remains intentionally fatal until Task 21; proxy, backup, Integration streaming, runtime attachment representation, App image behavior, and target-HA acceptance remain release gates.
- **Decision:** continue.
