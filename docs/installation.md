# Installation

## Status before you start

This guide includes the source release candidate: experimental, `amd64`-only
App `0.6.5`, Integration `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4`.
It is pending publication, signing, and target-Home-Assistant acceptance.
The previously published App `0.6.4` uses a signed immutable image with an
SPDX SBOM and build provenance. On target HAOS, Codex `0.144.4`'s official
`--no-proc` fallback works: denial of a fresh `/proc` mount leaves the sandbox
namespaces, read-only filesystem, AppArmor, and seccomp intact; `/proc` is
intentionally empty. App `0.6.1`'s fatal readiness cause was a sandbox-self-test
contract mismatch: it required `writableRoots` exactly `[workspace]`, while the
real `ha_bridge` `workspaceWrite` response includes bounded supplemental roots
(`.agents`, `.codex`, `.cursor`, `.git`, and `.vscode`) beneath the workspace.
The proc-less probe already used direct `capget`/`prctl`/`lsm_get_self_attr`
calls, without requesting `SYS_ADMIN` or weakening isolation; App `0.6.4`
retains canonical contained supplemental-root validation and hardened
`lsm_get_self_attr` record parsing. The previously published image passed
target-HAOS startup, its production sandbox self-test and attestation, an
authenticated API v1 readiness request, Supervisor discovery, Integration
pairing, and panel loading. App `0.6.4` adds private-IP Supervisor discovery and
retains bounded recovery after device approval, immediate entitlement-aware
model discovery, duration-aware usage windows, and resilient new-chat
hydration. The `0.6.5` candidate introduces a compact Codex-style sidebar,
focused chat canvas/composer, and accessible workspace navigation. When live
app-server catalogue discovery is unavailable, it dynamically reads the
installed Codex bundled catalogue; stale data retries after 15 seconds, a
verified last-known-good catalogue takes precedence, and a static fallback is
last. GPT-5.6 and model-specific Max/Ultra levels remain runtime-advertised
rather than hardcoded. A typed temporary artifact reservation preserves the
prior artifact view without a connection error even if the selected chat is
idle; unrelated failures still surface. Final ChatGPT account authorization
still requires the user. Remote access, the first
unattended automatic update, cold restore, and App-image rollback remain
acceptance checks for the intended Home Assistant installation.

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
   Assistant. The `0.6.5` source candidate is not installable through HACS
   until it is published.
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
until the store offers a published App version, then install and start **Codex
Bridge**. App `0.6.5` remains pending publication; App `0.6.4` is the prior
published release. Do not install App `0.6.1`; it fails closed during target-HAOS
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
4. Make a cold backup before an App change. The first unattended update and
   restore canary remains open, and arbitrary prior-image selection is not a
   validated rollback path.

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
