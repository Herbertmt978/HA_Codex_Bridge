# Support

## Where to ask

Use GitHub issues for reproducible bugs, feature requests, and setup questions.
Read the [README](README.md), [App documentation](codex_bridge_app/DOCS.md), and
[CONTRIBUTING.md](CONTRIBUTING.md) first. For suspected vulnerabilities, follow
[SECURITY.md](SECURITY.md) instead of creating a public issue.

## Include this information

- Home Assistant version and installation type.
- Integration/Bridge version and App version, if installed.
- Processor architecture (`amd64` is the current App target).
- Whether the report concerns the experimental App or an optional external
  Bridge.
- A minimal reproduction and redacted App/Integration diagnostics.

Never include device codes, bearer tokens, cookies, credentials, full private
workspace paths, or workspace secrets.

## Fast checks

- Confirm the browser reaches the Home Assistant URL, not a direct Bridge or App
  address.
- If readiness reports `sandbox_unavailable`, do not loosen the sandbox; collect
  redacted logs and report the failure.
- Confirm Home Assistant and ChatGPT sessions separately. Use **Sign in with
  ChatGPT** for initial sign-in or re-authentication, **Cancel** only for a
  pending sign-in, and **Sign out** to remove an established session.
- Ensure the user network can reach the approved ChatGPT device-auth page for
  initial sign-in and re-authentication. Normal panel use remains on Home
  Assistant after connection.
- For a missing model or reasoning level, check the panel catalogue status.
  Runtime discovery may show marked recovery data rather than a current list.
- For current facts such as live weather, check the run activity for
  **Searching the web** or **Opening a web page**. On a Supervisor connection,
  the `0.7.3` candidate defaults provider-advertised native search to Live for
  prompts and automations and re-negotiates it after device login without an
  Integration reload. The intentionally blocked shell-command network is
  separate from provider-side web search. Do not treat a plausible answer
  without web-search activity as a live result.

The target-HA-accepted App `0.7.1` image and its first unattended update are
historical evidence. Published `0.7.2` was signed but not target-HA accepted.
The current `0.7.3` App/Integration candidate
(Bridge `0.6.2`, Codex `0.144.4`) is pending real Home Assistant acceptance.
It adds provider-gated Live web search and signed-in image generation gated by
both `imageGeneration` and `namespaceTools`; no API key is used, and generated
PNG/JPEG/WebP artifacts remain private and bounded. The compact panel and
updater `jsonschema` dependency-installation fix are candidate changes. The
historical `0.7.1` live list returned `capabilities_unavailable` (HTTP 503),
not a current `0.7.3` result.
For recovery, use a cold backup or an existing private external Bridge;
external blocked-network routing, cold restore, and App-image rollback remain
unproven.
