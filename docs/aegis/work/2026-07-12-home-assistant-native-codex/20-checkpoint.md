# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** bounded release acceptance complete
- **Current todo:** Maintain the bounded `0.8.3` release matrix and close the explicitly open artifact, remote-routing, restore, rollback, and browser-worker acceptance gates.
- **Active slice:** App/Integration/panel `0.8.3` with Bridge `0.7.2` completes the next Codex-desktop parity slice: a shared 840-pixel reading rail, continuous transcript, compact composer, floating Activity information card, settled terminal subagent counts, local-only secondary artifact failures, and authoritative idle-state recovery.
- **Completed:** HA-native App/Integration runtime, ChatGPT account login, dynamic models/reasoning, limits, live search, image-artifact contract, durable automations, skills/plugins/MCP/instructions surfaces, run stages/subagent summaries, safe local PDF.js preview, signed App `0.8.3`, and a bounded target smoke proving retained account/history/version state, stale-run recovery, normal GPT-5.6-Sol completion, and native web-search activity.
- **Evidence refs:** `90-evidence.md` records the historical `0.8.1` failed PDF gate, the `0.8.2` signed publication and target smoke, and the published `0.8.3` exact-main release/live matrix.
- **Release state:** `0.8.3` is published and target-smoked. Main commit `913c08d3393574f799baf0b47e78d31422c12fe1`, CI `29544350904`, App publication `29544351022`, immutable digest `sha256:bd8c9b1e275e5f832a64d81d8aabb163c8f8d4e755ec317a6eeac530788741fa`, and provenance attestation `35745773` are recorded in the evidence file.
- **Open boundaries:** the current PDF artifact scan still returns typed local Files conflict `409`; external Nabu Casa/Cloudflare routing, cold restore, arbitrary image rollback, and the secure App-owned browser worker remain unproven. A manual paired HACS release gap was found; its paired-release workflow is policy-tested, while the first live automatic exercise remains the next App release.
- **Next step:** keep the bounded release matrix current and close the explicitly open artifact, remote-routing, restore, rollback, and browser-worker acceptance gates.

## Workflow state

- **Package Manager:** npm (via root `package-lock.json`)
- **Frontend:** framework-free JavaScript Web Component bundled with esbuild
- **Verification:** `npm run lint`, `npm run test:unit`, `npm run build`, Python/Ruff/HA contract suites, App packaging/security checks, then target-HA acceptance

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/083-codex-parity`
- **Branch:** `Herb/0.8.3-release-automation`
- **Integrated main head:** `913c08d3393574f799baf0b47e78d31422c12fe1` (release `0.8.3`).
- **Worktree status at checkpoint:** The follow-up is scoped to paired-release automation, its policy tests, and release evidence; the original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Published and bounded target-smoked Integration/App/panel `0.8.3`, Bridge `0.7.2`, and Codex `0.144.5`; signed-but-failed target exercise `0.8.1`, signed target smoke `0.8.2`, and fully accepted `0.7.5` remain bounded historical evidence.
- **Historical image evidence:** Signed immutable App `0.6.6` digest is `sha256:aab2882333a70354624c5ec3a461f738f5a3495ab5340b3161f4e941c6fe4767`; signed and live-accepted App `0.6.5` digest is `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`. Neither may be attributed to `0.7.0`.
- **Required readback on resume:** `10-intent.md`, this file, the approved spec/plan, current `git status`, latest main/PR/release state, and the final live-acceptance section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the recovery path. The published Bridge is `0.6.0`; granular authenticated capabilities make new Integration/old App pairings fail locally with update guidance.
- **Discovery boundary:** publisher and consumer accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; stale results retry after 15 seconds and prefer verified last-known-good over the installed bundled catalogue, with static fallback last. GPT-5.6 and model-specific Max/Ultra are not hardcoded. Account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Panel behavior:** `0.7.0` retains the Codex-style chat tree and adds live action/streaming/step telemetry plus Scheduled, Skills, Plugins, MCP, Instructions, About, Security, and system-information surfaces. Typed transient artifact reservations preserve the previous artifact snapshot and do not become a connection error, even where the selected chat is idle.
- **MCP boundary:** MCP is disabled by default and requires an explicit App option plus restart. Every pre-service and production app-server path suppresses MCP while disabled; startup removes the durable native MCP root with a compare-and-swap write and fails readiness closed if cleanup cannot be proved. HTTPS/DNS checks remain best effort rather than connection-time egress enforcement.
- **Release discipline:** App images remain immutable. `0.8.3` is signed, published, and target-smoked with bounded acceptance; its PDF/archive/restore, remote-routing, rollback, and browser-worker boundaries remain explicitly open. Cold restore and previous-image recovery are not implied by a successful update.
- **Decision:** retain historical `0.7.5`, `0.8.1`, and `0.8.2` evidence, and record `0.8.3` as the latest bounded release/live matrix rather than claiming the open capabilities.
