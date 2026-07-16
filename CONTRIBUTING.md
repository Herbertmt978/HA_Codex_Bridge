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

The release being shipped is Integration `0.7.0`, App `0.7.0`, Bridge `0.6.0`,
and Codex `0.144.4`. It is experimental and `amd64` only; publication, signing,
and target-Home-Assistant acceptance remain pending. App/Integration `0.6.6`
is the signed published baseline; the `0.6.5` live-accepted matrix remains
historical evidence only. A source checkout, local
image, or unit test is not release evidence. Do not claim external
blocked-network routing, cold restore, the first future unattended App-update
canary, or App-image rollback works until each has its own target-system
evidence.

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
