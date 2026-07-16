# Development

## Repository shape

| Area | Responsibility |
| --- | --- |
| `custom_components/codex_bridge` | Home Assistant Integration and panel surface. |
| `bridge_service` | Private Bridge API, runtime coordination, and tests. |
| `codex_bridge_app` | Experimental Supervisor App definition, sandbox, and App documentation. |
| `frontend` | Panel source and browser tests. |
| `bridge_service/src/codex_bridge_service/automations.py` | Durable automation definitions, schedules, claims, and bounded run history. |
| `bridge_service/src/codex_bridge_service/capabilities.py` | Workspace skills, plugins, and marketplace adapter. |
| `bridge_service/src/codex_bridge_service/mcp_manager.py` | Constrained HTTPS MCP configuration and explicit OAuth lifecycle. |
| `bridge_service/src/codex_bridge_service/routes/agents.py` | Global/project `AGENTS.md` persistence and private rollback snapshots. |

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
requesting `SYS_ADMIN` or weakening isolation; App `0.7.0` retains canonical
contained supplemental roots and hardens `lsm_get_self_attr` record parsing.
The published App/Integration `0.7.0` target run reported Bridge `0.6.0` and
Codex `0.144.4`, retained ChatGPT Pro, showed dynamic GPT-5.6, rendered the
five-hour window `Off`, preserved chat/history, and persisted App auto-update
and MCP opt-in after restart. Management forms lose unsaved values during a
background rerender; the `0.7.1` candidate contains the fix. Do not claim
automation, skills, plugins/marketplaces, MCP-server, or `AGENTS.md` mutation
acceptance until retested. The first unattended App update is proven. External
blocked-network/Nabu Casa/Cloudflare routing, cold restore, and previous-image
rollback remain unproven.

The current candidate is Integration/App `0.7.3`, Bridge `0.6.2`, and Codex
`0.144.4`; it is pending real Home Assistant acceptance. Provider-gated native
web search defaults to Live only for Supervisor prompts and automations,
provider capabilities are re-negotiated after authentication, and
model-controlled shell networking remains disabled. Signed-in image generation
requires `imageGeneration` plus `namespaceTools`, uses no API key, and stores
only private bounded PNG/JPEG/WebP artifacts. Preserve the compact panel and
the updater's pinned `jsonschema` dependency-installation fix. The
published/signed `0.7.0` baseline has generic image digest
`sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`
with amd64 child
`sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`.
Main CI `29471288344` and publication `29471288457` succeeded, with signature,
SBOM, and provenance on the [release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0).
Its catalogue recovery must remain ordered:
live app-server discovery first, then a verified last-known-good record, then
the dynamically read installed Codex bundled catalogue, and static fallback
last; stale records retry after 15 seconds. Do not add hardcoded model names:
GPT-5.6 and model-specific Max/Ultra are runtime data. Preserve the typed
artifact reservation behavior, including prior artifact preservation when the
selected chat is idle.

Capability changes need focused tests for the trust boundary as well as the
happy path. Automations must remain Home Assistant-scheduled and idempotent;
skills/plugins/marketplaces must remain workspace/config bounded; `AGENTS.md`
writes must remain atomic with private backups; and MCP must continue rejecting
literal/private endpoints, known non-public DNS answers, and bearer-token
configuration while keeping OAuth URLs one-shot and elicitation decline-only.
Because Codex owns the eventual connection and DNS may change after validation,
document the administrator trust requirement. Document any target-system gap
rather than implying that local tests prove unattended recovery or proxy
behavior.

For provider tools, tests and documentation must preserve the gate: advertise
web search only after a successful provider-capability probe, and advertise
image generation only when both `imageGeneration` and `namespaceTools` are
true. Never turn provider-side web search into a shell-network exemption, an
API-key flow, or a public artifact URL.

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
