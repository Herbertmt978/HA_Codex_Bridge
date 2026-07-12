# Home Assistant Codex Bridge

This repository turns Home Assistant into the only browser-visible surface for talking to Codex on a Windows machine.

The flow is:

1. A small bridge service runs next to Codex on Windows.
2. Home Assistant connects to that bridge with a token.
3. The Home Assistant panel proxies project management, thread creation, prompts, uploads, event polling, and artifact downloads.
4. Your work browser only ever talks to Home Assistant.

## What is included

- `bridge_service/`
  - FastAPI bridge for project/thread storage, prompt execution, uploads, event replay, limit snapshots, and artifact downloads.
  - Background runner that shells out to `codex exec --json` / `codex exec resume --json`.
- `custom_components/codex_bridge/`
  - Home Assistant custom integration with config flow.
  - WebSocket proxy commands for projects, threads, prompts, status, Codex VM auth, events, and artifacts.
  - Authenticated HTTP proxy views for uploads and downloads.
  - Full-screen Home Assistant panel UI for project-first chat, file uploads, artifact downloads, model controls, limit status, and copy-friendly responses.

## Project-first panel features

- Projects map to real folders on the Windows VM.
- Direct chats exist outside projects and are managed in the same left rail.
- Chats live under projects and inherit project defaults for:
  - model
  - thinking level
- The model picker is populated from the configured VM Codex executable at runtime, including each model's supported thinking levels.
- New projects inherit the effective Codex model and reasoning defaults instead of a bridge release's hard-coded values.
- Per-chat overrides can diverge from the project defaults without changing the whole project.
- The panel surfaces live 5-hour and weekly limit snapshots from the Codex auth session when available.
- Expired Codex logins are shown clearly with a VM sign-in action and copyable device-code details.
- Chats can be archived, restored, or deleted from the left rail.
- Folder uploads preserve relative paths for larger VBA/codebase drops.
- Workspace artifacts can be previewed in-panel for text, image, and PDF outputs.
- The right-side panel shows progress, artifacts, previews, and workspace details without duplicating attachment management.
- A one-click workspace archive action can bundle workspace files and uploads into a downloadable zip artifact.
- Assistant messages have explicit copy buttons and are rendered in stable selectable blocks so code can be copied cleanly from Edge.

## Bridge service setup

From `ha-codex-bridge/bridge_service`:

```powershell
python -m pip install -e .[test]
```

Set the bridge environment variables on the Windows machine that can run Codex:

```powershell
$bridgeToken = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:CODEX_BRIDGE_HOST = "127.0.0.1"
$env:CODEX_BRIDGE_PORT = "8766"
$env:CODEX_BRIDGE_ROOT_PATH = "C:\\CodexHA"
$env:CODEX_BRIDGE_AUTH_TOKEN = $bridgeToken
$env:CODEX_BRIDGE_CODEX_WRAPPER_PATH = "$env:LOCALAPPDATA\\Programs\\OpenAI\\Codex\\bin\\codex.exe"
$env:CODEX_BRIDGE_BYPASS_SANDBOX = "1"
$env:CODEX_BRIDGE_RUN_IDLE_TIMEOUT_SECONDS = "1800"
$env:CODEX_BRIDGE_MODEL_DISCOVERY_TIMEOUT_SECONDS = "10"
$env:CODEX_BRIDGE_MODEL_CACHE_TTL_SECONDS = "600"
```

Store the generated token securely and enter the same value in Home Assistant. Version 0.5.0 refuses missing tokens, tokens shorter than 32 characters, and the old documentation placeholders; validation errors are configured not to echo a rejected token.

Then start the bridge:

```powershell
codex-bridge-service
```

If you prefer a wrapper script, `CODEX_BRIDGE_CODEX_WRAPPER_PATH` can point at a `.ps1` or `.py` file instead of `codex.exe`.

The bridge bearer token is sent on every Home Assistant-to-VM request. Keep plain HTTP on a trusted private network only; use an HTTPS reverse proxy or private tunnel when traffic crosses an untrusted network.

`CODEX_BRIDGE_BYPASS_SANDBOX=1` is recommended for isolated Windows VMs where the bridge should run with full local access and the Codex Windows sandbox helper has not been bootstrapped. It keeps file/tool workflows working through the bridge without depending on the missing sandbox setup marker and local sandbox users.

If Codex reports that the refresh token was already used or a websocket request returns `401 Unauthorized`, the bridge will mark auth as expired and expose a VM sign-in action in Home Assistant. That flow can start and monitor sign-in from HA, but OpenAI still requires the account approval step to be completed from a device/browser that can reach ChatGPT or from the VM console.

