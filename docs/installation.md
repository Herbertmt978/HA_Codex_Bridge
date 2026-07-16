# Installation

## Status before you start

This guide covers the current candidate: experimental, `amd64`-only App and
Integration `0.7.5`, Bridge `0.6.3`, and Codex `0.144.5`. It is pending final
Home Assistant acceptance. Signed App `0.7.4` already publishes the verified
Codex `0.144.5` runtime; `0.7.5` coordinates the Integration and compact panel.
Provider-gated native web search defaults to Live for Supervisor prompts and
automations, re-negotiates automatically after ChatGPT sign-in, and guides
time-sensitive prompts toward the native tool; shell-command networking
remains disabled. Signed-in image generation requires
both `imageGeneration` and `namespaceTools`, uses no API key, and retains only
private bounded PNG/JPEG/WebP artifacts. The compact composer is a candidate
presentation change and does not expand authority. The
published/signed `0.7.0` baseline has generic image digest
`sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`
with amd64 child
`sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`;
main CI `29471288344` and publication `29471288457` succeeded, with signature,
SBOM, and provenance on the [release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0).
Target-Home-Assistant acceptance is bounded. The signed, live-accepted `0.6.5`
matrix is historical evidence only. On target
HAOS, Codex `0.144.4`'s official
`--no-proc` fallback works: denial of a fresh `/proc` mount leaves the sandbox
namespaces, read-only filesystem, AppArmor, and seccomp intact; `/proc` is
intentionally empty. App `0.6.1`'s fatal readiness cause was a sandbox-self-test
contract mismatch: it required `writableRoots` exactly `[workspace]`, while the
real `ha_bridge` `workspaceWrite` response includes bounded supplemental roots
(`.agents`, `.codex`, `.cursor`, `.git`, and `.vscode`) beneath the workspace.
The proc-less probe already used direct `capget`/`prctl`/`lsm_get_self_attr`
calls, without requesting `SYS_ADMIN` or weakening isolation; App `0.7.0`
retains canonical contained supplemental-root validation and hardened
`lsm_get_self_attr` record parsing. The historical `0.6.5` image passed
target-HAOS startup, its production sandbox self-test and attestation, an
authenticated API v1 readiness request, Supervisor discovery, Integration
pairing, and panel loading. App `0.7.0` uses private-IP Supervisor discovery and
retains bounded recovery after device approval, immediate entitlement-aware
model discovery, duration-aware usage windows, and resilient new-chat
hydration. The `0.7.0` panel has a clean left navigation tree, title-first chat
rows, one action menu, correct archive collapse/search and search icon, 44px
mobile targets, transcript-adjacent decisions, and collapsed mobile
limits/model controls. Its catalogue
recovery remains dynamic; no model or reasoning list is hardcoded. The target
run observed App and Integration `0.7.0`, Bridge `0.6.0`, Codex `0.144.4`,
retained ChatGPT Pro, dynamic GPT-5.6, five-hour `Off`, preserved chat/history,
and App auto-update plus MCP opt-in persistence after restart. Management forms
lose unsaved values during a background rerender; the `0.7.1` candidate
contains the fix. Do not claim automation, skills, plugins/marketplaces,
MCP-server, or `AGENTS.md` mutation acceptance until retested. The `0.6.5`
live acceptance is historical. The first unattended App update is proven;
external blocked-network/Nabu Casa/Cloudflare routing, cold restore, and
previous-image rollback remain unproven.

Codex Bridge has two separate surfaces:

1. The **Integration** is installed in Home Assistant and owns the
   administrator panel.
2. The private **App** runs the Bridge and Codex through Supervisor.

The Integration can be installed as a HACS custom repository. This does not
imply a HACS or Home Assistant listing, review, endorsement, or support. The
App repository is <https://github.com/Herbertmt978/HA_Codex_Bridge>.

## Prerequisites

- Home Assistant Core `2026.7.2` or newer, running on Home Assistant OS for
  `amd64`, with administrator access. Home Assistant Container does not provide
  Apps and cannot use this Supervisor App.
- A ChatGPT account that can use Codex. Device login does not use an OpenAI API
  key.
- A small, non-sensitive project directory you are comfortable letting Codex
  read and change.
- A recovery plan: make a cold backup and, if you already operate one, keep a
  private external Bridge available during evaluation. A Windows VM is optional
  legacy external-Bridge infrastructure, not a requirement.

