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

The current candidate (Integration/App `0.7.5`, Bridge `0.6.3`, Codex
`0.144.5`) is experimental and `amd64` only and is pending real Home Assistant
acceptance. Native Live web search is provider-gated for Supervisor prompts and
automations; bounded time-sensitive guidance does not relax the blocked
model-controlled shell network.
Image generation is gated by both `imageGeneration` and `namespaceTools`, uses
a signed-in ChatGPT account rather than an API key, and keeps only bounded
private PNG/JPEG/WebP artifacts. The compact composer is a presentation change,
not an expansion of authority. Target-HA-accepted coordinated release `0.7.3`
remains the live baseline; signed App `0.7.4` updates the verified runtime. The
published/signed `0.7.0` baseline has
generic image digest
`sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`
with amd64 child `sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`;
main CI `29471288344` and publication `29471288457` succeeded, and signature,
SBOM, and provenance attestations are published with the [release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0).
Target-Home-Assistant acceptance remains bounded. The target-HA-accepted
`0.7.1` retest confirmed management-form retention, skill mutations, and MCP
form cancellation; its live plugin/marketplace list returned
`capabilities_unavailable` (HTTP 503), so no `0.7.1` plugin or marketplace
list/mutation acceptance was claimed. The `0.7.5` changes above remain candidate
evidence pending live acceptance. The first
unattended App update is proven; external blocked-network routing, cold restore,
and previous-image rollback remain unproven. Arbitrary prior-image selection is not a validated
Supervisor rollback mechanism; recover with a cold backup or an existing private
external Bridge.

## Scope notes

The project can investigate vulnerabilities in the Integration, Bridge, App
source, image build inputs, and documented deployment boundary. Report issues
in Home Assistant, Nabu Casa, Cloudflare, ChatGPT, OpenAI, or other upstream
products to their respective security processes as well.
