# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Task 26 — ship Integration/App `0.6.6` with Bridge `0.5.5` and Codex `0.144.4`, then complete its publication, signing, and target-Home-Assistant acceptance.
- **Active slice:** release metadata and Codex-style left-rail panel work are prepared; the `0.6.5` live-acceptance record remains historical.
- **Completed:** approved spec/plan and implementation Tasks 1–24; signed immutable App `0.6.5`; Integration `0.6.5`; private-IP Supervisor discovery; exactly one installed/running App; ChatGPT Pro authorization; dynamic model/reasoning discovery and bundled fallback; disabled-five-hour and weekly-only usage rendering; exact-response chat smoke; App update and explicit restart recovery; dependency consolidation into one weekly Dependabot group.
- **Evidence refs:** `90-evidence.md` preserves the signed, live-accepted `0.6.5` evidence. No immutable `0.6.6` image digest or live-acceptance evidence exists yet.
- **Release state:** `0.6.6` is being shipped, experimental, and `amd64` only. Do not claim it is published, signed, or live-accepted; do not apply the `0.6.5` evidence to it.
- **Blocked on:** publication/signing and target-Home-Assistant acceptance for `0.6.6`, plus the existing external Nabu Casa/Cloudflare route, cold restore, first unattended App-update canary, and previous-image recovery evidence gaps.
- **Next step:** publish and install `0.6.6` on the target Home Assistant, rerun the release acceptance, then separately exercise the four broader recovery boundaries.

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/live-acceptance`
- **Branch:** `Herb/0.6.6-left-rail`
- **Integrated main head:** `854bd10` (release `0.6.5`).
- **Worktree status at checkpoint:** Release projections, catalogue recovery, artifact-refresh handling, compact sidebar work, and public documentation are intentionally uncommitted pending final verification. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Integration `0.6.6`, App `0.6.6`, Bridge `0.5.5`, and Codex `0.144.4` are being shipped. Publication, signing, and live acceptance are pending.
- **Historical image evidence:** Signed immutable App `0.6.5` digest is `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`; it must not be attributed to `0.6.6`.
- **Required readback on resume:** `10-intent.md`, this file, the approved spec/plan, current `git status`, latest main/PR/release state, and the final live-acceptance section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the recovery path. The released Bridge is `0.5.5`.
- **Discovery boundary:** publisher and consumer accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; stale results retry after 15 seconds and prefer verified last-known-good over the installed bundled catalogue, with static fallback last. GPT-5.6 and model-specific Max/Ultra are not hardcoded. Account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Panel behavior:** `0.6.6` uses a clean Codex-style left navigation tree, title-first chat rows, one action menu, correct archive collapse/search and search icon, 44px mobile targets, transcript-adjacent decisions, and collapsed mobile settings/limits. Typed transient artifact reservations preserve the previous artifact snapshot and do not become a connection error, even where the selected chat is idle.
- **Release discipline:** App images remain immutable; only the `0.6.5` image is signed and live-accepted within the recorded boundaries. `0.6.6` needs fresh release evidence. Supervisor arbitrary prior-image selection is not validated. Cold restore, first automatic update, previous-image recovery, and external blocked-network routing remain open evidence.
- **Decision:** retain the bounded `0.6.5` acceptance record and complete `0.6.6` acceptance plus the four broader recovery gates separately.