## Install the Integration

1. In HACS, add this repository as a custom repository with category
   **Integration**.
2. Install the latest published **Codex Bridge** Integration and restart Home
   Assistant.
3. Open **Settings -> Devices & services**, select **Add integration**, and add
   **Codex Bridge**.

The HACS link in the [repository README](../README.md) installs only the
Integration. It neither installs nor publishes an App image.

The App manifest uses Home Assistant's current `app_config:rw` map permission
for its private state. Older `addon_config` wording refers to the legacy App
model and should not be used when checking or editing this repository.

## Install the App

Open **Settings -> Apps -> App store**, select the three-dot menu, then
**Repositories**. Add <https://github.com/Herbertmt978/HA_Codex_Bridge>. Wait
until the store offers a published App release, then install and start **Codex
Bridge**. Do not install App `0.6.1`; it fails closed during target-HAOS
readiness. The App has no ingress route, direct port, or browser-visible Bridge
URL; Supervisor discovery supplies the private connection using the App's
assigned HA-network IP. The App publishes a bounded, non-secret marker on
each start so Supervisor refreshes an unchanged record while retaining its
stable identity. If Home Assistant starts the Integration before the App is
reachable, wait until the App reports ready and retry the flow. The valid
discovery form remains available during this temporary failure, and the
Integration does not save an unverified endpoint.

## First run

1. Confirm the App reports ready. If it reports `sandbox_unavailable`, stop:
   do not weaken its sandbox or broaden mounts.
2. Open the Codex Bridge panel as a Home Assistant administrator.
3. Select **Sign in with ChatGPT**, then complete the displayed approved
   ChatGPT device-auth page in a browser signed in to the intended account.
4. Wait for the connected state. The panel checks the authoritative account
   state every two seconds while approval is pending, so a delayed completion
   notification does not require a reload. Home Assistant and ChatGPT login are
   separate sessions. **Cancel** stops only an active sign-in; **Sign out**
   removes an established session.
5. Create a Project and grant a small workspace below `/config/workspaces`.

## Configure capabilities

After the first reversible chat, the panel's administrator-only navigation can
manage scheduled automations (one-time, interval, or RFC 5545 recurrence),
workspace skills under `.agents/skills/`, global or project-root `AGENTS.md`,
Codex plugins and marketplaces, and outbound MCP servers. Home Assistant owns
the automation clock while the Bridge records idempotent claims and skipped
overlap/capacity/misfire outcomes. MCP is disabled by default. If you need it,
open **Settings -> Apps -> Codex Bridge -> Configuration**, enable **Enable
MCP**, save, and restart the App. MCP URLs must use trusted HTTPS hostnames;
literal IPs, localhost/internal hosts, and known non-public DNS answers are
rejected. DNS checks are best effort and are not a connection-time IP
allowlist. OAuth is an explicit one-shot flow, and bearer-token configuration
and MCP elicitation are unavailable.

These controls do not add a browser-facing App/Bridge endpoint. Review prompts,
instruction files, marketplace/plugin content, and every automation target
before enabling unattended work.

After connection, normal panel use can remain on Home Assistant. Initial
sign-in and re-authentication require browser access to the approved ChatGPT
device-auth page.

## Update an existing installation

1. In HACS, open **Codex Bridge**, choose **Update** or **Redownload**, select
   the latest Integration release, and restart Home Assistant.
2. Reload panel tabs that were open before the restart, then verify the four
   runtime versions shown at the top of the panel against the
   [release notes](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest).
3. Update the App separately from **Settings -> Apps -> Codex Bridge** when a
   new App version is offered. The App's auto-update toggle may apply released
   images automatically.
4. Make a cold backup before an App change. The first unattended update is
   proven, but restore and arbitrary prior-image selection are not validated
   rollback paths.

## After installation

- Read [remote access](remote-access.md) before exposing Home Assistant
  remotely.
- Enable App auto-update only after making a cold backup and accepting the
  experimental update/recovery limits described below.
- Make a cold backup before an App change; see
  [backup and recovery](backup-restore.md).
- Never paste device codes, cookies, bearer tokens, or API keys into App
  settings.
- See [SUPPORT.md](../SUPPORT.md) and [SECURITY.md](../SECURITY.md).
