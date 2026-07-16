# Codex Bridge App documentation

## Runtime boundary

The App hosts the private Bridge and Codex runtime. Home Assistant remains the
client-facing boundary: the Integration authorizes Home Assistant users and
uses the Supervisor-managed private connection to the App.

Do not publish the App or Bridge to a browser, LAN, or WAN. Nabu Casa,
Cloudflare, a VPN, or another correctly configured HTTPS reverse proxy must
terminate at Home Assistant; it must not proxy a browser to the App or Bridge.
The App does not request ingress, host networking,
Docker access, devices, `/share`, Home Assistant configuration, or broad
Supervisor roles.

## Filesystem and persistence

The only writable host mapping is `app_config:rw`, mounted at `/config`.
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

## Automations and capability configuration

The administrator-only panel exposes these App-backed surfaces:

- Automations persist a prompt, target (project or existing thread), mode, and
  schedule. Home Assistant's scheduler asks for the next due UTC occurrence;
  the Bridge performs revision-checked, idempotent claims and records run
  history. Once, interval, and RFC 5545 recurrence schedules are validated;
  overlap, capacity, pause, and misfire skips are explicit outcomes.
- Skills are listed or managed in the selected workspace and created under
  `.agents/skills/`. Global `AGENTS.md` is kept in the private Codex home;
  project `AGENTS.md` is kept at that project's workspace root. Writes are
  atomic, bounded, and retain private rollback snapshots.
- Plugins and marketplaces are projected from Codex's native configuration.
  Sources and names are validated, credentials are rejected, and no arbitrary
  file path or JSON-RPC payload is passed through this API. The published/live-
  accepted `0.7.1` run returned `capabilities_unavailable` (HTTP 503); no
  `0.7.1` plugin or marketplace list/mutation acceptance was claimed. The
  `0.7.5` candidate is pending real Home Assistant acceptance; it is not live
  publication evidence.
- MCP is disabled by default. To opt in, set **Enable MCP** in the App
  configuration, save, and restart. Disabled startup suppresses MCP before
  Codex reads saved configuration and removes the native MCP server table;
  cleanup failure keeps readiness unavailable. Other Codex extensions are
  preserved.
- Enabled MCP servers are outbound streamable HTTP. The endpoint must use a
  trusted HTTPS hostname; literal IPs, local/internal names, and known
  non-public DNS answers fail validation. DNS checks are best effort and are
  not a connection-time IP allowlist, so the provider remains an administrator
  trust decision. Bearer-token configuration is unsupported. OAuth login is
  explicit and the one-shot authorization URL is returned with `no-store`;
  the Bridge never logs or stores it. MCP elicitation is handled by a
  decline-only callback.

These operations are not a second network boundary: the browser remains on
Home Assistant, and a configured MCP server does not make the App or Bridge
public.

## Model catalogue

The App asks the installed Codex runtime for its model catalogue and each
model's supported reasoning levels. If live app-server discovery fails, it
dynamically reads the installed Codex bundled catalogue. Stale results retry
after 15 seconds; a verified last-known-good catalogue is preferred over the
bundled recovery, and the static fallback is used only if neither is available.
Every recovery state is marked stale, and configured selections are preserved
rather than silently changing a chat to another model. GPT-5.6 and
model-specific Max/Ultra levels are displayable whenever Codex advertises them;
the Bridge does not keep a hardcoded model-name list. A confirmed account or
plan change expires the signed-out catalogue immediately, so newly entitled
models and reasoning levels are fetched on the next status request rather than
waiting for the normal cache lifetime.

## Provider-gated native tools

For a Supervisor-connected Integration, native web search defaults to `live`
for prompts and manual automation runs only when the App's installed Codex
runtime advertises web-search support. The preference survives a signed-out
startup and activates automatically when device login completes. The
Integration can disable it, and an
external legacy Bridge does not inherit the default. This is provider-side web
search, not shell networking: model-controlled shell networking remains
disabled by the tool sandbox.

Image generation requires a signed-in ChatGPT account and both provider
capabilities named `imageGeneration` and `namespaceTools`. It never requires or
uses an OpenAI API key. The Bridge validates and stores only bounded PNG, JPEG,
and WebP output as private artifacts, then makes them available through Home
Assistant's authenticated path. Capability absence or a failed probe leaves
these tools unavailable.

## Updates and recovery

On each App start, the ready Bridge publishes its Supervisor-assigned private
App IP with a fresh non-secret publication marker. This ensures Supervisor
re-pushes discovery after an App restart while retaining the Supervisor-issued
discovery UUID. Discovery failures are logged by safe category only; tokens and
endpoint credentials are never logged.

An App update is a new versioned image; Codex and the Bridge are not updated in
a running container. App-image rollback is not yet validated: do not state or
assume that Supervisor can select an arbitrary earlier image. Until a prior
immutable App tag and restore procedure are published and tested, recover with
a cold Home Assistant backup or, where one already exists, a private external
Bridge. Retain workspaces until their contents have been reviewed.

## Release status

Candidate App/Integration `0.7.5` with Bridge `0.6.3` and Codex `0.144.5` is
pending real Home Assistant acceptance. It retains capability-gated Live web
search and signed-in image generation with private bounded PNG/JPEG/WebP
artifacts, guides time-sensitive prompts toward the native search tool, and
uses a compact Codex-style composer; shell networking remains disabled and no
OpenAI API key is used. These are candidate facts only.

The latest published signed App is `0.7.4`, with Bridge `0.6.2` and Codex
`0.144.5`. Publication run `29507100716` verified its signature, SBOM, and
provenance. Its immutable digest is
`sha256:de03e6e57cd6fcaa0dd2a479b743ede2c4d3773b228fc2af3b35b0eb86c1b152`.
The latest target-HA-accepted coordinated release is Integration/App `0.7.3`,
Bridge `0.6.2`, and Codex `0.144.4` (experimental, `amd64` only). Acceptance is
bounded to the recorded checks.
Target-Home-Assistant acceptance is bounded. The signed, live-accepted `0.6.5`
matrix remains historical evidence only. App `0.6.1` is known-bad
on target HAOS because its sandbox self-test required `writableRoots` exactly
`[workspace]` while the real `ha_bridge` `workspaceWrite` response includes
bounded supplemental roots beneath the workspace. App `0.6.2` validates
canonical contained supplemental roots and hardens `lsm_get_self_attr` record
parsing. The historical `0.6.5` image passed target-HAOS startup, its production sandbox
self-test and attestation, an authenticated API v1 readiness request,
Supervisor discovery, Integration pairing, and panel loading. A redacted
ChatGPT device-login start/cancel cycle also passed. The target `0.7.1` run
retained ChatGPT Pro, showed dynamic GPT-5.6, rendered the five-hour window
`Off`, and preserved existing chats/history. Scheduled and Skills form drafts
survived rerenders; Skills create/list/delete passed; the MCP form draft
survived and was cancelled. A one-time Observe automation was claimed exactly
at `2026-07-16T09:09:30Z`, completed at `09:09:35Z`, then paused and deleted.
The historical `0.7.1` live Plugins/marketplaces list returned
`capabilities_unavailable` (HTTP 503); no `0.7.1` plugin or marketplace
list/mutation acceptance was claimed. The first unattended App update is
proven, and this manual update kept the prior-version backup. External
blocked-network/Nabu Casa/Cloudflare routing, cold restore, and arbitrary
previous-image rollback remain unproven.

For responsible vulnerability reporting, see [SECURITY.md](../SECURITY.md).
