# Initial Architecture Baseline

**Snapshot date:** 2026-07-12

**Repository state:** `a5fff55` / release `0.5.3`

**Purpose:** Capture the implemented Windows-hosted system before the Home Assistant-native runtime migration.

## 1. Project structure

| Path | Current responsibility |
|------|------------------------|
| `bridge_service/` | Installable Python bridge service, tests, and built artifacts. |
| `bridge_service/src/codex_bridge_service/main.py` | Reads process settings and constructs the FastAPI application. |
| `bridge_service/src/codex_bridge_service/app.py` | Composes storage, runner, model, account, authentication, and HTTP routes. |
| `custom_components/codex_bridge/` | HACS-installable Home Assistant integration, config flow, authenticated proxy, WebSocket commands, and panel registration. |
| `custom_components/codex_bridge/frontend/codex-bridge-panel.js` | Home Assistant project, chat, file, diagnostics, and sign-in user interface. |
| `scripts/` | Windows launcher/update lifecycle and associated utilities. |
| `README.md` | Windows-first installation, operation, security, and recovery guidance. |
| `hacs.json` | HACS repository metadata. |

The repository has no Home Assistant App target, `repository.yaml`, container build, CI workflow, or declared project license at this snapshot.

## 2. Tech stack

- Python 3.12+, FastAPI, Uvicorn, Pydantic, HTTPX, and multipart handling for the Bridge.
- Home Assistant custom integration APIs, `aiohttp`, and Voluptuous for the Integration.
- Framework-free JavaScript custom panel using Home Assistant WebSocket and authenticated HTTP surfaces.
- JSON files under the Bridge root for projects, chats, events, uploads, and artifacts.
- Codex CLI subprocesses using `codex exec --json`; model discovery uses the installed Codex runtime.
- Pytest and pytest-asyncio for the existing automated suite.
- PowerShell for the Windows updater and its tests.

## 3. Ownership mapping

| Concern | Canonical owner at this snapshot |
|---------|----------------------------------|
| Bridge process composition | `bridge_service/src/codex_bridge_service/app.py` |
| Process configuration | `bridge_service/src/codex_bridge_service/settings.py` |
| Project/chat/file persistence | `bridge_service/src/codex_bridge_service/storage.py` |
| Codex run lifecycle | `bridge_service/src/codex_bridge_service/runner.py` |
| Codex subprocess environment | `bridge_service/src/codex_bridge_service/codex_process.py` |
| Model catalogue | `bridge_service/src/codex_bridge_service/model_catalog.py` |
| Login/logout process | `bridge_service/src/codex_bridge_service/codex_auth.py` |
| Bridge HTTP contract | `bridge_service/src/codex_bridge_service/routes/` |
| HA-to-Bridge client | `custom_components/codex_bridge/bridge_api.py` |
| HA setup and credential storage | `custom_components/codex_bridge/config_flow.py` and config entry data |
| HA authorization boundary | `custom_components/codex_bridge/websocket_api.py` and `http.py` |
| User experience | `custom_components/codex_bridge/frontend/codex-bridge-panel.js` |
| Windows CLI update | `scripts/` |

## 4. Contract inventory

- The Bridge exposes bearer-token-protected JSON HTTP routes for readiness, status, projects, chats, prompts, events, authentication, attachments, and artifacts.
- The Integration stores a Bridge URL and token in a Home Assistant config entry, verifies `/ready`, and proxies administrator-only commands.
- The panel calls only Home Assistant custom WebSocket commands and authenticated Home Assistant HTTP views; it does not receive the Bridge token.
- Thread events are append-only records replayed by numeric position; the panel polls or subscribes through the Integration.
- Project records currently accept arbitrary host absolute paths and chats materialize workspace paths from those records.
- Authentication status is a mutable record produced by parsing human-oriented `codex login --device-auth` output.
- Model results distinguish live, cached, and fallback catalogues, with special-project default reconciliation.
- Release `0.5.3` is the shared version in the Python package and Home Assistant manifest.

## 5. Dependency direction convention

The panel depends on Integration contracts; the Integration depends on the Bridge HTTP contract; the Bridge coordinates storage and Codex subprocesses. The browser does not call the Bridge directly. Windows process and update scripts own the deployed Codex runtime outside the repository's Python package.

## 6. Test system

- Automated tests live under `bridge_service/tests/` and cover storage, routes, runner behavior, authentication, model discovery, account/limits diagnostics, settings, security, and Windows updater behavior.
- The last verified pre-migration suite reported 115 passing tests.
- There is no committed Home Assistant integration test harness, frontend test/build pipeline, container test, multi-architecture test, or real HA OS acceptance suite.

## 7. Build and deploy

- The Bridge is built as a Python wheel and installed on a persistent Windows machine or VM.
- Codex is installed and authenticated in the Windows account.
- A PowerShell scheduled task checks for stable Codex releases and updates the VM installation.
- The Integration is installed from HACS or copied into Home Assistant.
- There is no GitHub Actions release pipeline or GHCR image at this snapshot.

## 8. Known anti-patterns

- Runtime ownership is split between this repository and an independently maintained Windows VM.
- Defaults, labels, setup copy, and file semantics embed Windows/VM assumptions.
- Project paths are not confined to a single explicitly mounted workspace root.
- Codex subprocesses inherit nearly the entire Bridge environment, which is unsafe in a Supervisor App containing service credentials.
- Login/logout is driven by stdout parsing and has race, cancellation, restart, and stale-status weaknesses.
- Authentication changes are not serialized against active Codex runs.
- The panel has no complete sign-out/cancel flow and no auth event subscription independent of chats.
- Integration HTTP uploads spool complete files to Home Assistant Core temporary storage; downloads buffer complete artifacts.
- There is no immutable container or rollback-tested automatic update path.

## 9. Last review findings

**Review date:** 2026-07-12

**Reviewers:** Codex architecture review plus focused auth, container/release, and Home Assistant App research.

Open findings:

1. The canonical runtime should move from Windows to a Supervisor-managed App.
2. App discovery must replace manual URL/token pairing for the primary setup.
3. Structured Codex app-server account events must replace human stdout parsing.
4. A monotonic, serialized auth coordinator must reject stale worker updates and coordinate with runs.
5. The Codex child environment must be an allowlist that excludes Supervisor and Bridge secrets.
6. Workspace paths must be confined and symlink-safe.
7. Protected real HA OS must prove the Codex Linux sandbox before VM retirement.
8. Dynamic model recovery must reconcile direct-chat defaults before the first post-recovery chat.
9. Remote Nabu Casa operation needs explicit streaming, reconnect, upload, and download acceptance evidence.

## 10. Compatibility boundaries

- Existing Bridge HTTP consumers and manual external Bridge config entries must continue working during a bounded rollback window.
- Existing Windows workspaces must never be deleted or moved automatically.
- The user explicitly chose a fresh HA chat/project history; no metadata or credential migration is required.
- The panel remains the sole browser-facing Codex UI and must continue to enforce Home Assistant administrator authorization.
- Model catalogue fallback must stay explicitly provisional and must not become a permanent chat override after recovery.
