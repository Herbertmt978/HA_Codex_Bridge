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
  `0.7.5` defaults provider-advertised native search to Live for prompts and
  automations, re-negotiates it after device login, and guides time-sensitive
  requests toward the native tool. The intentionally blocked shell-command
  network is separate from provider-side web search. Do not treat a plausible
  answer without web-search activity as a live result.

The target-HA-accepted coordinated baseline is App/Integration `0.7.5` with
Bridge `0.6.3` and Codex `0.144.5`. The latest signed and installed release is
App/Integration/panel `0.8.8` with Bridge `0.7.3` and Codex `0.144.5`.
Candidate `0.8.9` with Bridge `0.7.4` preserves partial output when a long
provider stream fails, reports only a safe failure category, repairs stale or
racing usage-limit state, and fixes compact chat-creation controls. Its signed
publication and bounded target-HA acceptance remain pending. The historical
0.8.0 live exercise passed install/pairing, ChatGPT Pro/history, versions,
GPT-5.6 models, Max/Ultra, five-hour `Off`, native web-search source/stage
history, and subagent stage history, but PDF indexing/archive returned
persistent HTTP 409 after an App restart. That PDF result remains historical;
current target PDF list/archive/preview/download acceptance is still pending.
Issue #43 tracks the secure App-owned browser-worker follow-up; interactive
Chromium remains deferred under ADR 0006. The historical `0.7.1` live list
returned `capabilities_unavailable` (HTTP 503); that is not a current result.
For recovery, use a cold backup or an existing private external Bridge;
external blocked-network routing, cold restore, and App-image rollback remain
unproven.
