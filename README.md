# Home Assistant Codex Bridge

This repository turns Home Assistant into the only browser-visible surface for talking to Codex on a Windows machine.

The flow is:

1. A small bridge service runs next to Codex on Windows.
2. Home Assistant connects to that bridge with a token.
3. The Home Assistant panel proxies thread creation, prompts, uploads, event polling, and artifact downloads.
4. Your work browser only ever talks to Home Assistant.

## What is included

- `bridge_service/`
  - FastAPI bridge for thread storage, prompt execution, uploads, event replay, and artifact downloads.
  - Background runner that shells out to `codex exec --json` / `codex exec resume --json`.
- `custom_components/codex_bridge/`
  - Home Assistant custom integration with config flow.
  - WebSocket proxy commands for threads, prompts, events, and artifacts.
  - Authenticated HTTP proxy views for uploads and downloads.
  - Full-screen Home Assistant panel UI for chat, responses, file uploads, and artifact downloads.

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
```

Then start the bridge:

```powershell
codex-bridge-service
```

If you prefer a wrapper script, `CODEX_BRIDGE_CODEX_WRAPPER_PATH` can point at a `.ps1` or `.py` file instead of `codex.exe`.

## Home Assistant setup

1. Copy `custom_components/codex_bridge` into your Home Assistant config's `custom_components/` directory, or install the repo through HACS.
2. Restart Home Assistant.
3. Add the `Codex Bridge` integration from Settings -> Devices & Services.
4. Enter:
   - `Bridge URL`: the Windows bridge URL reachable from Home Assistant, for example `http://192.168.1.50:8766`
   - `Bridge token`: the same token used by the bridge service
   - `Panel title`: the sidebar label you want in Home Assistant
5. Open the new sidebar panel.

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
