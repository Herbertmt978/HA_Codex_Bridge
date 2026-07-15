# Codex Bridge App

The Codex Bridge App is the private Supervisor runtime for Home Assistant Codex
Bridge. The companion `codex_bridge` Integration is the browser-facing
administrator panel and connects to this App through Supervisor.

## Status

- Release being shipped: App `0.6.6` (`amd64` only, experimental)
- Integration: `0.6.6`
- External Bridge: `0.5.5`
- Bundled Codex: `0.144.4`
- App repository: <https://github.com/Herbertmt978/HA_Codex_Bridge>

Publication, signing, and target-Home-Assistant acceptance for `0.6.6` remain
pending. The signed, live-accepted `0.6.5` matrix remains historical evidence;
do not reuse its image digest or acceptance claims for this release. On target
HAOS, pinned Codex `0.144.4`'s official `--no-proc`
fallback works: denial of a fresh `/proc` mount leaves user, PID, and network
namespaces, the read-only filesystem, AppArmor, and seccomp enforced; `/proc` is
intentionally empty. App `0.6.1`'s fatal readiness cause was a sandbox-self-test
contract mismatch: it required `writableRoots` exactly `[workspace]`, while the
real `ha_bridge` `workspaceWrite` response includes bounded supplemental roots
(`.agents`, `.codex`, `.cursor`, `.git`, and `.vscode`) beneath the workspace.
The proc-less probe already used direct `capget`/`prctl`/`lsm_get_self_attr`
calls, without requesting `SYS_ADMIN` or weakening isolation. App `0.6.2`
validates canonical contained supplemental roots and hardens
`lsm_get_self_attr` record parsing. The historical `0.6.5` image passed target-HAOS
startup, the production sandbox self-test and attestation, an authenticated API
v1 readiness request, Supervisor discovery, Integration pairing, and panel
loading. Its live-acceptance evidence does not accept `0.6.6`. External
blocked-network/Nabu Casa/Cloudflare routing, cold restore, the first future
unattended App update, and previous-image rollback remain unproven.

The `0.6.6` release advertises the Supervisor-assigned private App IP and includes a
fresh non-secret publication marker on each start, so Home Assistant can
recover discovery without changing the stable Supervisor identity. It retains
bounded device-authorization recovery, immediate model-entitlement refresh,
duration-based usage windows, and successful new chats while secondary
snapshots retry. When live app-server model discovery fails, the release reads
the installed Codex bundled catalogue dynamically. Stale data retries after 15
seconds; a verified last-known-good catalogue wins over bundled recovery, and
the static fallback is last. Model and reasoning choices remain discovered from
Codex, so GPT-5.6 and model-specific `max`/`ultra` levels appear only when the
runtime advertises them.

The companion panel uses a clean Codex-style left navigation tree, title-first
chat rows, one action menu, correct archive collapse/search, and a corrected
search icon. Approvals follow the active transcript, decision controls remain
reachable in the natural mobile scroll flow, and limits/model controls fold
behind a compact mobile disclosure. Mobile targets are at least 44px; typed
transient artifact reservations still preserve the prior artifact view without
a false connection error.

## Installation model

Add this repository to the Home Assistant App store, install the App, then
install the Integration through HACS. Supervisor discovery supplies the private
Integration-to-App connection; an administrator does not enter a Bridge address,
port, or bearer token.

The App exposes no browser-facing port or ingress route. Reach the panel through
Home Assistant. HACS and Home Assistant references describe compatible
installation surfaces only; they do not imply endorsement by those projects.

## Storage and authentication

The App's writable host mapping is its dedicated `app_config` directory at
`/config`. User workspaces live below `/config/workspaces`. Private Bridge state
and ChatGPT device-login state live in the App-private `/data` volume.

From the Home Assistant panel, select **Sign in with ChatGPT** and complete the
approved ChatGPT device-auth page in a browser. **Cancel** stops an unfinished
sign-in; **Sign out** removes the established session. Once signed in, normal
panel use stays on Home Assistant, but re-authentication again needs access to
the approved ChatGPT page. This flow does not use an OpenAI API key.

## Updates and recovery

Update or redownload the Integration in HACS first, restart Home Assistant, and
reload any panel tab that predates the restart. Check the
[release notes](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest)
and the panel runtime strip before applying a separately offered App update.

The running image never replaces Codex or itself. Home Assistant can install a
newly released image and can apply it automatically after the App auto-update
toggle is enabled. Do not assume Supervisor can select an arbitrary earlier App
image: App-image rollback is not yet validated. Use a cold Home Assistant backup
or an existing private external Bridge for recovery, and keep workspaces until
their contents have been reviewed.

See [DOCS.md](DOCS.md), the repository [installation guide](../docs/installation.md),
and [backup and recovery](../docs/backup-restore.md).
