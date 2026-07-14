# Codex Bridge App

The Codex Bridge App is the private Supervisor runtime for Home Assistant Codex
Bridge. The companion `codex_bridge` Integration is the browser-facing
administrator panel and connects to this App through Supervisor.

## Status

- App version: `0.6.1` (`amd64` only, experimental)
- Integration: `0.5.4`
- External Bridge: `0.5.3`
- Bundled Codex: `0.144.4`
- App repository: <https://github.com/Herbertmt978/HA_Codex_Bridge>

The public immutable image is signed and accompanied by an SPDX SBOM and build
provenance. A protected-runtime image passed sandbox self-test and authenticated
readiness on an amd64 Home Assistant OS development VM on 14 July 2026. Remote
access, the first automatic update, and prior-image recovery remain acceptance
checks for the intended Home Assistant installation.

## Installation model

Add this repository to the Home Assistant App store, install the App, then
install the Integration through HACS. Supervisor discovery supplies the private
Integration-to-App connection; an administrator does not enter a Bridge address,
port, or bearer token.

The App exposes no browser-facing port or ingress route. Reach the panel through
Home Assistant. HACS and Home Assistant references describe compatible
installation surfaces only; they do not imply endorsement by those projects.

## Storage and authentication

The App's writable host mapping is its dedicated `addon_config` directory at
`/config`. User workspaces live below `/config/workspaces`. Private Bridge state
and ChatGPT device-login state live in the App-private `/data` volume.

From the Home Assistant panel, select **Sign in with ChatGPT** and complete the
approved ChatGPT device-auth page in a browser. **Cancel** stops an unfinished
sign-in; **Sign out** removes the established session. Once signed in, normal
panel use stays on Home Assistant, but re-authentication again needs access to
the approved ChatGPT page. This flow does not use an OpenAI API key.

## Updates and recovery

The running image never replaces Codex or itself. Home Assistant can install a
newly released image and can apply it automatically after the App auto-update
toggle is enabled. Do not assume Supervisor can select an arbitrary earlier App
image: App-image rollback is not yet validated. Use a cold Home Assistant backup
or an existing private external Bridge for recovery, and keep workspaces until
their contents have been reviewed.

See [DOCS.md](DOCS.md), the repository [installation guide](../docs/installation.md),
and [backup and recovery](../docs/backup-restore.md).
