# Project context: Home Assistant Codex Bridge

## Purpose

Home Assistant Codex Bridge keeps Home Assistant as the browser-facing control
plane for Codex:

```text
Browser -> Home Assistant -> private Supervisor App or external Bridge -> Codex / OpenAI
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

- Integration: `0.6.0`; optional external Bridge: `0.5.3`.
- App: `0.6.2`, experimental and `amd64` only; bundled Codex: `0.144.4`.
- The public App `0.6.2` release is a signed immutable image with an SPDX SBOM
  and build provenance.
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
- The published App `0.6.2` image passed target-HAOS startup, its production
  sandbox self-test and attestation, an authenticated API v1 readiness request,
  Supervisor discovery, Integration pairing, and panel loading. A redacted
  ChatGPT device-login start/cancel cycle also passed; completing account
  authorization still requires the user. Remote access, the first unattended
  automatic update, cold restore, and App-image rollback on the intended
  installation remain acceptance gates. The current recovery plan is a cold
  backup and, if already operated, a private external Bridge. Do not claim
  Supervisor can choose an arbitrary earlier image until a prior immutable tag
  and restore procedure are published and exercised.

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
