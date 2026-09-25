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
  option. Public connections retain trusted HTTPS hostnames and OAuth; their
  DNS screen is best effort. Local LAN/App connections require the separate
  default-disabled option, per-endpoint consent and the private local MCP relay.
  The relay pins approved private addresses at connection time, verifies TLS,
  rejects redirects and never forwards its private capability header upstream.
  Never pass a local upstream URL or upstream credential directly to Codex.
  MCP-02 permits write-only bearer tokens and named authentication headers
  through the private relay, bound to one approved endpoint. Reject routing,
  protocol and reserved headers. Submit credentials through bounded administrator
  HTTP views, never WebSocket commands, prompts or browser persistence. Keep
  private credentials out of responses, logs and native configuration; redact
  reflected secrets in relay responses. Arbitrary native stdio commands and
  local OAuth remain unsupported. Keep OAuth authorisation URLs one-shot and uncached.
  This local exception does not change browser, shell or Host Access permissions.
- For MCP-03 stdio support, use the separate boundary in
  [ADR 0009](docs/aegis/adr/0009-isolated-stdio-mcp.md). Codex must see only the Bridge-owned
  authenticated loopback HTTP adapter, never a native executable command or
  worker environment. Do not activate a worker without its own HAOS-proven
  Bubblewrap/AppArmor/namespace/seccomp attestation, verified approved package
  revision, explicit grants, tool allow-list and resource budget. The first
  supported runtime is Python 3.14 on amd64 with no network or workspace files.
  Keep the stdio App option off by default, preserve existing HTTPS/LAN MCP
  rules, and fail closed on uncertain startup, update or teardown.
- Preserve native MCP pause state across restart. Destination edits require a
  paused server, a fresh revision and explicit authentication consent. Recover
  interrupted private edits before activating relay bindings; uncertain rollback
  blocks new work until restart.
- Confine skills and project instructions to the selected workspace. Global
  instructions stay in the fixed private Codex home. Writes must be bounded,
  atomic, no-follow, and privately backed up where the implementation promises.
- Reuse the panel's design tokens and accessible interaction patterns. Preserve
  loading, empty, error, retry, keyboard, narrow-screen, and reduced-motion
  states.
- Edit `frontend/src/`, then regenerate the panel and bundled PDF worker below
  `custom_components/codex_bridge/frontend/` with `npm run build`; do not
  hand-edit generated frontend assets.
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
