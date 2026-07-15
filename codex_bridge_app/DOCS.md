# Codex Bridge App documentation

## Runtime boundary

The App hosts the private Bridge and Codex runtime. Home Assistant remains the
client-facing boundary: the Integration authorizes Home Assistant users and
uses the Supervisor-managed private connection to the App.

Do not publish the App or Bridge to a browser, LAN, or WAN. Remote users reach
Home Assistant through Nabu Casa, Cloudflare, a VPN, or another correctly
configured HTTPS route. The App does not request ingress, host networking,
Docker access, devices, `/share`, Home Assistant configuration, or broad
Supervisor roles.

## Filesystem and persistence

The only writable host mapping is `addon_config:rw`, mounted at `/config`.
Workspaces live under `/config/workspaces`; they are the only files an operator
should grant to Codex. Private Bridge and ChatGPT device-login state live under
the App-private `/data` volume.

Create cold backups with the App stopped so Home Assistant captures a consistent
state. See [backup and recovery](../docs/backup-restore.md).

## Tool sandbox

The trusted Bridge and Codex parent may contact OpenAI for ChatGPT account login
and prompt handling. Model-controlled tools run through the locked Bubblewrap
sandbox and constrained AppArmor child profile. They are limited to the selected
workspace and have no network access to Home Assistant, Supervisor, other Apps,
the LAN, or the internet.

The Bridge selects a managed Codex permission profile for every new or resumed
chat and verifies the profile provenance and resulting sandbox before starting
a turn. **Observe** receives a read-only workspace. **Edit** and **Full auto**
receive the selected writable workspace; Full auto changes approval handling,
not its filesystem or network boundary. Any profile or sandbox mismatch fails
closed.

At startup, a fail-closed attestation verifies the locked binaries and profiles,
namespace and mount isolation, capabilities, seccomp/no-new-privileges state,
workspace-only writes, protected private state, hidden parent environment, and
network restrictions. If the attestation is missing, stale, malformed, wrongly
owned, or inconsistent with the running release, readiness reports the
non-sensitive fatal state `sandbox_unavailable`. Do not broaden permissions to
work around it.

On target HAOS, Codex `0.144.4`'s official `--no-proc` fallback works: denial of
a fresh `/proc` mount leaves user, PID, and network namespaces, the read-only
filesystem, AppArmor, and seccomp enforced; `/proc` is intentionally empty.
Attestation inspects that state without requiring procfs or broader container
privileges. App `0.6.1`'s fatal readiness cause was instead a
sandbox-self-test contract mismatch: it required `writableRoots` exactly
`[workspace]`, while the real `ha_bridge` `workspaceWrite` response includes
bounded supplemental roots (`.agents`, `.codex`, `.cursor`, `.git`, and
`.vscode`) beneath the workspace. The proc-less probe already used direct
`capget`/`prctl`/`lsm_get_self_attr` calls, without requesting `SYS_ADMIN` or
weakening isolation; App `0.6.2` validates canonical contained supplemental
roots and hardens `lsm_get_self_attr` record parsing.

## Authentication

The Integration starts Codex's ChatGPT device-login flow. From the panel, select
**Sign in with ChatGPT**, then complete the approved ChatGPT device-auth page in
a browser. **Cancel** ends only an unfinished sign-in; **Sign out** removes an
established session. Initial sign-in and re-authentication require access to the
approved ChatGPT page, while normal signed-in panel use remains on the Home
Assistant origin. While device approval is pending, the panel performs a
bounded two-second account check and keeps the one-time code until Codex
authoritatively confirms the session. Uncorrelated completion events are never
allowed to replace a newer login.

Credentials stay in private App state and are not entered in App options, Home
Assistant configuration, or a browser URL. No OpenAI API key is part of this
contract. If a device or credential is suspected compromised, stop the App,
use **Sign out**, and revoke the ChatGPT session through normal account controls.

## Model catalogue

The App asks the installed Codex runtime for its model catalogue and each
model's supported reasoning levels. During a transient discovery failure, the
Bridge may expose a clearly marked last-known-good or fallback catalogue. It
preserves configured selections rather than silently changing a chat to another
model. A confirmed account or plan change expires the signed-out catalogue
immediately, so newly entitled models and reasoning levels are fetched on the
next status request rather than waiting for the normal cache lifetime.

## Updates and recovery

An App update is a new versioned image; Codex and the Bridge are not updated in
a running container. App-image rollback is not yet validated: do not state or
assume that Supervisor can select an arbitrary earlier image. Until a prior
immutable App tag and restore procedure are published and tested, recover with
a cold Home Assistant backup or, where one already exists, a private external
Bridge. Retain workspaces until their contents have been reviewed.

## Release status

The App is experimental and `amd64` only. App `0.6.3` is a signed
immutable image with an SPDX SBOM and build provenance. App `0.6.1` is known-bad
on target HAOS because its sandbox self-test required `writableRoots` exactly
`[workspace]` while the real `ha_bridge` `workspaceWrite` response includes
bounded supplemental roots beneath the workspace. App `0.6.2` validates
canonical contained supplemental roots and hardens `lsm_get_self_attr` record
parsing. The published image passed target-HAOS startup, its production sandbox
self-test and attestation, an authenticated API v1 readiness request,
Supervisor discovery, Integration pairing, and panel loading. A redacted
ChatGPT device-login start/cancel cycle also passed; completing account
authorization still requires the user. Remote access, the first unattended
automatic update, cold restore, and App-image rollback remain acceptance work
for the intended Home Assistant installation.

For responsible vulnerability reporting, see [SECURITY.md](../SECURITY.md).
