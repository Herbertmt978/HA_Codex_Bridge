# Contributing to Home Assistant Codex Bridge

This repository contains a Home Assistant Integration, a private Bridge
service, and an experimental Supervisor App. Preserve the boundary: browsers
talk to Home Assistant; Home Assistant talks privately to the Bridge; Codex
works only in an explicitly granted workspace.

## Before opening an issue or pull request

- Read [CONTEXT.md](CONTEXT.md) for shared product language and
  [AGENTS.md](AGENTS.md) for repository-wide engineering and release rules.
- Use [SUPPORT.md](SUPPORT.md) for setup and usage questions.
- Use [SECURITY.md](SECURITY.md) for potential vulnerabilities; do not disclose
  them in a public issue.
- Search existing issues and keep proposed changes narrowly scoped.
- Do not include device codes, tokens, cookies, credentials, private workspace
  paths, customer data, or raw logs that may contain them.

## Development expectations

1. Start from the current default branch and keep unrelated changes out of the
   pull request.
2. Explain the user-visible behavior, trust boundary, and compatibility impact.
3. Add or adjust regression tests for behavior changes.
4. Update documentation and `codex_bridge_app/CHANGELOG.md` when installation,
   authentication, workspace, model, security, update, recovery, or uninstall
   behavior changes.

The current candidate is Integration/App `0.7.5`, Bridge `0.6.3`, and Codex
`0.144.5` (experimental, `amd64` only), pending real Home Assistant acceptance.
Provider-gated native web search defaults to Live for Supervisor prompts and
automations, with bounded time-sensitive guidance; model-controlled shell
networking remains disabled.
Signed-in image generation requires both `imageGeneration` and `namespaceTools`,
uses no API key, and keeps bounded PNG/JPEG/WebP artifacts private. The compact
composer is a presentation change and does not expand authority. Coordinated
`0.7.3` is the target-HA-accepted baseline; signed App `0.7.4` carries the
verified Codex `0.144.5` runtime. The published/signed `0.7.0` baseline has
generic image digest
`sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`
with amd64 child `sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`;
main CI `29471288344` and publication `29471288457` succeeded, with signature,
SBOM, and provenance attached to the [release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0).
Target-Home-Assistant evidence for the published/live-accepted `0.7.1` release
is bounded: versions, ChatGPT Pro retention, dynamic GPT-5.6, five-hour `Off`,
chat/history, management-form retention, skill mutations, App auto-update, and
MCP form cancellation were observed. Its live plugin/marketplace list returned
`capabilities_unavailable` (HTTP 503), so no `0.7.1` plugin or marketplace
list/mutation acceptance was claimed. The `0.7.5` changes above remain candidate
evidence pending live acceptance. External blocked-
network routing, cold restore, and previous-image rollback remain unproven. A
source checkout, local image, or unit test is not release evidence.

## Local checks

```powershell
# Home Assistant Integration (run in Linux, matching CI)
python -m pytest -q

# Bridge suite (isolated from the Home Assistant pytest plugin)
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q bridge_service\tests -p pytest_asyncio.plugin -p pytest_timeout

python -m compileall -q bridge_service\src custom_components
node --check custom_components\codex_bridge\frontend\codex-bridge-panel.js
```

For an App, Supervisor, sandbox, proxy, or image change, state exactly which
target-system checks were performed and what remains. Never weaken the sandbox,
mount broader filesystems, expose a Bridge port, or add API-key login merely to
make a test pass.

## Documentation style

Use the terms in [CONTEXT.md](CONTEXT.md). Write the exact UI labels **Sign in
with ChatGPT**, **Cancel**, and **Sign out**. State that normal signed-in panel
use remains on Home Assistant, while initial sign-in and re-authentication need
browser access to the approved ChatGPT device-auth page.

For catalogue changes, preserve dynamic runtime discovery: stale data retries
after 15 seconds, a verified last-known-good record precedes the installed
Codex bundled catalogue, and static fallback is last. Do not hardcode GPT-5.6,
Max, Ultra, or any other model-specific option. For panel changes, preserve the
compact Codex-style sidebar and the typed transient artifact-reservation path,
including prior-artifact preservation when the selected chat is idle.

For capability surfaces, preserve these boundaries:

- Home Assistant owns automation timing; Bridge claims are revision-checked and
  idempotent, and skipped overlap/capacity/misfire outcomes remain visible.
- Skills and plugins stay scoped to the selected workspace and runtime
  configuration. Marketplace sources are HTTPS-only and must not contain
  credentials or private addresses.
- Global/project `AGENTS.md` writes are bounded, atomic, and backed up in
  private storage. Do not turn instruction files into a filesystem escape.
- MCP is outbound streamable HTTP to a trusted HTTPS endpoint. Preserve the
  best-effort rejection of known non-public DNS answers. Do not add a
  browser-facing MCP/App port, bearer-token setting, or persisted OAuth URL;
  preserve explicit OAuth and decline-only elicitation behavior.
