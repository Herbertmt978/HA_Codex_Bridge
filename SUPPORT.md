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
- Whether the report concerns the stable, `amd64`-only Supervisor App or an
  optional external Bridge.
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
  `0.7.5` defaults provider-advertised native search to Live for prompts and
  automations, re-negotiates it after device login, and guides time-sensitive
  requests toward the native tool. The intentionally blocked shell-command
  network is separate from provider-side web search. Do not treat a plausible
  answer without web-search activity as a live result.

The stable `1.0.0` App/Integration/panel release retains the
`amd64`-only Supervisor scope, Bridge `0.7.6`, and Codex `0.144.5`. It fixes a
panel-only composer defect: **Send** now enables or disables immediately as a
prompt is typed, without needing a refresh. The local chat contract is account
neutral: chats, projects, transcripts, files, workspace settings, archive
state, and automation targets remain available across a ChatGPT account
change; only stale private provider-thread continuity is detached. Verify the
signed `1.0.0` release and immutable-image evidence on GitHub before installing.

The prior signed and target-HA-accepted `0.8.11` App/Integration/panel release
uses Bridge `0.7.6` and Codex `0.144.5`, exact main commit
`5387a2abcdeac3a5a3c01fe96876634af56542ad`, publication workflow
`29633146637`, and immutable image digest
`sha256:1e69b2db3b223f3e60bc00ce463ae9c5a941d9492c5149ff95eaa1f890deab85`.
Its signature, SBOM, provenance, account-switch behavior, and preserved local
chat history were verified. Target acceptance remains deliberately bounded:
PDF list/archive/preview/download, external Nabu Casa/Cloudflare routing, cold
restore, arbitrary App-image rollback, and the secure App-owned browser worker
are not yet accepted. Issue #43 tracks the browser-worker follow-up; interactive
Chromium remains deferred under ADR 0006. The historical `0.7.1` live list
returned `capabilities_unavailable` (HTTP 503); that is not a current result.
For recovery, use a cold backup or an existing private external Bridge.
