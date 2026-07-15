# Development

## Repository shape

| Area | Responsibility |
| --- | --- |
| `custom_components/codex_bridge` | Home Assistant Integration and panel surface. |
| `bridge_service` | Private Bridge API, runtime coordination, and tests. |
| `codex_bridge_app` | Experimental Supervisor App definition, sandbox, and App documentation. |
| `frontend` | Panel source and browser tests. |

Use [CONTEXT.md](../CONTEXT.md): Home Assistant, not the App, is the browser
boundary; the Integration and App are distinct components.

## Local checks

Run the smallest relevant check first, then the applicable broader checks:

```powershell
# Home Assistant Integration (run in Linux, matching CI)
python -m pytest -q

# Bridge suite (isolated from the Home Assistant pytest plugin)
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q bridge_service\tests -p pytest_asyncio.plugin -p pytest_timeout

python -m compileall -q bridge_service\src custom_components
node --check custom_components\codex_bridge\frontend\codex-bridge-panel.js
```

Local containers and unit tests do not validate protected Home Assistant OS
behavior. On target HAOS, pinned Codex `0.144.4`'s official `--no-proc` fallback
works: denial of a fresh `/proc` mount leaves the sandbox namespaces, read-only
filesystem, AppArmor, and seccomp intact; `/proc` is intentionally empty. App
`0.6.1`'s fatal readiness cause was a sandbox-self-test contract mismatch: it
required `writableRoots` exactly `[workspace]`, while the real `ha_bridge`
`workspaceWrite` response includes bounded supplemental roots (`.agents`,
`.codex`, `.cursor`, `.git`, and `.vscode`) beneath the workspace. The proc-less
probe already used direct `capget`/`prctl`/`lsm_get_self_attr` calls, without
requesting `SYS_ADMIN` or weakening isolation; App `0.6.4` retains canonical
contained supplemental roots and hardens `lsm_get_self_attr` record parsing.
The published App `0.6.4` image passed target-HAOS startup, its production
sandbox self-test and attestation, an authenticated API v1 readiness request,
Supervisor discovery, Integration pairing, and panel loading. Remote access,
the first unattended automatic update, cold restore, and App-image rollback
still need post-release validation.

The source release candidate matrix is Integration `0.6.5`, App `0.6.5`,
Bridge `0.5.5`, and Codex `0.144.4`. It is pending publication, signing, and
target-Home-Assistant acceptance. Its catalogue recovery must remain ordered:
live app-server discovery first, then a verified last-known-good record, then
the dynamically read installed Codex bundled catalogue, and static fallback
last; stale records retry after 15 seconds. Do not add hardcoded model names:
GPT-5.6 and model-specific Max/Ultra are runtime data. Preserve the typed
artifact reservation behavior, including prior artifact preservation when the
selected chat is idle.

## Supervisor discovery contract

The App publishes its endpoint through Supervisor discovery using the
Supervisor-assigned private HA-network IP. Do not substitute the App hostname:
the Core-to-App path must remain private and hostname resolution is not
guaranteed in every Supervisor network. The App manifest uses the current
`app_config:rw` map permission; `addon_config` is legacy terminology and must
not be reintroduced.

Discovery keeps a stable identity but includes a bounded, non-secret
publication marker on each App start. This causes Supervisor to re-push an
otherwise unchanged record after a restart. The Integration validates the
discovered endpoint before storing it. A temporary connection failure returns a
retryable confirmation form, so tests and callers must not persist an
unverified URL or token.

## App development rules

- Keep the image immutable. Do not self-update Codex, download executables at
  startup, or mutate a release lock.
- Do not add a direct port, ingress route, host networking, Docker access, broad
  host mappings, or API-key login to unblock development.
- Treat `sandbox_unavailable` as fatal. Fix the sandbox/build problem instead
  of weakening isolation.
- Keep browser traffic on Home Assistant. The approved ChatGPT device-auth page
  is needed only for initial sign-in and re-authentication.
- Keep the compact Codex-style sidebar within Home Assistant's theme and
  accessibility conventions; do not turn a typed transient artifact reservation
  into a connection error.
- Do not document a Supervisor prior-image selection as rollback until an
  immutable prior tag and restore procedure are published and tested.

## Contribution hygiene

Read [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md), and
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) before opening a pull request.
Never commit device codes, credentials, cookies, tokens, private workspace
content, or unredacted Home Assistant diagnostics.
