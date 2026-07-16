<div align="center">

<img src="brand/logo.png" alt="Codex Bridge — private code, home control" width="720">

# Home Assistant Codex Bridge

Keep browser traffic on Home Assistant while a private HAOS App connects to
Codex/OpenAI from your home network.

[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=ha-codex-bridge&category=integration)
[![Integration release](https://img.shields.io/github/v/release/Herbertmt978/HA_Codex_Bridge?display_name=tag&label=Integration&color=0EA5E9)](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest)
[![CI](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml)
[![App release](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml)
[![App status](https://img.shields.io/badge/App-Experimental-F59E0B?logo=home-assistant&logoColor=white)](codex_bridge_app/README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-0F766E.svg)](LICENSE)

[Installation](docs/installation.md) | [Capabilities](#automations-and-codex-capabilities) | [Updates](#updates-and-recovery) | [Remote access](docs/remote-access.md) | [Backup and recovery](docs/backup-restore.md) | [Security](SECURITY.md) | [Support](SUPPORT.md)

</div>

## What it is

Codex Bridge keeps Home Assistant as the user-facing control plane. An
administrator works in the Home Assistant panel; the private Bridge coordinates
Codex and a deliberately granted workspace.

```text
Browser -> Home Assistant -> Codex Bridge Integration -> private App or external Bridge -> Codex / OpenAI
```

The browser does not connect directly to the Bridge, App, or Codex. Publish
Home Assistant through its normal LAN or HTTPS remote-access route instead.
Nabu Casa, Cloudflare, or another reverse proxy can provide that route; the
App and Bridge remain private to Home Assistant.

## Two components, two installation paths

- **HACS Integration:** the `codex_bridge` custom integration supplies the
  administrator panel and is installed through HACS. The HACS link above opens
  a custom-repository flow; it is not a statement that this project is listed,
  reviewed, endorsed, or supported by HACS or Home Assistant.
- **Supervisor App:** the private runtime intended to run the Bridge and Codex
  alongside Home Assistant. Add this repository to the Home Assistant App
  store to install its published immutable image.

<details>
<summary><b>Current release and validation details</b></summary>

The latest target-Home-Assistant-accepted release (historical for the current
candidate) is experimental, `amd64`-only App `0.7.1`,
Integration `0.7.1`, optional external Bridge `0.6.0`, and bundled Codex
`0.144.4`. The distributed immutable App image has generic digest
`sha256:ec4e5f4ea48ba2333d5689879bc98a58912ae15ac9f90a133d30712452403184`
and its amd64 child digest is
`sha256:cacfb7b4a65a1b0290fe5c7da9dfa33c5ffde78f8ebaa3370fac9366c19681a6`.
Main CI rerun `29483810669` and App publication run `29483810926` succeeded
([release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.1)).
On target HA, App and Integration `0.7.1` were installed and running with
Bridge `0.6.0` and Codex `0.144.4`; ChatGPT Pro remained signed in, GPT-5.6
was visible, the five-hour window rendered `Off`, and existing chats/history
were preserved. Scheduled form drafts survived rerenders; the Skills form draft
survived and create/list/delete passed; and the MCP form draft survived and was
cancelled. A one-time Observe automation was claimed exactly at
`2026-07-16T09:09:30Z`, completed at `09:09:35Z`, then paused and deleted.
The historical `0.7.1` Plugins/marketplaces list returned
`capabilities_unavailable` (HTTP 503); do not claim `0.7.1` plugin or
marketplace list/mutation acceptance. The first unattended App auto-update remains proven,
and this manual update kept the prior-version backup. External blocked-network/
Nabu Casa/Cloudflare routing, cold restore, and arbitrary previous-image rollback
remain unproven.

App/Integration `0.7.2` with Bridge `0.6.1` was subsequently published and
signed, but was not accepted on the target Home Assistant before this candidate
superseded it. Its generic image digest is
`sha256:6d2622bfbf2f1ce50611a4b2b0f72b9f682d0ad6e6619ed84c06d3d74fd462bd`,
with amd64 child digest
`sha256:8e70abea7f98037c805d5163601a0d4a3045e3d54a83f27ee36af64072fe56f0`;
main CI `29491849347` and App publication `29491849502` succeeded
([release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.2)).

The `0.7.1` publication and target-HA acceptance above are historical for this
candidate. App/Integration `0.7.3` with Bridge `0.6.2` is a candidate pending
real Home Assistant acceptance, with bundled Codex `0.144.4`. It enables
provider-gated native live web search by default for Supervisor-connected
prompts and automations, including automatic capability recovery after a
delayed ChatGPT sign-in; shell-command networking remains disabled. Signed-in
ChatGPT-account image generation is available only when Codex advertises both
`imageGeneration` and `namespaceTools`; it uses no API key and keeps bounded
PNG, JPEG, or WebP results as private artifacts. The compact panel remains the
primary UI. The candidate also retains the bounded plugin catalogue fixes and
the updater's pinned `jsonschema` dependency installation fix; neither is
live-acceptance evidence.

App `0.7.0` uses private-IP Supervisor discovery. It retains bounded recovery
for delayed ChatGPT device sign-in, expires the signed-out catalogue when
account entitlements change, and discovers every model and reasoning level
from Codex rather than hardcoding a release list. It also separates weekly-only
usage from the disabled five-hour window and keeps a newly created chat usable
while secondary snapshots retry.
The `0.7.0` release extends the Codex-style surface with a clean left navigation
tree, title-first chat rows, one action menu, correct archive collapse/search,
and a corrected search icon. It adds Scheduled, Skills, Plugins, MCP,
Instructions, About, Security, and system-information screens plus live action,
streaming, and step/file metrics in the transcript. Approvals remain beside the
active transcript, every decision stays reachable in the normal mobile scroll
flow, and limits/model controls fold behind a compact mobile disclosure. Its
catalogue recovery remains runtime-derived: live discovery, verified
last-known-good data, the bundled Codex catalogue, then the static fallback.

The App publishes discovery with the current Supervisor `app_config` map
permission and its assigned private HA-network IP. A restart includes a bounded
non-secret publication marker, which makes Supervisor refresh an otherwise
unchanged record without changing its stable identity. If Core starts the
Integration before the App is reachable, the flow shows a retryable connection
state and does not save an unverified endpoint.

The external Bridge remains an optional, private compatibility path for people
who already operate one. Fresh Home Assistant OS installations should use the
published App `0.7.1`. App `0.6.1` must not be used. Keep an existing external
Bridge as a recovery path until the remaining blocked-network route, cold
restore, and previous-image rollback are evidenced on the intended installation.

</details>

> [!IMPORTANT]
> The App is experimental and currently supports `amd64` Home Assistant OS.
> Installing the HACS Integration alone provides the panel but does not run
> Codex; install the Supervisor App as well, or explicitly configure the
> advanced private external Bridge.

| Before you install | Boundary |
| --- | --- |
| Network | Publish Home Assistant only; the App and Bridge remain private. |
| Storage | The App writes its private state plus workspaces deliberately placed under `/config/workspaces`. |
| Account | ChatGPT device authentication stays in App-private storage and does not use an OpenAI API key. |
| Reversal | Stop/remove the App and Integration; review workspaces and sign out before deleting their data. |

## Install and first run

1. Install the **Codex Bridge** Integration through HACS, then restart Home
   Assistant so its Supervisor discovery handler is active.
2. In **Settings -> Apps -> App store -> Repositories**, add
   <https://github.com/Herbertmt978/HA_Codex_Bridge>. Wait until the store
   offers App `0.7.1` or newer, then install and start **Codex Bridge**. Do not
   install App `0.6.1`; it fails closed during target-HAOS readiness.
3. In **Settings -> Devices & services**, confirm the discovered **Codex
   Bridge** Integration. Supervisor advertises the App's private HA-network IP
   and port automatically; there is no host, port, or bearer token to copy. If
   the App has just started or restarted, discovery can take a few seconds to
   arrive. Retry after the App reports ready; the Integration keeps a valid
   discovery form visible while that private endpoint is temporarily
   unreachable and does not save an unverified connection.
4. Open the panel as a Home Assistant administrator. Select **Sign in with
   ChatGPT**, then use a browser to complete the approved ChatGPT device-auth
   page. **Cancel** only cancels an in-progress sign-in; **Sign out** removes an
   established Codex session. After approval, the panel checks the authoritative
   account state every two seconds until Codex reports the session ready.
5. Create a Project and grant a small workspace beneath `/config/workspaces` in
   App mode. Review changes before expanding that boundary.

The Home Assistant and ChatGPT sessions are separate. After a ChatGPT session
is established, normal panel use can remain on the Home Assistant origin.
Initial sign-in and re-authentication still require browser access to the
approved ChatGPT device-auth page. This account flow does not use an OpenAI API
key.

## Automations and Codex capabilities

The panel also exposes administrator-only capabilities that remain bounded by
the selected App workspace:

- **Automations / scheduled tasks:** create a prompt targeting a project or
  existing thread, choose `observe`, `edit`, or `full-auto`, and schedule a
  one-time, interval, or RFC 5545 recurrence. Home Assistant owns the wall
  clock; the Bridge persists definitions, uses revision checks and idempotent
  claims, records overlap/capacity/misfire skips, and keeps run history bounded.
  Pause an automation before deleting it.
- **Native web search and images:** on a Supervisor connection, native web
  search is selected by default and activates only after the App advertises
  it, including after a delayed sign-in. It applies to prompts and manual
  automation runs and can be disabled in Integration options. This does not
  enable shell-command networking. Image generation
  needs a signed-in ChatGPT account plus both runtime `imageGeneration` and
  `namespaceTools` capabilities. It does not use an OpenAI API key; generated
  PNG, JPEG, and WebP artifacts remain private and size-bounded.
- **Skills:** list, enable/disable, create, and delete workspace skills under
  the selected workspace's `.agents/skills/` tree. Paths outside that workspace
  are rejected.
- **Instructions (`AGENTS.md`):** edit a global Codex `AGENTS.md` or the
  selected project's workspace-root `AGENTS.md`. Writes are atomic and prior
  versions are retained in private, bounded rollback snapshots.
- **Plugins and marketplaces:** inspect runtime-reported marketplaces and
  plugins, install/uninstall plugins, and add/remove/upgrade a marketplace.
  Marketplace sources must use HTTPS hostnames; literal/known non-public
  addresses, credentials, and arbitrary config payloads are rejected. In the
  historical `0.7.1` live-acceptance run, the list call returned
  `capabilities_unavailable` (HTTP 503); no `0.7.1` plugin or
  marketplace list/mutation acceptance was claimed. The `0.7.3` candidate is
  pending real Home Assistant acceptance.
- **MCP servers:** MCP is disabled by default. To use it, explicitly enable
  **Enable MCP** in the Codex Bridge App configuration, save, and restart the
  App. Configure outbound streamable-HTTP servers only with an HTTPS hostname.
  Literal IPs, local/internal hostnames, and known non-public DNS answers are
  rejected; bearer-token configuration is not exposed. DNS checks are best
  effort and do not form a connection-time IP allowlist, so enable MCP only for
  providers you trust. OAuth is explicit: start login from the panel and treat
  the returned authorization URL as one-shot sensitive data. MCP elicitation
  requests are declined until a separately reviewed UX exists. Turning MCP off
  suppresses and removes its saved server table without changing skills,
  plugins, marketplaces, or instructions. Adding a server does not publish the
  App or Bridge.

These surfaces are runtime-derived and can be unavailable while Codex is busy,
unauthenticated, or recovering. Failed mutations return bounded errors without
leaking provider details or secrets.

## Updates and recovery

The Integration and App update separately:

1. In HACS, update or redownload the latest **Codex Bridge** Integration, then
   restart Home Assistant. Reload any panel tab that was already open before
   the restart.
2. Read the matching [release notes](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest)
   and confirm the runtime strip in the panel shows the expected Integration,
   App, Bridge, and Codex versions.
3. If Home Assistant offers an App update, make a cold backup and apply it from
   **Settings -> Apps -> Codex Bridge**. Auto update can do this after its toggle
   is enabled; the first unattended update is proven. The manual `0.7.1` update
   retained the prior-version backup, while recovery and rollback remain
   unproven.

App images are immutable: a running container does not replace Codex or itself.
Upstream Codex updates first arrive as a verified, reviewable repository PR;
unattended merge remains disabled until a real update/recovery canary passes.
The Supervisor App does **not** currently provide a validated way to select an
arbitrary prior image, so make a cold Home Assistant backup before an App
change. Keep a private external Bridge where one already exists until cold
restore has been exercised; see [backup and recovery](docs/backup-restore.md).

## Security boundary

| Boundary | Responsibility |
| --- | --- |
| Remote access | Publish Home Assistant, not the App or Bridge. |
| Home Assistant | The panel is administrator-only; an administrator can ask Codex to act in granted workspaces. |
| App / Bridge | Private runtime state and the Codex session stay off the browser-facing path. |
| Workspace | Codex can inspect and change only the files you grant; start small and review changes. |
| Credentials | Do not share device codes, Bridge tokens, session material, or workspace secrets. |

The App fails closed when its sandbox attestation cannot be verified. Do not
weaken the sandbox to continue; inspect the App log and use a supported build.
See [App documentation](codex_bridge_app/DOCS.md) and [SECURITY.md](SECURITY.md).

## Uninstall

Stop the App or external Bridge, remove the Integration, and review
`/config/workspaces` before deleting project data. Remove Codex access with
**Sign out** and revoke the ChatGPT session through normal account controls
before repurposing a device. The [external-Bridge migration guide](docs/migration-from-windows.md)
has safe cutover and recovery guidance.

## Development and contribution

See [development](docs/development.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
The source is available under the [MIT License](LICENSE); third-party
attribution is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
