# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** active
- **Current todo:** Extend the HA-native release into a complete Codex application surface: durable scheduled automations, safe remote MCP configuration/OAuth, managed skills/plugins/marketplaces, fixed global and project `AGENTS.md`, and Codex-desktop-style navigation and run telemetry.
- **Active slice:** corrective App/Integration/panel `0.8.2` with Bridge `0.7.2` restores aggregate filesystem-scan error semantics, contains secondary artifact failures in **Files**, and aligns the wide HA panel with the Codex desktop rails, conversation axis, composer, full-height context surface, and user-invoked Focus mode. Local regression and release gates, signed publication, and target-HA acceptance remain.
- **Completed:** HA-native App/Integration runtime, ChatGPT account login, dynamic models/reasoning, limits, live search, image-artifact contract, durable automations, skills/plugins/MCP/instructions surfaces, run stages/subagent summaries, safe local PDF.js preview, and signed App `0.8.1`. The 0.8.1 live exercise retained account/history/models/limits but failed the required PDF gate.
- **Evidence refs:** `90-evidence.md` records signed `0.8.1` generic digest `sha256:2df98ca0452262a8336b82ec4842ba681c49b44c22a28983a7a10b3d9692e8a2`, amd64 payload `sha256:83074645bb03000884e5b13e05501899929dd41c99ef1aa228fccb636adae537`, publication run `29527193037`, and the bounded target failure caused by stale sandbox-test debris plus HTTP 400 misclassification.
- **Release state:** `0.8.1` is the latest published signed release; `0.7.5` remains the latest fully target-HA-accepted record. Candidate `0.8.2` is not published or accepted and must not be assigned an image digest or successful target result yet.
- **Blocked on:** complete local/CI review, signed `0.8.2` publication, then target-HA proof of typed local Files recovery, controlled acceptance-debris cleanup, PDF index/archive/preview, retained runtime state, and responsive geometry. External blocked-network/Nabu Casa/Cloudflare routing, cold restore, and arbitrary previous-image recovery remain open.
- **Next step:** clear the 0.8.2 release gates, publish the immutable image, install both components on the target, and execute the bounded live matrix.

## Workflow state

- **Package Manager:** npm (via root `package-lock.json`)
- **Frontend:** framework-free JavaScript Web Component bundled with esbuild
- **Verification:** `npm run lint`, `npm run test:unit`, `npm run build`, Python/Ruff/HA contract suites, App packaging/security checks, then target-HA acceptance

## ResumeStateHint

- **Repository:** repository root
- **Worktree:** `.worktrees/082-alignment`
- **Branch:** `Herb/0.8.2-alignment`
- **Integrated main head:** `ceecf338fd63816de545ee6ad7c8b8430612bbe8` (release `0.8.1`).
- **Worktree status at checkpoint:** 0.8.2 backend/frontend regressions, release projections, and candidate evidence are intentionally uncommitted pending full review/CI. The original main worktree's unrelated user edits remain untouched.
- **Current focused matrix:** Candidate Integration/App/panel `0.8.2`, Bridge `0.7.2`, and Codex `0.144.5`; signed-but-failed target exercise `0.8.1` and fully accepted `0.7.5` remain bounded historical evidence.
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
- **Release discipline:** App images remain immutable. `0.8.1` is signed and published but failed the bounded target PDF gate; `0.7.5` remains the latest fully accepted matrix. Candidate `0.8.2` is unpublished and must clear CI, signed-image provenance, and the real target gates before its status changes. Cold restore, previous-image recovery, and external blocked-network routing remain open evidence.
- **Decision:** retain `0.7.5` as the accepted baseline, retain the exact failed `0.8.1` evidence, and represent `0.8.2` only as a candidate until the live matrix passes.
