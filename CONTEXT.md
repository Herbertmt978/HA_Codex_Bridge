# Project context: Home Assistant Codex Bridge

## Purpose

Home Assistant Codex Bridge keeps Home Assistant as the browser-facing control
plane for Codex:

```text
Browser -> Home Assistant -> Codex Bridge Integration -> private Supervisor App or external Bridge -> Codex / OpenAI
```

Remote access terminates at Home Assistant. A browser must not connect directly
to the App or Bridge.

## Terms

| Term | Meaning | Do not use for |
| --- | --- | --- |
| **App** | The Supervisor-managed Codex Bridge runtime beside Home Assistant. | The Home Assistant integration. |
| **Integration** | The `codex_bridge` Home Assistant component, configuration flow, and administrator panel. | The App or Bridge process. |
| **Bridge** | A private service that receives authenticated Integration requests and coordinates Codex. | Codex itself. |
| **Workspace** | A deliberately granted project folder; in App mode, it is beneath `/config/workspaces`. | Home Assistant configuration or a generic broad share. |
| **Project** | A user-visible group of Codex chats with one workspace and defaults. | A workspace or repository. |
| **External Bridge** | An optional, separately operated private Bridge compatibility path. | A required Windows VM or browser endpoint. |
| **Automation** | A durable prompt definition whose due time is scheduled by Home Assistant and claimed idempotently by the Bridge. | A free-running background worker or unrestricted cron job. |
| **Skill** | A workspace-scoped Codex instruction under `.agents/skills/`. | A global executable or a path outside the workspace. |
| **MCP server** | An explicitly enabled outbound streamable-HTTP server configured with a trusted HTTPS hostname and optional OAuth metadata. | A public listener for the App, Bridge, or Home Assistant. |
| **Global/project AGENTS.md** | Global Codex instructions or an `AGENTS.md` at the selected project workspace root. | A way to grant Codex additional filesystem access. |

## Current compatibility statement

- Current candidate being shipped: Integration `0.7.1`, App `0.7.1`
  (experimental and `amd64` only), optional external Bridge `0.6.0`, and bundled
  Codex `0.144.4`. It carries the candidate fix for management forms losing
  unsaved values during background rerender; target-HA mutation retest remains
  open.
- Published/signed baseline: Integration `0.7.0`, App `0.7.0`. Its generic image
  digest is
  `sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`
  with amd64 child digest
  `sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`.
  Main CI run `29471288344` and publication run `29471288457` succeeded; the
  release includes signature, SBOM, and provenance attestations ([release
  page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0)).
  Target-Home-Assistant acceptance is bounded below; it is not a blanket
  acceptance of every capability mutation.
- App/Integration `0.6.6` is the prior signed publication. The `0.6.5` matrix
  remains live-accepted only within the historical boundaries recorded in
  `90-evidence.md`; neither historical claim supersedes the bounded `0.7.0`
  evidence above.
- Supervisor discovery advertises a validated private App IP, retains its
  stable Supervisor UUID, and changes a bounded non-secret marker on every
  start so Home Assistant re-delivers otherwise unchanged discovery. The
  Integration keeps a valid-but-temporarily-unreachable discovery visible for
  retry and never persists it before authenticated readiness succeeds.
- Device-login recovery uses bounded authoritative account checks; account
  entitlement changes invalidate the signed-out model catalogue before project
  defaults are reconciled. Model and reasoning choices stay runtime-discovered.
  If live app-server discovery fails, the `0.7.0` release uses the installed
  Codex bundled catalogue dynamically, retries stale data after 15 seconds,
  prefers a verified last-known-good record, and uses a static fallback only as
  the final recovery layer. GPT-5.6 and per-model Max/Ultra options appear only
  when the runtime advertises them.
- Usage windows are classified by advertised duration, and a successful chat
  creation remains usable while secondary snapshots retry.
