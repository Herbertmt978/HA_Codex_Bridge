# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 25 — publish and complete target-Home-Assistant/recovery acceptance for the candidate matrix: Integration `0.6.5`, App `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4`.
- **Active slice:** complete release checks and publication for the candidate matrix, then install it on the target Home Assistant and rerun chat plus App restart recovery.
- **Completed:** approved spec/plan and implementation Tasks 1–24; signed immutable App `0.6.4`; Integration `0.6.4`; private-IP Supervisor discovery; target-HA installation; one App instance; ChatGPT device authorization; dynamic model/reasoning discovery; disabled-five-hour and weekly-only usage rendering; exact-response chat smoke; dependency consolidation into one weekly Dependabot group.
- **Evidence refs:** `90-evidence.md`; App image `sha256:0fa57cba4a1b76dc673e8d79b098724a13b7d15e8541d6731d507da4cc72a863`; Integration `0.6.4` tag `979cd81aabe76eb2b64a6bb5f6b2df074b248cd7`.
- **Release state:** candidate/pending publication. Do not claim `0.6.5` is published, signed, or live-accepted.
- **Blocked on:** no implementation blocker. Publication/signing, target-HA installation, chat/restart recovery, external blocked-network/remote-route proof, cold restore, the first unattended App update, and prior-image recovery remain acceptance gates.
- **Next step:** publish the candidate matrix, install it on the target Home Assistant, prove catalogue recovery and chat/stop-start recovery without a stale banner, then record the remaining recovery evidence separately.

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/live-acceptance`
- **Branch:** `Herb/live-acceptance-fixes`
- **Integrated main head:** `f0bf4a7` (grouped weekly dependency maintenance merged after release `0.6.4`).
- **Worktree status at checkpoint:** Candidate version projections, catalogue recovery, artifact-refresh handling, compact sidebar work, and public documentation are intentionally uncommitted pending final verification. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Integration `0.6.5`, App `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4` are candidate/pending publication. Broader CI and target-HA acceptance are the next gates.
- **Published image evidence:** Historical App `0.6.4` is signed and immutable with an SPDX SBOM and provenance; its runtime reports Bridge `0.5.4` and Codex `0.144.4`. No corresponding `0.6.5` publication or signature is claimed.
- **Required readback on resume:** `10-intent.md`, this file, the approved spec/plan, current `git status`, latest main/PR/release state, and the final live-acceptance section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the recovery path. The candidate Bridge is `0.5.5`.
- **Discovery boundary:** publisher and consumer accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; stale results retry after 15 seconds and prefer verified last-known-good over the installed bundled catalogue, with static fallback last. GPT-5.6 and model-specific Max/Ultra are not hardcoded. Account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Panel behavior:** the compact Codex-style sidebar remains accessible within Home Assistant themes. Typed transient artifact reservations preserve the previous artifact snapshot and do not become a connection error, even where the selected chat is idle.
- **Release discipline:** App images remain immutable; no claim is made that the `0.6.5` candidate is published, signed, or live-accepted, or that Supervisor can select an arbitrary prior image. Cold restore, first automatic update, and previous-image recovery remain open acceptance evidence.
- **Decision:** complete publication and target-HA validation for the bounded `0.6.5` candidate matrix, then keep the broader recovery gates explicit.
