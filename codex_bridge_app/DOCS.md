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
  `0.7.5` acceptance did not exercise plugins or marketplaces, so no current
  plugin or marketplace acceptance is claimed.
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

The App deliberately does not mount the desktop machine's local skill bundle.
Image generation here is the provider-native tool, so a model-authored remark
about an unavailable local image skill guide is narration rather than a
capability result. The advertised native-tool status and the presence of a
validated generated artifact are authoritative. The panel does not instruct
users to invoke a non-existent App-local `$imagegen` skill.

## Run stages and private previews

The panel presents plan stages, allowlisted tool activity, file/line totals,
and aggregate subagent status. It never receives agent IDs, prompts, private
paths, raw commands, model names, messages, or arbitrary provider status text
for this view.

Selected text, raster-image, and PDF artifacts are fetched through Home
Assistant's administrator-authenticated artifact route. PDF preview requires a
known and observed size of no more than 8 MB plus a valid leading PDF signature;
the panel then renders the bytes on canvas with the bundled local PDF.js
renderer. PDF.js scripting, eval, and XFA support are disabled. No iframe or
native browser PDF embed is used. HTML, SVG, XML, invalid PDFs, unknown-size
files, and oversized files keep the safe open/download fallback. This does not
expose a browser, CDP, App, or Bridge endpoint. Browser automation needs the
separate isolation recorded in
[ADR 0006](../docs/aegis/adr/0006-preview-and-browser-boundary.md).

The panel options menu exposes **Focus mode** when the browser permits the
standards fullscreen API. It can be entered only from that user gesture,
leaves the existing Home Assistant transport and authorization path unchanged,
uses the Codex-style tinted navigation and floating context treatment, and uses
native Escape plus accessible focus restoration on exit.

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

Signed App/Integration/panel `0.8.7` with Bridge `0.7.3` and Codex `0.144.5`
is installed on the target Home Assistant. ChatGPT Pro, projects, and history
were retained; generated-image preview and transcript-only scrolling passed.
The browser persisted a complete 3,276,457-byte PNG with the expected signature
and SHA-256
`F211434D64D69C2246A600445B9B69DDAB82D6D676D32FD0D215D178DB7D31FF`.
Chrome's automation event did not report the blob download, so the persisted
file is the acceptance evidence. Candidate `0.8.8` makes the authenticated
**Prepare download** -> **Preparing...** -> **Save file** handoff visible on
generic Files rows as well as generated-image and PDF controls. Unsaved prepared
bytes expire after 60 seconds and are cleared on panel disconnect or context
change; no Home Assistant credential enters a URL. Candidate publication and
live generic-artifact acceptance remain separate gates.

The historical fully target-HA-accepted matrix, App, Integration, and panel
`0.7.5` with Bridge `0.6.3` and Codex `0.144.5`,
were installed and running on target Home Assistant `192.168.50.20` on
2026-07-16. ChatGPT Pro remained connected. A fresh direct chat defaulted to
`gpt-5.6-sol` with `low` thinking; the runtime catalogue exposed Sol, Terra,
and Luna plus Low, Medium, High, XHigh, Max, and Ultra where advertised. The
compact composer showed five-hour `Off` and Week `60%`. The natural prompt
`what is the weather in Malta like today` recorded `Searching the web` and
returned current live conditions; shell networking remained disabled.

App publication run `29511116947` verified the signed `0.7.5` image, SBOM, and
provenance. Its immutable digest is
`sha256:6214ab4fa471f3356460c1c392e582981cd1b80ad2fc2173ddb925aaba6336d0`,
with attestation `35670902`. This acceptance does not establish image-
generation, plugin/marketplace, MCP, external-routing, cold-restore, or
arbitrary prior-image rollback acceptance.

Historical App/Integration/panel `0.8.4` with Bridge `0.7.3` and Codex
`0.144.5` was the previous signed release. It was published from exact main commit
`ccc698e96a2142d46ba96fb1419857461efe81ca`; signed App publication
`29571157282` passed and the paired
[0.8.4 Integration release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.8.4)
points to that commit. This publication evidence does not claim that `0.8.4`
completed the target-HA matrix.

The latest bounded target smoke remains `0.8.3`. On target Home Assistant
`192.168.50.20`, App and Integration `0.8.3` reported
Bridge `0.7.2` and Codex `0.144.5`; ChatGPT Pro, projects, and chat history
were retained. The old `0.8.0 PDF acceptance` thread recovered from the false
**Working / Preparing a response / Stop / steer** state to a truthful ready/
Run completed state. A fresh GPT-5.6-Sol prompt completed. Sol, Terra, and Luna
and advertised Max/Ultra reasoning levels were visible; five-hour usage showed
`Off`; and the Malta prompt exposed `Searching the web` and `Using web search`
before returning live conditions. No false global **Connection issue** remained.

The release restores operational aggregate workspace-scan failures to the
typed, retryable local **Files** contract and adds the measured Codex-style
wide-screen alignment pass. Artifact-index and preview failures remain local to
**Files** and cannot overwrite a valid reply or healthy chat state. The current
PDF artifact scan still returns the typed `409` local Files conflict, so
PDF/archive/restore acceptance is not claimed. External Nabu Casa/Cloudflare
routing, cold restore, arbitrary image rollback, and the secure App-owned
browser worker remain unproven. The paired App/Integration release workflow
completed its first live automatic exercise for `0.8.4`.

The signed, live-accepted `0.6.5` matrix remains historical evidence only. App `0.6.1` is known-bad
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
