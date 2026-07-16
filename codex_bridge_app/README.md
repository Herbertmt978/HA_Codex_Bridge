# Codex Bridge App

The Codex Bridge App is the private Supervisor runtime for Home Assistant Codex
Bridge. The companion `codex_bridge` Integration is the browser-facing
administrator panel and connects to this App through Supervisor.

## Status

- Published App: `0.8.1` (`amd64` only, experimental)
- Published Integration: `0.8.1`
- Latest target-HA-accepted App/Integration: `0.7.5`
- Candidate App/Integration: `0.8.2`
- Candidate Bridge: `0.7.2`
- Bundled Codex: `0.144.5`
- App repository: <https://github.com/Herbertmt978/HA_Codex_Bridge>

Candidate App/Integration `0.8.2` with Bridge `0.7.2` is pending signed
publication and real Home Assistant validation; coordinated `0.7.5` remains
the fully accepted baseline. Signed App `0.8.1` carries the verified Codex
`0.144.5` runtime and the bounded live results below. The candidate restores
aggregate scan failures to the typed, retryable local **Files** contract and
keeps secondary artifact failures out of global connection state. It also
calibrates the wide panel to the Codex desktop rail, conversation, composer,
and full-height context geometry, with an explicit standards-based Focus mode
for a clean three-pane canvas inside Home Assistant.

The 0.8.1 live exercise proved installation/pairing, ChatGPT Pro/history,
version reporting, GPT-5.6 models, Max/Ultra, five-hour `Off`, native
web-search source/stage history, and subagent stage history. PDF creation
succeeded, but indexing/archive encountered stale sandbox-test debris in the
aggregate workspace root; `0.8.1` misreported that operational scan failure as
HTTP 400 unsafe selected-workspace content. PDF acceptance remains
failed/pending. Secure App-owned browser-worker follow-up is issue #43;
interactive Chromium remains deferred by ADR 0006.

The published `0.8.1` image has immutable digest
`sha256:2df98ca0452262a8336b82ec4842ba681c49b44c22a28983a7a10b3d9692e8a2`.
Publication, signature, SBOM, and provenance verification passed in
[run 29527193037](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/runs/29527193037).

The published `0.7.4` image has immutable digest
`sha256:de03e6e57cd6fcaa0dd2a479b743ede2c4d3773b228fc2af3b35b0eb86c1b152`.
Publication, signature, SBOM, and provenance verification passed in
[run 29507100716](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/runs/29507100716).

The published `0.7.2` image has generic digest
`sha256:6d2622bfbf2f1ce50611a4b2b0f72b9f682d0ad6e6619ed84c06d3d74fd462bd`
and amd64 child digest
`sha256:8e70abea7f98037c805d5163601a0d4a3045e3d54a83f27ee36af64072fe56f0`.
Main CI `29491849347` and App publication `29491849502` succeeded; see the
[`0.7.2` release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.2).

The published `0.7.1` image has generic digest
`sha256:ec4e5f4ea48ba2333d5689879bc98a58912ae15ac9f90a133d30712452403184`
and amd64 child digest
`sha256:cacfb7b4a65a1b0290fe5c7da9dfa33c5ffde78f8ebaa3370fac9366c19681a6`.
Main CI rerun `29483810669` and App publication `29483810926` succeeded; see
the [release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.1).
Target-Home-Assistant acceptance is bounded. On target
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
loading. The target `0.7.1` run installed and ran App and Integration with
Bridge `0.6.0` and Codex `0.144.4`; it retained ChatGPT Pro, showed dynamic
GPT-5.6, rendered the five-hour window `Off`, and preserved existing
chats/history. Scheduled form drafts survived rerenders; the Skills form draft
survived and create/list/delete passed; the MCP form draft survived and was
cancelled. A one-time Observe automation was claimed exactly at
`2026-07-16T09:09:30Z`, completed at `09:09:35Z`, then paused and deleted. The
historical `0.7.1` live Plugins/marketplaces list returned
`capabilities_unavailable` (HTTP 503); no `0.7.1` plugin or marketplace
list/mutation acceptance was claimed. The first unattended App auto-update remains proven, and this
manual update kept the prior-version backup. External blocked-network/Nabu
Casa/Cloudflare routing, cold restore, and arbitrary previous-image rollback
remain unproven.

The `0.7.1` release advertises the Supervisor-assigned private App IP and includes a
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
transient artifact reservations and other secondary Files failures preserve the
prior artifact view without a false connection error. On supported browsers,
open the panel options menu and choose **Focus mode** for the Codex-style
three-pane canvas; native Escape exits and returns keyboard focus to the menu.
Healthy component versions live under **Context -> System** so the transcript
stays quiet; runtime warnings still surface beside the conversation.

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

## Automations, instructions, and extensions

The administrator panel can manage durable automations, workspace skills,
global/project `AGENTS.md`, plugins, marketplaces, and MCP servers. The App
does not run a hidden wall-clock worker: Home Assistant schedules the next UTC
occurrence and the Bridge accepts an idempotent claim. One-time, interval, and
RFC 5545 recurrence schedules are supported; overlap, capacity, pause, and
misfire outcomes are recorded as skipped runs.

Skills are created below the selected workspace's `.agents/skills/` directory.
Global instructions live in the private Codex home; project instructions live
at the workspace root. Instruction writes are atomic and retain bounded private
snapshots. Plugin and marketplace operations use Codex's runtime configuration
and never accept arbitrary JSON or paths outside the workspace. The historical
`0.7.1` live list returned `capabilities_unavailable` (HTTP 503). Release
`0.7.2` was published without target acceptance, and `0.7.3` functionality is
not live-acceptance evidence.

For a Supervisor connection whose App advertises native web search, the
Integration defaults prompts and manual automation runs to **Live**; an
administrator can disable it in Integration options. A device login completed
after Integration setup re-negotiates the capability automatically; it does
not require an Integration reload. This does not relax the
model-controlled shell network boundary. Image generation remains provider-
gated as described above and never exposes generated artifacts outside the
private App/Bridge and Home Assistant path.

MCP is disabled by default. Enable **Enable MCP** in the App configuration,
save, and restart the App before adding servers. When it is off, Codex starts
with MCP suppressed and the Bridge removes the saved native MCP server table
without rewriting plugins, skills, marketplaces, or instructions. A cleanup
failure keeps readiness unavailable.

MCP configuration is outbound only. The server URL must use a trusted HTTPS
hostname (not a literal IP, localhost, internal hostname, or known non-public
DNS answer). DNS checks are best effort, are not a connection-time IP allowlist,
and cannot guarantee that an answer will not change after validation. This
surface does not configure bearer tokens. OAuth login is an explicit
administrator action; its authorization URL is returned once and is not
retained by the Bridge. MCP elicitation requests are declined until a consent
UX is reviewed. None of these settings publishes the App or Bridge to a
browser.

## Updates and recovery

Update or redownload the Integration in HACS first, restart Home Assistant, and
reload any panel tab that predates the restart. Check the
[release notes](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest)
and **Context -> System -> Versions** before applying a separately offered App
update. The conversation runtime strip appears only when a component needs
attention.

The running image never replaces Codex or itself. Home Assistant can install a
newly released image and can apply it automatically after the App auto-update
toggle is enabled. Do not assume Supervisor can select an arbitrary earlier App
image: App-image rollback is not yet validated. Use a cold Home Assistant backup
or an existing private external Bridge for recovery, and keep workspaces until
their contents have been reviewed.

See [DOCS.md](DOCS.md), the repository [installation guide](../docs/installation.md),
and [backup and recovery](../docs/backup-restore.md).
