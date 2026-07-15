<div align="center">

# Home Assistant Codex Bridge

Run and supervise Codex work from Home Assistant without publishing a coding-agent endpoint to the browser.

[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=ha-codex-bridge&category=integration)
[![Integration release](https://img.shields.io/github/v/release/Herbertmt978/HA_Codex_Bridge?display_name=tag&label=Integration&color=0EA5E9)](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest)
[![CI](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/ci.yml)
[![App release](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml/badge.svg)](https://github.com/Herbertmt978/HA_Codex_Bridge/actions/workflows/release.yml)

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

This source release targets experimental, `amd64`-only App `0.6.3`; the
Integration is `0.6.2`, the optional external Bridge is `0.5.4`, and the
bundled Codex runtime is `0.144.4`. The public App is distributed as a signed
immutable image with an SPDX SBOM and build provenance; App `0.6.3` uses that
release workflow. On target HAOS, App
`0.6.2` completed startup, its production sandbox self-test and attestation, an
authenticated API v1 readiness request, Supervisor discovery, Integration
pairing, and panel loading. Codex uses its official `--no-proc`
restrictive-container fallback there; user, PID, and network namespaces, the
read-only filesystem, AppArmor, and seccomp remain enforced, and `/proc` is
intentionally empty. App `0.6.2` accepts only canonical supplemental roots
contained by the selected workspace, without requesting `SYS_ADMIN` or
weakening isolation. A redacted ChatGPT device-login start/cancel cycle also
passed; completing account authorization still requires the user. Remote-access
acceptance, the first unattended automatic update, cold restore, and prior-image
recovery remain validation work for the intended installation.

App `0.6.3` adds bounded recovery for delayed ChatGPT device sign-in, expires
the signed-out catalogue when account entitlements change, and continues to
discover every model and reasoning level from Codex rather than hardcoding a
release list. It also separates weekly-only usage from the disabled five-hour
window and keeps a newly created chat usable while secondary snapshots retry.
Integration `0.6.2` gives the panel a clearer Codex-style reading surface,
stronger chat and composer hierarchy, intentional empty states, and improved
keyboard and screen-reader navigation while retaining Home Assistant themes.

The external Bridge remains an optional, private compatibility path for people
who already operate one. Fresh Home Assistant OS installations should use App
`0.6.3` or newer; App `0.6.1` must not be used. Keep an existing
external Bridge as a recovery path until an App update and cold-restore
exercise has passed on the intended installation.

> [!IMPORTANT]
> The App is experimental and currently supports `amd64` Home Assistant OS.
> Installing the HACS Integration alone provides the panel but does not run
> Codex; install the Supervisor App as well, or explicitly configure the
> advanced private external Bridge.

## Install and first run

1. In **Settings -> Apps -> App store -> Repositories**, add
   <https://github.com/Herbertmt978/HA_Codex_Bridge>. Wait until the store
   offers App `0.6.3` or newer, then install and start **Codex Bridge**. Do not
   install App `0.6.1`; it fails closed during target-HAOS readiness.
2. Install the **Codex Bridge** Integration through HACS, restart Home
   Assistant, then add it in **Settings -> Devices & services**. App discovery
   supplies the private connection automatically; there is no host, port, or
   bearer token to copy.
3. Open the panel as a Home Assistant administrator. Select **Sign in with
   ChatGPT**, then use a browser to complete the approved ChatGPT device-auth
   page. **Cancel** only cancels an in-progress sign-in; **Sign out** removes an
   established Codex session. After approval, the panel checks the authoritative
   account state every two seconds until Codex reports the session ready.
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
