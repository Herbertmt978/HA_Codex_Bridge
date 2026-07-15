# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 25 — finish target-Home-Assistant and recovery acceptance for Integration `0.6.5` with App `0.6.4`.
- **Active slice:** remove the false connection banner caused by an expected artifact-scan reservation during an active run, publish the Integration fix, reinstall it on the target Home Assistant, and rerun chat plus App restart recovery.
- **Completed:** approved spec/plan and implementation Tasks 1–24; signed immutable App `0.6.4`; Integration `0.6.4`; private-IP Supervisor discovery; target-HA installation; one App instance; ChatGPT device authorization; dynamic model/reasoning discovery; disabled-five-hour and weekly-only usage rendering; exact-response chat smoke; dependency consolidation into one weekly Dependabot group.
- **Evidence refs:** `90-evidence.md`; App image `sha256:0fa57cba4a1b76dc673e8d79b098724a13b7d15e8541d6731d507da4cc72a863`; Integration `0.6.4` tag `979cd81aabe76eb2b64a6bb5f6b2df074b248cd7`.
- **Blocked on:** no implementation blocker. External blocked-network/remote-route proof, cold restore, the first unattended App update, and prior-image recovery remain acceptance gates.
- **Next step:** merge and publish Integration `0.6.5`, install it on the target Home Assistant, prove chat and stop/start recovery without a stale banner, then record the remaining recovery evidence separately.

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/live-acceptance`
- **Branch:** `Herb/live-acceptance-fixes`
- **Integrated main head:** `f0bf4a7` (grouped weekly dependency maintenance merged after release `0.6.4`).
- **Worktree status at checkpoint:** Integration `0.6.5` artifact-refresh regression fix, version projections, and public documentation are intentionally uncommitted pending final verification. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** frontend lint and 171 unit tests pass; the deterministic generated panel was rebuilt and `git diff --check` passes. Broader CI and target-HA `0.6.5` acceptance are the next gates.
- **Published image evidence:** App `0.6.4` is signed and immutable with an SPDX SBOM and provenance; its runtime reports Bridge `0.5.4` and Codex `0.144.4`.
- **Required readback on resume:** `10-intent.md`, this file, the approved spec/plan, current `git status`, latest main/PR/release state, and the final live-acceptance section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the recovery path.
- **Discovery boundary:** publisher and consumer accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Release discipline:** App images remain immutable; no claim is made that Supervisor can select an arbitrary prior image. Cold restore, first automatic update, and previous-image recovery remain open acceptance evidence.
- **Decision:** ship the bounded Integration `0.6.5` refresh fix, verify it on the target Home Assistant, and keep the broader recovery gates explicit.
