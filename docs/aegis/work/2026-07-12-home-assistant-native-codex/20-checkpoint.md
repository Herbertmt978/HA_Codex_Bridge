# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Extend the HA-native release into a complete Codex application surface: durable scheduled automations, safe remote MCP configuration/OAuth, managed skills/plugins/marketplaces, fixed global and project `AGENTS.md`, and Codex-desktop-style navigation and run telemetry.
- **Active slice:** Bridge capability persistence/routes, administrator-only HA WebSocket proxying/scheduling, unattended-run safety, and the matching desktop-style frontend are implemented in the `live-acceptance` worktree. Local source, browser, reproducible-context, Docker-image, exact Codex `0.144.4` contract, and independent release-review gates are green; Linux CI and target-HA release acceptance remain.
- **Completed:** approved spec/plan and implementation Tasks 1–24; signed immutable App `0.6.5`; Integration `0.6.5`; private-IP Supervisor discovery; exactly one installed/running App; ChatGPT Pro authorization; dynamic model/reasoning discovery and bundled fallback; disabled-five-hour and weekly-only usage rendering; exact-response chat smoke; App update and explicit restart recovery; dependency consolidation into one weekly Dependabot group.
- **Evidence refs:** `90-evidence.md` preserves the bounded live-accepted `0.6.5` evidence. App/Integration `0.6.6` is the prior signed baseline. Published `0.7.0` uses generic digest `sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea` and amd64 child `sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`; CI `29471288344` and publication `29471288457` succeeded with signature, SBOM, and provenance.
- **Release state:** `0.7.0` is published/signed and has bounded target-HA evidence. The target run retained ChatGPT Pro, showed dynamic GPT-5.6, five-hour `Off`, chat/history, App auto-update, and MCP opt-in persistence after restart. Management forms lose unsaved values during background rerender; the `0.7.1` candidate contains the fix.
- **Blocked on:** Retesting automation, skills, plugins/marketplaces, MCP-server, and global/project `AGENTS.md` mutations after the `0.7.1` fix, plus external Nabu Casa/Cloudflare routing, cold restore, and previous-image rollback evidence.
- **Next step:** retest each management mutation against the `0.7.1` candidate, then refresh the bounded target-HA acceptance record.

## Workflow state

- **Package Manager:** npm (via root `package-lock.json`)
- **Frontend:** framework-free JavaScript Web Component bundled with esbuild
- **Verification:** `npm run lint`, `npm run test:unit`, `npm run build`, Python/Ruff/HA contract suites, App packaging/security checks, then target-HA acceptance

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/live-acceptance`
- **Branch:** `Herb/0.7.1-form-state`
- **Integrated main head:** `0dd6c7f` (release `0.7.0`).
- **Worktree status at checkpoint:** The `0.7.1` management-form fix, release projections, regression tests, and evidence updates are intentionally uncommitted pending final review/CI. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Candidate Integration `0.7.1`, App `0.7.1`, Bridge `0.6.0`, and Codex `0.144.4`; the published/signed `0.7.0` baseline has bounded target-HA evidence and management-mutation retest remains open.
- **Historical image evidence:** Signed immutable App `0.6.6` digest is `sha256:aab2882333a70354624c5ec3a461f738f5a3495ab5340b3161f4e941c6fe4767`; signed and live-accepted App `0.6.5` digest is `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`. Neither may be attributed to `0.7.0`.
- **Required readback on resume:** `10-intent.md`, this file, the approved spec/plan, current `git status`, latest main/PR/release state, and the final live-acceptance section in `90-evidence.md`.

## DriftCheckDraft

- **Intent alignment:** yes. Browser traffic remains on Home Assistant; only the private App/Bridge contacts Codex/OpenAI.
- **Compatibility:** API v1 Supervisor discovery is primary; explicit private external v0 remains the recovery path. The candidate Bridge is `0.6.0`; granular authenticated capabilities make new Integration/old App pairings fail locally with update guidance.
- **Discovery boundary:** publisher and consumer accept only literal RFC1918/ULA App IPs. Tokens are validated only against that origin and are never placed in browser-visible configuration or logs.
- **Restart recovery:** each publication retains the Supervisor UUID and changes only a bounded non-secret marker so Supervisor re-pushes an otherwise equal discovery record.
- **Failure behavior:** a valid but unreachable App remains visible for administrator retry and is not persisted or used to replace an existing entry until authenticated readiness succeeds.
- **Model/limits behavior:** catalogue and reasoning levels remain Codex-discovered; stale results retry after 15 seconds and prefer verified last-known-good over the installed bundled catalogue, with static fallback last. GPT-5.6 and model-specific Max/Ultra are not hardcoded. Account changes expire stale entitlement data; duration-classified limits represent weekly-only and disabled five-hour windows correctly.
- **Panel behavior:** `0.7.0` retains the Codex-style chat tree and adds live action/streaming/step telemetry plus Scheduled, Skills, Plugins, MCP, Instructions, About, Security, and system-information surfaces. Typed transient artifact reservations preserve the previous artifact snapshot and do not become a connection error, even where the selected chat is idle.
- **MCP boundary:** MCP is disabled by default and requires an explicit App option plus restart. Every pre-service and production app-server path suppresses MCP while disabled; startup removes the durable native MCP root with a compare-and-swap write and fails readiness closed if cleanup cannot be proved. HTTPS/DNS checks remain best effort rather than connection-time egress enforcement.
- **Release discipline:** App images remain immutable. `0.7.0` is signed/published and the first unattended App update is proven; Supervisor arbitrary prior-image selection is not validated. Cold restore, previous-image recovery, and external blocked-network routing remain open evidence. Management mutation acceptance is superseded pending `0.7.1` retest.
- **Decision:** retain the bounded historical acceptance records and separate the `0.7.1` management-mutation retest from the broader recovery gates.
