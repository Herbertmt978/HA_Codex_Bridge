# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 13 — replace per-panel polling with one Integration event broker
- **Active slice:** Give each config entry one cancellable global event consumer with replay, fan-out, gap recovery, and bounded subscribers
- **Completed:** approved spec and plan; Tasks 1–12; Supervisor discovery now owns confirmed v1 setup, stable identity, credential rotation, and single-entry lifecycle
- **Evidence refs:** `90-evidence.md`; Task 12 implementation commit `e730c36`
- **Blocked on:** no implementation blocker; real HA inherited-descriptor, process-group, and sandbox behavior remain acceptance gates
- **Next step:** write Task 13 RED contracts for one upstream event consumer, scoped replay/fan-out, cursor persistence, gap snapshots, bounded slow subscribers, reconnect, token changes, and unload

## ResumeStateHint

- **Repository:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge`
- **Worktree:** `C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge\.worktrees\ha-app`
- **Branch:** `Herb/ha-app`
- **Current implementation head before this checkpoint update:** `e730c36`
- **Worktree status at checkpoint:** Task 12 implementation committed; README branding rewrite remains intentionally unstaged and isolated until Task 23; Task 13 is the next implementation slice
- **Baseline commands:** root `python -m pytest -q` for the HA Integration; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --ignore=tests/test_update_script.py -p pytest_asyncio.plugin -p pytest_timeout` from `bridge_service` for the legacy service suite on Linux
- **Baseline result:** Integration 82/82 on Linux, including 23 focused Task 12 contracts; Task 11 remained 59/59 on Windows; Bridge 996 passed/1 skipped on Linux with the PowerShell updater module excluded; Ruff, `compileall`, and diff checks passed
- **Required readback on resume:** `10-intent.md`, this file, approved spec, plan, current `git status`, latest commits, and evidence file

## DriftCheckDraft

- **Intent alignment:** yes; HA authentication, turns, approvals, questions, cancellation, events, private binary transport, and concurrency remain behind the supervised Bridge boundary.
- **Compatibility:** v0 adapter and VM rollback remain explicit.
- **New owner/fallback:** one `CodexAppServerClient` is the HA process/credential owner for catalogue, account/limits, authentication, and `RuntimeBroker` turns; `BridgeRunner` plus subprocess probes remain explicitly deprecated external rollback adapters and are not composed in HA mode.
- **Attachment boundary:** completed uploads are private and unselected by default; generic runtime representation is deferred until Tasks 10–17 can negotiate only schema-supported inputs or explicit workspace import.
- **Retirement:** unchanged; VM stops only after real acceptance.
- **Evidence sufficiency:** sufficient to accept shared runtime ownership, generation-aware model/limit recovery, direct-chat default reconciliation, bounded account/catalogue reads, version-mismatch diagnostics, reverse lifecycle cleanup, and fail-closed readiness across Windows and Linux. The real sandbox self-test remains intentionally fatal until Task 21; proxy, backup, Integration streaming, runtime attachment representation, App image behavior, and target-HA acceptance remain release gates.
- **Decision:** continue.
