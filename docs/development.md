# Development

## Repository shape

| Area | Responsibility |
| --- | --- |
| `custom_components/codex_bridge` | Home Assistant Integration and panel surface. |
| `bridge_service` | Private Bridge API, runtime coordination, and tests. |
| `codex_bridge_app` | Stable, `amd64`-only Supervisor App definition, sandbox, and App documentation. |
| `frontend` | Panel source and browser tests. |
| `bridge_service/src/codex_bridge_service/automations.py` | Durable automation definitions, schedules, claims, and bounded run history. |
| `bridge_service/src/codex_bridge_service/capabilities.py` | Workspace skills, plugins, and marketplace adapter. |
| `bridge_service/src/codex_bridge_service/mcp_manager.py` | Constrained HTTPS MCP configuration and explicit OAuth lifecycle. |
| `bridge_service/src/codex_bridge_service/routes/agents.py` | Global/project `AGENTS.md` persistence and private rollback snapshots. |

Use [CONTEXT.md](../CONTEXT.md): Home Assistant, not the App, is the browser
boundary; the Integration and App are distinct components.

## Local checks

Use the Node and Python versions declared in the repository and CI. Install
Python test dependencies from `requirements-test.txt` and run `npm ci` before
frontend checks. Edit `frontend/src/` and regenerate the bundled assets; do
not edit the generated panel directly.

```text
npm run lint
npm run test:unit
npm run build
npm run test:e2e
python -m ruff check bridge_service custom_components scripts tests
python scripts/sync_app_release.py --check
python scripts/update_codex_lock.py --check codex_bridge_app/codex-release.json
```

Run the full Home Assistant Integration suite on Linux, matching CI:

```text
python -m pytest -q
```

For the Bridge suite, disable automatic loading of the Home Assistant pytest
plugin, then explicitly load the Bridge's required plugins. In PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q bridge_service\tests -p pytest_asyncio.plugin -p pytest_timeout
```

Use an isolated Linux worker for the full Bridge and App build checks. The
Home Assistant test plugin imports Linux-only modules and cannot run unchanged
on Windows. A local container test does not prove that the App sandbox works
on HAOS; verify the built image and startup attestation on the target as well.

This release pairs App `1.1.1`, Integration and panel `1.1.0`, with Bridge `0.8.0` and
Codex `0.155.1`. Keep their version authorities and release projections
consistent. Do not change runtime dependencies without regenerating the
hash-locked deployed requirements and testing the resulting App image.

For dependency updates, regenerate the affected lock from its source manifest:

```text
uv pip compile codex_bridge_app/requirements-build.in --generate-hashes --no-annotate --python-version 3.14 --output-file codex_bridge_app/requirements-build.txt
uv pip compile bridge_service/pyproject.toml --no-annotate --generate-hashes --python-version 3.14 --output-file codex_bridge_app/requirements-runtime.txt
```

Review the resulting diff. The build lock must retain setuptools, even if a
dependency bot removes it, and the runtime lock must meet every Bridge
dependency requirement. A deployed dependency change needs a new App patch
version through `python scripts/sync_app_release.py --bump-patch`.

## Behaviour to preserve

- Home Assistant owns scheduling; the Bridge owns definitions, durable claims
  and run history. The Scheduled editor translates friendly controls into the
  existing typed API and preserves unchanged saved schedules.
- Local chats survive ChatGPT account changes. Only stale provider continuity
  is detached; an unverified account cannot start a new turn.
- Model discovery prefers the live runtime, then verified cached data, then
  the installed bundled catalogue. Recovery data is marked stale. Do not add
  model-specific names to the picker.
- Keep unchanged UI controls mounted during HA state refreshes. Cover typed
  drafts, dropdowns, keyboard focus and explicit reset with browser tests.
- Skills, plugins and project instructions stay within their granted scope.
  MCP remains an explicit opt-in to trusted HTTPS servers. DNS validation is
  not connection-time egress enforcement, and OAuth URLs are one-shot.
- Native search and images depend on runtime support. They do not grant shell
  networking or enable the separately gated browser worker.

Treat signed publication, target startup, feature behaviour, external proxy
routes and recovery as separate checks. See the acceptance guides for
[remote access](acceptance/remote-access.md), [cold restore](acceptance/cold-restore.md)
and the [browser worker](acceptance/browser-worker.md).

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
- Keep run-stage and subagent telemetry enum/aggregate-only. Never project
  prompts, IDs, paths, commands, raw messages, URLs, or provider-controlled
  labels into the panel.
- PDF preview must stay on the authenticated HA artifact route, enforce both
  metadata and fetched-byte limits, verify the file signature, and render only
  through the bundled local PDF.js canvas renderer with scripting, eval, and XFA
  disabled. Do not add an iframe or native browser PDF embed. Invalid,
  HTML/SVG/XML, unknown-size, and oversized content must keep the safe
  open/download fallback. Do not add a panel-visible browser, CDP port,
  arbitrary URL proxy, or local MCP endpoint; follow ADR 0006 for future
  App-owned browser automation.
- Do not document a Supervisor prior-image selection as rollback until an
  immutable prior tag and restore procedure are published and tested.

## Verified updater setup

Dependabot maintains the repository's supported package manifests and Actions.
The Codex runtime uses a custom lock with verified archives, signatures and
protocol schemas, so the daily `Verified Codex update` workflow owns runtime
updates instead of Dependabot. It checks stable upstream releases, verifies
the assets, regenerates the contract and opens a narrowly scoped App update.
The lock includes Codex, its code-mode host and Bubblewrap from the same signed
release. All three are required in the staged App; a missing companion prevents
dynamic tools from running even when chat sign-in succeeds.

For unattended updates, the scheduled Codex updater uses a dedicated GitHub App
installed only on this repository. Grant that App **Contents: read and write**
and **Pull requests: read and write**; do not grant Actions, Packages,
Administration, webhook, or branch-protection bypass permissions. Store its
client ID as repository variable `CODEX_UPDATER_APP_CLIENT_ID` and its private
key as Actions secret `CODEX_UPDATER_APP_PRIVATE_KEY`. Also set repository
variable `CODEX_UPDATER_APP_ACTOR` to the installed App's login (for example,
`codex-updater[bot]`). All three values are mandatory: if any is absent, the
workflow fails with an actionable error when an update is available and creates
no pull request. Check failed scheduled runs so missing setup cannot silently
leave the runtime behind. It deliberately
does not fall back to `GITHUB_TOKEN`, because that token cannot start the
required pull-request CI. The required policy checks both the PR author and the
event actor against `CODEX_UPDATER_APP_ACTOR`, so a collaborator cannot retain
the bot-authored PR while pushing a later branch mutation.

Repository auto-merge must remain paired with protected `main` and its required
CI contexts. The updater signs its PR commit and arms squash auto-merge only
when GitHub verifies that commit and its exact head SHA. The required
workflow-policy job rejects any `automation/codex-*` PR that changes a path
outside the updater allowlist. The updater token can neither publish packages
nor bypass failed checks. Set repository variable `CODEX_UPDATE_PAUSED` to
`true` to stop new updater runs.

## Contribution hygiene

Read [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md), and
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) before opening a pull request.
Never commit device codes, credentials, cookies, tokens, private workspace
content, or unredacted Home Assistant diagnostics.
