# Codex Bridge contributor instructions

These instructions apply to the whole repository. Read `CONTEXT.md` before
changing product language or architecture.

## Product boundary

- The browser talks only to Home Assistant. The Integration proxies requests to
  the private App/Bridge; do not expose an App, Bridge, Codex, or MCP listener
  to the browser.
- Home Assistant owns administrator authentication and wall-clock automation
  scheduling. The Bridge owns durable automation definitions, claims, run
  history, workspace confinement, and Codex runtime coordination.
- The primary supported path is the Home Assistant App with ChatGPT account
  device login. Keep the external Bridge as a private compatibility path.
- Treat prompts, workspaces, plugins, skills, marketplaces, MCP servers,
  `AGENTS.md` content, OAuth responses, and Codex runtime output as untrusted.

## Engineering rules

- Preserve capability negotiation. A newer Integration must not call a feature
  that an older App did not advertise.
- Unattended automations fail closed: decline approvals and elicitations, avoid
  hidden interaction state, record terminal outcomes, and recover durable
  claims after restart.
- Keep MCP disabled by default and capability-gated behind the explicit App
  option. Constrain enabled MCP configuration to trusted HTTPS hostnames,
  reject credentials and known non-public addresses, never expose bearer-token
  settings, document that DNS validation is not connection-time enforcement,
  and keep OAuth authorization URLs one-shot and uncached.
- Confine skills and project instructions to the selected workspace. Global
  instructions stay in the fixed private Codex home. Writes must be bounded,
  atomic, no-follow, and privately backed up where the implementation promises.
- Reuse the panel's design tokens and accessible interaction patterns. Preserve
  loading, empty, error, retry, keyboard, narrow-screen, and reduced-motion
  states.
- Edit `frontend/src/`, then regenerate
  `custom_components/codex_bridge/frontend/codex-bridge-panel.js` with
  `npm run build`; do not hand-edit the generated bundle.
- Keep App, Integration, panel, Bridge, Codex lock, changelog, and documentation
  versions synchronized through the owning scripts and tests.

## Verification

Run the smallest relevant checks while iterating, then the applicable release
gates before merging:

```text
npm run lint
npm run test:unit
npm run build
python -m ruff check bridge_service custom_components scripts tests
python -m pytest -q bridge_service/tests
python scripts/sync_app_release.py --check
python scripts/update_codex_lock.py --check codex_bridge_app/codex-release.json
```

Home Assistant's current test plugin imports Linux-only modules. Run the full
Integration suite and App image build in the Linux CI workflow even when a
Windows workstation can run only the plugin-independent slices.

## Release discipline

- Do not overwrite immutable App image tags or reuse an older image digest in a
  new release claim.
- Verify CI, the signed image digest, provenance, SBOM attestation, and target
  Home Assistant behavior before publishing acceptance evidence.
- Never commit or print ChatGPT credentials, Home Assistant tokens, OAuth state,
  cookies, private keys, or complete authorization headers.
