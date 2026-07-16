# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Extend the HA-native release into a complete Codex application surface: durable scheduled automations, safe remote MCP configuration/OAuth, managed skills/plugins/marketplaces, fixed global and project `AGENTS.md`, and Codex-desktop-style navigation and run telemetry.
- **Active slice:** Bridge capability persistence/routes, administrator-only HA WebSocket proxying/scheduling, unattended-run safety, and the matching desktop-style frontend are implemented in the `live-acceptance` worktree. Local source, browser, reproducible-context, Docker-image, exact Codex `0.144.4` contract, and independent release-review gates are green; Linux CI and target-HA release acceptance remain.
- **Completed:** approved spec/plan and implementation Tasks 1–24; signed immutable App `0.6.5`; Integration `0.6.5`; private-IP Supervisor discovery; exactly one installed/running App; ChatGPT Pro authorization; dynamic model/reasoning discovery and bundled fallback; disabled-five-hour and weekly-only usage rendering; exact-response chat smoke; App update and explicit restart recovery; dependency consolidation into one weekly Dependabot group.
- **Evidence refs:** `90-evidence.md` preserves the bounded live-accepted `0.6.5` evidence and the published `0.7.0` baseline. Published/live-accepted `0.7.1` uses generic digest `sha256:ec4e5f4ea48ba2333d5689879bc98a58912ae15ac9f90a133d30712452403184` and amd64 child `sha256:cacfb7b4a65a1b0290fe5c7da9dfa33c5ffde78f8ebaa3370fac9366c19681a6`; main CI rerun `29483810669` and publication `29483810926` succeeded ([release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.1)).
- **Release state:** `0.7.1` is published/live-accepted within the bounded target-HA evidence and remains historical for the current candidate. Candidate App/Integration `0.7.2` with Bridge `0.6.1` and Codex `0.144.4` has verified a signed-in native plugin catalogue of approximately `4,041,499` bytes, `1,916` plugins, and `35.887s` cold completion. Its app-server `8MiB` message/`60s` request, Integration `75s`/`8MiB` plugin request, `4,096` projection cap, and single frontend request are candidate fixes; live plugin acceptance is pending.
- **Blocked on:** Publishing/live-accepting the `0.7.2` plugin catalogue candidate. The historical `0.7.1` live list returned `capabilities_unavailable` (HTTP 503); that evidence remains retained, but is not a current `0.7.2` result. External Nabu Casa/Cloudflare routing, cold restore, and arbitrary previous-image recovery gates also remain open.
- **Next step:** complete the separate `0.7.2` plugin live-acceptance gate, then exercise the remaining external-routing, cold-restore, and previous-image recovery gates.

## Workflow state

- **Package Manager:** npm (via root `package-lock.json`)
- **Frontend:** framework-free JavaScript Web Component bundled with esbuild
- **Verification:** `npm run lint`, `npm run test:unit`, `npm run build`, Python/Ruff/HA contract suites, App packaging/security checks, then target-HA acceptance

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/live-acceptance`
- **Branch:** `Herb/0.7.2-plugin-catalog`
- **Integrated main head:** `0dd6c7f` (release `0.7.0`).
- **Worktree status at checkpoint:** The `0.7.2` plugin-catalogue fixes, release projections, regression tests, and candidate evidence updates are intentionally uncommitted pending final review/CI. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Candidate Integration/App `0.7.2`, Bridge `0.6.1`, and Codex `0.144.4`; published/live-accepted `0.7.1` and the published `0.7.0` baseline remain historical bounded evidence.
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
- **Release discipline:** App images remain immutable. `0.7.1` is published/live-accepted and its first unattended App update is proven; the `0.7.2` candidate is not published. The candidate catalogue fixes remain bounded by `8MiB`/`60s` app-server limits, `75s`/`8MiB` Integration limits, a `4,096` projection cap, and one frontend request. Cold restore, previous-image recovery, and external blocked-network routing remain open evidence.
- **Decision:** retain `0.7.1` as the historical live-accepted record, document the measured `0.7.2` catalogue and fixes as candidate evidence, and keep plugin live acceptance separate from the broader recovery gates.