## Automatic Codex CLI updates

The model catalogue follows the executable configured by `CODEX_BRIDGE_CODEX_WRAPPER_PATH`. To check that executable daily for Codex releases, install the included scheduled task from an elevated PowerShell prompt on the bridge VM (the task itself runs with limited privileges):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-CodexAutoUpdate.ps1 `
  -CodexPath "$env:LOCALAPPDATA\Programs\OpenAI\Codex\bin\codex.exe" `
  -DailyAt "03:15" `
  -RunNow
```

The updater pins the official installer to that exact directory, puts Windows `tar.exe` first for the update process, backs up the launcher/real executable layout, and validates `codex debug models --bundled` after updating. A failed update or model smoke test rolls back automatically. It writes only sanitized version/status lines to `C:\CodexHA\logs\codex-update.log`, retries transient task failures, and never reads or logs bridge credentials. Preview or remove it with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-CodexAutoUpdate.ps1 -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-CodexAutoUpdate.ps1 -Uninstall
```

## Home Assistant setup

1. Copy `custom_components/codex_bridge` into your Home Assistant config's `custom_components/` directory, or install the repo through HACS.
2. Restart Home Assistant.
3. Add the `Codex Bridge` integration from Settings -> Devices & Services.
4. Enter:
   - `Bridge URL`: the Windows bridge URL reachable from Home Assistant, for example `http://192.168.1.50:8766`
   - `Bridge token`: the same token used by the bridge service
   - `Panel title`: the sidebar label you want in Home Assistant
5. Open the new sidebar panel.

## Upgrade to 0.5.0

1. Before restarting the VM bridge, replace any token shorter than 32 characters or old placeholder in both the VM service configuration and Home Assistant.
2. Update the VM bridge service and point `CODEX_BRIDGE_CODEX_WRAPPER_PATH` at the current managed Codex executable.
3. In HACS, open `Codex Bridge` and choose `Redownload` or update to `0.5.0`.
4. Restart the VM bridge and Home Assistant.
5. Hard refresh the browser and open `/codex-bridge`.

After the upgrade, you can:
- automatically see newly available Codex models, including GPT-5.6 variants, without another bridge code change
- use the reasoning levels advertised by each model, including `max` and `ultra` where available
- create projects with the VM's effective Codex model/reasoning defaults
- retain configured or stored model choices when a temporary catalogue response omits them
- restrict the panel, WebSocket commands, uploads, and downloads to Home Assistant administrators
- validate the bridge token during Home Assistant setup instead of accepting any token through the public health endpoint
- keep the VM Codex CLI current with the optional logged scheduled updater
- see Codex refresh-token failures as a clear auth-expired banner instead of a raw websocket 401
- start a VM-side Codex device sign-in from Home Assistant and copy the displayed device code/login URL
- refresh the bridge auth status after completing sign-in on a phone, home browser, or the VM console
- avoid permanently stuck runs when Codex stops emitting output; silent runs now fail cleanly after the bridge watchdog timeout
- recover stale `running` chats after a bridge restart instead of leaving them pinned forever
- create a project by entering only its name; the bridge creates a VM folder under `C:\CodexHA\project-workspaces`
- use stable create/edit form buttons during background refreshes
- read long workspace/account detail values without overlapping text
- see the Codex Bridge brand icon in HACS and the Home Assistant integration UI
- create or browse real VM-backed projects
- keep standalone direct chats outside projects
- archive or delete old chats from the left rail
- upload whole folders for VBA/codebase work
- paste screenshots directly into the prompt box
- preview and download generated artifacts from the side panel
- use the sleeker colour-accented panel with quieter idle polling and faster event replay
- see the signed-in Codex account email/plan in the panel without exposing auth tokens
- stop a running Codex job from the panel
- see bridge diagnostics, tool availability, and the latest bridge/Codex error in the side panel
- get persistent out-of-credit and last-error banners until a new state replaces them
- copy full assistant replies or individual fenced code blocks line-for-line

## Dashboard launcher

The main UI is a full Home Assistant panel because chat, files, and run history need more room than a small tile. If you want a dashboard entry point, add a button card that opens the panel:

```yaml
type: button
name: Codex Bridge
icon: mdi:robot-outline
tap_action:
  action: navigate
  navigation_path: /codex-bridge
```

## Development

Run the bridge tests:

```powershell
pytest bridge_service/tests -q
```

Quick syntax checks for the custom component and panel:

```powershell
python -m compileall bridge_service/src custom_components/codex_bridge
node --check custom_components/codex_bridge/frontend/codex-bridge-panel.js
```
