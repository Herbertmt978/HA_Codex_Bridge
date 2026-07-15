# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 25 — publish Integration `0.6.3` and App `0.6.4`, install them on the target Home Assistant, and complete the post-release smoke checks.
- **Active slice:** Recover Supervisor discovery by advertising the App's validated private IP, force safe rediscovery after restarts, retain unreachable discovery for retry, and close superseded dependency PRs.
- **Completed:** approved spec/plan; implementation Tasks 1–24; releases through Integration `0.6.2` / App `0.6.3`; private-IP recovery patch; consumer-side discovery confinement; retryable panel/config-flow UX; dependency consolidation; local production image and full regression gates.
- **Evidence refs:** `90-evidence.md`; local candidate image `sha256:86d0ec5fba3eba371699b208cd028e5e503148673a1840bdc336a658a60f9248`.
- **Blocked on:** no implementation blocker. PR review, signed publication, target-HA installation, cold restore/update canary, remote blocked-network proof, and rollback evidence remain acceptance gates.
- **Next step:** commit and push the recovery branch, open the release PR, address automated review/CI, merge and publish, then update/install on the target Home Assistant and run the live smoke matrix.

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/ha-app`
- **Branch:** `Herb/ha-discovery-recovery`
- **Integrated main head:** `3e3996e` (Dependabot `httpx` and Docker login-action updates merged).
- **Worktree status at checkpoint:** recovery/release changes are intentionally uncommitted pending final diff review; the original main worktree's unrelated user edits remain untouched.
- **Local release matrix:** Integration 170/170 in disposable Linux HA 2026.7.2; Bridge 1092 passed/188 platform skips; frontend 142/142 plus Playwright 11/11; App/release focused 100 passed/3 skips; protocol/App security 78 passed/1 skip; Ruff, ESLint, compileall, deterministic bundle, lock sync, and App projection checks passed.
- **Image evidence:** hermetic amd64 staging succeeded with `build==1.5.0`, `setuptools==83.0.0`, and `wheel==0.47.0`; the built image reports App `0.6.4`, Bridge `0.5.4`, and Codex `0.144.4`; the pinned base exposes the verified `bashio::addon.ip_address` helper.
- **Required readback on resume:** `10-intent.md`, this file, approved spec/plan, current `git status`, latest main/PR/release state, and the final recovery section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the rollback path.
- **Discovery boundary:** publisher and consumer both accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Release discipline:** App images remain immutable; no claim is made that Supervisor can select an arbitrary prior image. Cold restore, first automatic update, and previous-image recovery remain open acceptance evidence.
- **Decision:** continue to PR/release and live target-HA acceptance.
