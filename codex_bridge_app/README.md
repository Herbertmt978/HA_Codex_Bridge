# Codex Bridge App

The Codex Bridge App is the private Supervisor runtime for Home Assistant Codex
Bridge. The companion `codex_bridge` Integration is the browser-facing
administrator panel and connects to this App through Supervisor.

## Status

- App version: `0.6.0` (`amd64` only, experimental)
- Integration and external Bridge: `0.5.3`
- Public App image: not available yet
- Future App repository: <https://github.com/Herbertmt978/ha-codex-bridge>

The source, manifest, and image definition are present here. A private immutable
image passed sandbox self-test and authenticated readiness on an amd64 Home
Assistant OS development VM on 14 July 2026. This is not a published App
release or validation of remote access, updates, or App-image rollback.

## Installation model

For a published release, install the matching App image through Home Assistant
Supervisor and the Integration through HACS. Supervisor discovery supplies the
private Integration-to-App connection; an administrator does not enter a Bridge
address, port, or bearer token.

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

The running image never replaces Codex or itself. Do not assume Supervisor can
select an arbitrary earlier App image: App-image rollback is not yet validated.
Until a prior immutable tag and restore procedure are published and tested, use
a cold Home Assistant backup or an existing private external Bridge for
recovery. Keep workspaces until their contents have been reviewed.

See [DOCS.md](DOCS.md), the repository [installation guide](../docs/installation.md),
and [backup and recovery](../docs/backup-restore.md).
