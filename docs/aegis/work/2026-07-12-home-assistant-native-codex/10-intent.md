# Home Assistant-native Codex — Task Intent

## Requested outcome

Run Codex inside Home Assistant OS and operate it entirely through the improved Home Assistant panel over any standards-compatible HA access path. Replace the Windows VM only after protected HA acceptance proves login, streaming, approvals, files, sandboxing, updates, and rollback.

## Goal and success evidence

- Supervisor App is the canonical Bridge/Codex runtime.
- HACS Integration/panel is the only browser-facing control surface.
- ChatGPT-managed device authorization works without API keys.
- Browser traffic remains on the configured HA/proxy origin; App egress reaches OpenAI from home.
- Model-controlled tools cannot reach HA/LAN/internet or read credentials/outside workspaces.
- Dynamic models, signed immutable Codex updates, backup/restore, and rollback are proven.
- README, installation, branding, badges, topics, support/security, and release surfaces meet the approved documentation gate.

## Stop conditions

- **Done candidate:** every plan task has fresh evidence, real HA acceptance passes, protected checks/release/merge agree, and the VM is stopped but retained for rollback.
- **Needs verification:** code exists but real HA, proxy, signature, update, or rollback evidence is missing.
- **Blocked:** target HA cannot provide safe Bubblewrap/AppArmor isolation or a required external service remains unavailable after repeated verified attempts.
- **Scope exceeded:** a solution requires bypassing HA protection, using API keys, exposing the App, deleting the VM, or changing employer/account policy.

## Scope

Implement the 25 tasks in `docs/aegis/plans/2026-07-12-home-assistant-native-codex.md` test-first, with per-task spec and quality review.

## Non-goals

- Importing VM chat/project history or credentials.
- Browser-to-App or browser-to-OpenAI access.
- Arbitrary HA/host paths or model-tool network access.
- Official/Community endorsement or acceptance.
- Deleting the Windows rollback path in 0.6.x.

## Risk hints

- Codex app-server schema drift and approval deadlock.
- Filesystem TOCTOU and HA internal-network exposure.
- Cross-store crash consistency between JSON state and SQLite events.
- HACS/App version skew and generated frontend drift.
- Upstream release identity and image rollback.
- Mistaking container tests for protected HA OS evidence.

## BaselineReadSetHint

- `CONTEXT.md`
- `docs/aegis/BASELINE-GOVERNANCE.md`
- `docs/aegis/baseline/2026-07-12-initial-baseline.md`
- `docs/aegis/specs/2026-07-12-home-assistant-native-codex-design.md`
- `docs/aegis/plans/2026-07-12-home-assistant-native-codex.md`
- Current files/tests listed in the plan's File Map.

## ImpactStatementDraft

- Runtime ownership moves from Windows to Supervisor App.
- Bridge public contract gains negotiated API v1 plus bounded v0 compatibility.
- Storage, auth, events, files, Integration, panel, packaging, CI, docs, and release ownership change.
- One app-server process becomes source of truth for ChatGPT auth, models, limits, turns, approvals, and questions.
- Windows stays as a rollback carrier until the acceptance/retirement trigger.
