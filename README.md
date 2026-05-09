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
  - WebSocket proxy commands for projects, threads, prompts, status, events, and artifacts.
  - Authenticated HTTP proxy views for uploads and downloads.
  - Full-screen Home Assistant panel UI for project-first chat, file uploads, artifact downloads, model controls, limit status, and copy-friendly responses.

## Project-first panel features

- Projects map to real folders on the Windows VM.
- Direct chats exist outside projects and are managed in the same left rail.
- Chats live under projects and inherit project defaults for:
  - model
  - thinking level
- Per-chat overrides can diverge from the project defaults without changing the whole project.
- The panel surfaces live 5-hour and weekly limit snapshots from the Codex auth session when available.
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
$env:CODEX_BRIDGE_HOST = "127.0.0.1"
$env:CODEX_BRIDGE_PORT = "8766"
$env:CODEX_BRIDGE_ROOT_PATH = "C:\\CodexHA"
$env:CODEX_BRIDGE_AUTH_TOKEN = "replace-this-with-a-long-random-token"
$env:CODEX_BRIDGE_CODEX_WRAPPER_PATH = "C:\\Users\\Ashby\\.codex\\.sandbox-bin\\codex.exe"
$env:CODEX_BRIDGE_BYPASS_SANDBOX = "1"
```

Then start the bridge:

```powershell
codex-bridge-service
```

If you prefer a wrapper script, `CODEX_BRIDGE_CODEX_WRAPPER_PATH` can point at a `.ps1` or `.py` file instead of `codex.exe`.

`CODEX_BRIDGE_BYPASS_SANDBOX=1` is recommended for isolated Windows VMs where the bridge should run with full local access and the Codex Windows sandbox helper has not been bootstrapped. It keeps file/tool workflows working through the bridge without depending on the missing sandbox setup marker and local sandbox users.

## Home Assistant setup

1. Copy `custom_components/codex_bridge` into your Home Assistant config's `custom_components/` directory, or install the repo through HACS.
2. Restart Home Assistant.
3. Add the `Codex Bridge` integration from Settings -> Devices & Services.
4. Enter:
   - `Bridge URL`: the Windows bridge URL reachable from Home Assistant, for example `http://192.168.1.50:8766`
   - `Bridge token`: the same token used by the bridge service
   - `Panel title`: the sidebar label you want in Home Assistant
5. Open the new sidebar panel.

## Upgrade to 0.4.3

1. In HACS, open `Codex Bridge`.
2. Choose `Redownload` or update to `0.4.3`.
3. Restart Home Assistant.
4. Hard refresh the browser.
5. Open `/codex-bridge`.

After the upgrade, you can:
- create or browse real VM-backed projects
- keep standalone direct chats outside projects
- archive or delete old chats from the left rail
- upload whole folders for VBA/codebase work
- preview and download generated artifacts from the side panel

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
