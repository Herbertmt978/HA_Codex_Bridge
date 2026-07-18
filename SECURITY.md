# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub private vulnerability
reporting](https://github.com/Herbertmt978/HA_Codex_Bridge/security/advisories/new).
If that private form is unavailable, contact the maintainer through the
repository's published GitHub profile and request a private reporting channel.

Do not open a public issue for an unpatched vulnerability or include device
codes, bearer tokens, cookies, ChatGPT credentials, API keys, private workspace
contents, or full authorization headers.

Include a concise impact statement, affected component/version, reproducible
steps using synthetic data, expected and observed behavior, and a suggested
mitigation where safe. We will acknowledge reports when a private channel is
available and coordinate a fix and disclosure timeline with the reporter.

## Security boundaries

The browser talks to Home Assistant, and Home Assistant talks privately to the
Bridge. Do not expose the App or Bridge as a browser endpoint. Use Home
Assistant's supported LAN, VPN, Nabu Casa, Cloudflare, or HTTPS reverse-proxy
access path, terminating that route at Home Assistant rather than proxying it
through to the App or Bridge.

The App uses ChatGPT device login and does not use an OpenAI API key. In App
mode, keep workspaces under `/config/workspaces`; do not mount Home Assistant
configuration, host filesystems, or broad shares. The App fails closed when its
tool-sandbox attestation is unavailable. Do not weaken AppArmor, container
permissions, or network restrictions to bypass it.

## Capabilities and unattended operation

Automations are administrator-created records. Home Assistant owns the clock;
the Bridge stores the prompt and target, enforces revision and idempotency
checks, limits run history, and records skipped overlap/capacity/misfire cases.
Treat a scheduled task as a request to claim work, not as an unconditional
promise that a Codex turn will run. Stop or pause automations before changing a
workspace or restoring a backup.

Skills and plugins are constrained to the selected workspace and the Codex
runtime's reported configuration. `AGENTS.md` writes are limited to the global
Codex home or the selected project root and keep private rollback snapshots.
Review instructions and third-party plugin/marketplace content as untrusted
input before enabling them.

MCP is disabled by default and requires the administrator to enable **Enable
MCP** in the App configuration and restart it. When disabled, the App starts
Codex with an empty MCP override and removes the saved native MCP server table;
cleanup failure keeps readiness unavailable. This does not alter skills,
plugins, marketplaces, or instructions.

When enabled, MCP configuration is deliberately narrow: only outbound
streamable-HTTP servers using HTTPS hostnames are accepted. Literal IPs and
local/internal names are rejected, and available DNS answers are checked for
non-public addresses before a server is saved. DNS checks are best effort, do
not create a connection-time IP allowlist, and cannot prevent a trusted name's
ownership or answers changing later. An administrator must still trust every
configured provider.
Bearer-token configuration is not supported by this surface. OAuth login is
explicit and returns a one-shot authorization URL
with `no-store` handling; do not log, cache, or paste it. MCP elicitation is
declined until a separately reviewed consent flow exists. These controls do
not make the App or Bridge public and do not replace Home Assistant's own
remote-access boundary.

Unattended updates and recovery remain fail-closed. A missing or invalid App
sandbox attestation reports `sandbox_unavailable`; do not broaden mounts or
permissions to make a task continue. Keep a cold backup before App changes,
and do not claim arbitrary Supervisor image rollback until a prior immutable
tag and restore procedure have been tested.

The stable `1.0.0` App/Integration/panel release is `amd64` only and uses
Bridge `0.7.6` with Codex `0.144.5`. Verify its signed release and immutable
image evidence on GitHub before installing.
Its composer Send-state fix and account-neutral local-chat contract are
presentation and continuity improvements, not expansions of authority. Native
Live web search remains provider-gated for Supervisor prompts and automations;
bounded time-sensitive guidance does not relax the blocked model-controlled
shell network. Image generation remains gated by both `imageGeneration` and
`namespaceTools`, uses a signed-in ChatGPT account rather than an API key, and
keeps only bounded private PNG/JPEG/WebP artifacts.

The prior signed and target-HA-accepted `0.8.11` App/Integration/panel release
uses Bridge `0.7.6` and Codex `0.144.5`, exact main commit
`5387a2abcdeac3a5a3c01fe96876634af56542ad`, publication workflow
`29633146637`, and immutable image digest
`sha256:1e69b2db3b223f3e60bc00ce463ae9c5a941d9492c5149ff95eaa1f890deab85`.
Its signature, SBOM, provenance, account-switch behavior, and preserved local
chat history were verified. Target acceptance remains bounded: the first
unattended App update is proven, but external blocked-network routing, cold
restore, arbitrary prior-image selection, and the secure App-owned browser
worker remain unproven. PDF list/archive/preview/download acceptance is also
not claimed. Recover with a cold backup or an existing private external Bridge.

Artifact previews remain on the Home Assistant origin. PDFs are fetched only
through the administrator-authenticated artifact route, checked against an 8 MB
declared and observed size limit, and validated for a leading `%PDF-` signature.
Validated bytes are rendered on a canvas by the bundled local PDF.js renderer;
PDF.js scripting, eval, and XFA support are disabled. The panel does not use an
iframe or native browser PDF embed, and does not embed remote URLs, HTML, SVG,
XML, or an unvalidated PDF. Unsupported content keeps the safe open/download
fallback. This preview is not a Chrome/CDP endpoint and does not grant
model-controlled networking.
See [ADR 0006](docs/aegis/adr/0006-preview-and-browser-boundary.md) for the
separate isolation requirements that must be met before App-owned browser
automation can be enabled.

## Scope notes

The project can investigate vulnerabilities in the Integration, Bridge, App
source, image build inputs, and documented deployment boundary. Report issues
in Home Assistant, Nabu Casa, Cloudflare, ChatGPT, OpenAI, or other upstream
products to their respective security processes as well.
