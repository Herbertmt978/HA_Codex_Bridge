<div align="center">

<img src="brand/logo.png" alt="Home Assistant Codex Bridge" width="360">

# Home Assistant Codex Bridge

Run and supervise Codex work from Home Assistant without publishing a coding-agent endpoint to the browser.

[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=ha-codex-bridge&category=integration)
[![CI](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml)
[![App release](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml)
[![App: 0.6.1 experimental](https://img.shields.io/badge/App-0.6.1%20Experimental-F59E0B)](codex_bridge_app/README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-00897B.svg)](LICENSE)

[Installation](docs/installation.md) | [Remote access](docs/remote-access.md) | [Backup and recovery](docs/backup-restore.md) | [Security](SECURITY.md) | [Support](SUPPORT.md)

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

## Two components, two installation paths

- **HACS Integration:** the `codex_bridge` custom integration supplies the
  administrator panel and is installed through HACS. The HACS link above opens
  a custom-repository flow; it is not a statement that this project is listed,
  reviewed, endorsed, or supported by HACS or Home Assistant.
- **Supervisor App:** the private runtime intended to run the Bridge and Codex
  alongside Home Assistant. Add this repository to the Home Assistant App
  store to install its published immutable image.

The App is experimental, `amd64`-only, and version `0.6.1`; the Integration is
`0.5.4`, the optional external Bridge is `0.5.3`, and the bundled Codex runtime
is `0.144.4`. The release workflow publishes a signed GHCR image with an SPDX
SBOM and build provenance. A protected-runtime image also passed the sandbox
self-test and authenticated readiness check on an amd64 Home Assistant OS
development VM on 14 July 2026. Remote-access acceptance, the first automatic
update, and prior-image recovery still need validation on the intended Home
Assistant installation.

The external Bridge remains an optional, private compatibility path for people
who already operate one. Fresh Home Assistant OS installations should use the
App. Keep an existing external Bridge only as a recovery path until an App
update and cold-restore exercise has passed on the intended installation.

> [!IMPORTANT]
> The App is experimental and currently supports `amd64` Home Assistant OS.
> Installing the HACS Integration alone provides the panel but does not run
> Codex; install the Supervisor App as well, or explicitly configure the
> advanced private external Bridge.

## Install and first run

1. In **Settings -> Apps -> App store -> Repositories**, add
   <https://github.com/Herbertmt978/HA_Codex_Bridge>. Install and start
   **Codex Bridge**.
2. Install the **Codex Bridge** Integration through HACS, restart Home
   Assistant, then add it in **Settings -> Devices & services**. App discovery
   supplies the private connection automatically; there is no host, port, or
   bearer token to copy.
3. Open the panel as a Home Assistant administrator. Select **Sign in with
   ChatGPT**, then use a browser to complete the approved ChatGPT device-auth
   page. **Cancel** only cancels an in-progress sign-in; **Sign out** removes an
   established Codex session.
4. Create a Project and grant a small workspace beneath `/config/workspaces` in
   App mode. Review changes before expanding that boundary.

The Home Assistant and ChatGPT sessions are separate. After a ChatGPT session
is established, normal panel use can remain on the Home Assistant origin.
Initial sign-in and re-authentication still require browser access to the
approved ChatGPT device-auth page. This account flow does not use an OpenAI API
key.

## Updates and recovery

App images are immutable: a running container does not update Codex or itself.
Home Assistant can offer a released App update and can apply it automatically
after the administrator enables the App's auto-update toggle. Upstream Codex
updates first arrive as a verified, reviewable repository PR; unattended merge
remains disabled until a real update/recovery canary passes. The Supervisor App
does **not** currently provide a validated way to select an arbitrary prior
image, so make a cold Home Assistant backup before an App change. Keep a private
external Bridge where one already exists until cold restore has been exercised;
see [backup and recovery](docs/backup-restore.md).

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