- A typed, temporary artifact-scan reservation preserves the previous artifact
  snapshot and does not turn a healthy chat or completed response into a false
  connection failure, even where the selected chat is idle.
- The `0.7.0` release keeps the chat surface at a bounded reading width with a
  clean Codex-style left navigation tree, title-first chat rows, one action
  menu, correct archive collapse/search and search icon, 44px mobile targets,
  transcript-adjacent decisions, and collapsed mobile settings/limits. It
  retains theme-derived contrast and accessible disclosure, selection,
  progress, and retry state.
- On target HAOS, pinned Codex `0.144.4`'s official `--no-proc` fallback works:
  denial of a fresh `/proc` mount leaves user, PID, and network namespaces, the
  read-only filesystem, AppArmor, and seccomp enforced; `/proc` is intentionally
  empty.
- App `0.6.1`'s fatal readiness cause was a sandbox-self-test contract mismatch:
  it required `writableRoots` exactly `[workspace]`, while the real `ha_bridge`
  `workspaceWrite` response includes bounded supplemental roots (`.agents`,
  `.codex`, `.cursor`, `.git`, and `.vscode`) beneath the workspace. The
  proc-less probe already used direct `capget`/`prctl`/`lsm_get_self_attr` calls,
  without requesting `SYS_ADMIN` or weakening isolation; App `0.6.2` validates
  canonical contained supplemental roots and hardens `lsm_get_self_attr` record
  parsing.
- On target HA, App and Integration `0.7.0` reported Bridge `0.6.0` and Codex
  `0.144.4`; ChatGPT Pro remained signed in, GPT-5.6 was visible from dynamic
  runtime discovery, the five-hour window rendered `Off`, and chat/history were
  preserved. App auto-update and MCP opt-in persistence after restart were also
  observed. Management forms currently lose unsaved values during a background
  rerender; the `0.7.1` candidate contains the fix. Do not claim automation,
  skills, plugins/marketplaces, MCP-server, or `AGENTS.md` mutation acceptance
  until that candidate is retested. The first unattended App update is now
  proven. External blocked-network/Nabu Casa/Cloudflare routing, cold restore,
  and previous-image rollback remain unproven.
- The App includes administrator-only capability surfaces for durable
  automations, workspace skills, global/project `AGENTS.md`, plugins and
  marketplaces, and outbound MCP configuration. Automations are persisted in
  the Bridge while Home Assistant owns wall-clock scheduling. MCP is disabled
  by default and requires an explicit App option plus restart. When enabled it
  accepts only trusted HTTPS hostnames, rejects known non-public DNS answers,
  does not expose bearer-token configuration, and offers an explicit one-shot
  OAuth login response; MCP elicitation is declined by design.

## Product language

- Keep **Integration** and **App** distinct. HACS installs the Integration;
  Supervisor installs the App from this repository.
- ChatGPT device login and Home Assistant login are separate. Use the exact UI
  labels **Sign in with ChatGPT**, **Cancel**, and **Sign out**. Cancellation is
  only for an in-progress sign-in; sign-out removes an established session.
- Normal panel use can remain on Home Assistant after sign-in. Initial sign-in
  and re-authentication require browser access to the approved ChatGPT
  device-auth page.
- Codex discovers available models and reasoning levels at runtime. A marked
  last-known-good catalogue must not silently change a chat to another model.
- App images are immutable. Never imply that the current Supervisor App can
  roll back to an arbitrary earlier image.
- Do not describe an automation as guaranteed execution: capacity, overlap,
  pause, and misfire policies can produce a recorded skipped run. Keep the
  public contract that Home Assistant schedules and the Bridge claims.
- Keep MCP documentation explicit that configured endpoints are outbound,
  disabled by default, and limited to trusted HTTPS servers; never suggest
  exposing the App or Bridge as an MCP endpoint. Make the best-effort DNS
  limitation explicit. Never document bearer tokens, private URLs, or
  persisted OAuth authorization URLs.
