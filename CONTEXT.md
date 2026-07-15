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

## Current compatibility statement

- Current release being shipped: Integration `0.6.6`, App `0.6.6`
  (experimental and `amd64` only), optional external Bridge `0.5.5`, and bundled
  Codex `0.144.4`. Publication, signing, and target-Home-Assistant acceptance
  remain pending; no App image digest is recorded for `0.6.6` yet.
- The prior `0.6.5` matrix is signed and live-accepted within the historical
  boundaries recorded in `90-evidence.md`; do not apply that evidence to `0.6.6`.
- Supervisor discovery advertises a validated private App IP, retains its
  stable Supervisor UUID, and changes a bounded non-secret marker on every
  start so Home Assistant re-delivers otherwise unchanged discovery. The
  Integration keeps a valid-but-temporarily-unreachable discovery visible for
  retry and never persists it before authenticated readiness succeeds.
- Device-login recovery uses bounded authoritative account checks; account
  entitlement changes invalidate the signed-out model catalogue before project
  defaults are reconciled. Model and reasoning choices stay runtime-discovered.
  If live app-server discovery fails, the `0.6.6` release uses the installed
  Codex bundled catalogue dynamically, retries stale data after 15 seconds,
  prefers a verified last-known-good record, and uses a static fallback only as
  the final recovery layer. GPT-5.6 and per-model Max/Ultra options appear only
  when the runtime advertises them.
- Usage windows are classified by advertised duration, and a successful chat
  creation remains usable while secondary snapshots retry.
- A typed, temporary artifact-scan reservation preserves the previous artifact
  snapshot and does not turn a healthy chat or completed response into a false
  connection failure, even where the selected chat is idle.
- The `0.6.6` release keeps the chat surface at a bounded reading width with a
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
- The live App `0.6.5` passed target-HAOS startup, production sandbox
  self-test/attestation, authenticated API v1 readiness, Supervisor discovery,
  Integration pairing, ChatGPT Pro sign-in, runtime chat, and explicit App
  restart recovery. This historical result does not accept `0.6.6`; external
  blocked-network/Nabu Casa/Cloudflare routing, cold restore, the first future
  unattended App update, and previous-image rollback remain unproven.

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
