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
configuration, host filesystems, or broad shares into the normal Bridge App. The App fails closed when its
tool-sandbox attestation is unavailable. Do not weaken AppArmor, container
permissions, or network restrictions to bypass it.

The optional **Codex Host Access** App is a deliberate exception with a separate
installation and explicit consent. It shares the HAOS PID namespace and, with
Protection mode disabled, enters the host environment as root. It can access
host files, mounted storage, credentials, containers, services and the host's
network. The ordinary Bridge App keeps its existing permissions.

Installing or privately pairing the companion does not enable it. A Home
Assistant administrator must acknowledge the current machine-specific warning,
then select host access for each chat or schedule. Scheduled work needs its own
unattended acknowledgement. A changed environment or revoked grant blocks new
work; the Bridge never silently substitutes another environment.

Root access is not a containment boundary. Commands can change access controls,
read Codex and integration credentials, or start work outside the tracked
process group. Cancellation and revocation prevent new Bridge commands and
attempt to stop tracked work; they cannot undo changes or guarantee termination
of independently started work. See the complete
[disclosure and installation guide](codex_host_access_app/DOCS.md).

## Command activity

Administrator chat activity can include bounded command previews. The Bridge
omits commands with recognised credential patterns before saving activity, but
this filter cannot identify every secret. Unrecognised credentials in command
arguments may remain in private chat history and its backups. Avoid placing
credentials in command arguments. Output, environment values and image paths
are not added to these events. See [chat activity](docs/chat-activity.md) for
display limits and compatibility behaviour.

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
Codex with explicit disabled overrides for saved servers. Before accepting
requests it checks that the effective configuration contains no enabled server,
then removes the saved native MCP server table; cleanup failure keeps readiness
unavailable. An empty table alone is insufficient because Codex merges tables. This does not alter skills,
plugins, marketplaces, or instructions.

Public MCP configuration without static credentials is deliberately narrow: only outbound
streamable-HTTP servers using HTTPS hostnames are accepted. Literal IPs and
local/internal names are rejected, and available DNS answers are checked for
non-public addresses before a server is saved. DNS checks are best effort, do
not create a connection-time IP allowlist, and cannot prevent a trusted name's
ownership or answers changing later. An administrator must still trust every
configured provider.
The local MCP option is a deliberate exception to the public-destination rule.
It is disabled by default and requires separate acknowledgement for each LAN or
HA App endpoint. Local connections use an authenticated loopback relay, never a
direct native upstream URL. A private registry records the selected URL and
approved RFC1918/ULA addresses. Every request resolves and checks the complete
answer set against those addresses, then dials an approved IP directly with the
original HTTP Host and verified TLS hostname. Redirects, environment proxies,
loopback/link-local/metadata destinations and Supervisor access are rejected.
A DNS address change requires removing and re-adding the connection. HTTP sends
MCP data and any URL-path secret without encryption; the UI warns before consent.
Only the fixed saved endpoint receives MCP protocol headers and bodies. The
relay's generated capability header never reaches the upstream server. Request,
stream and concurrency limits bound resource use. Disabling local access removes
its native bindings and prevents their restoration without a new connection.
Local OAuth is not supported in this release. Existing public HTTPS/OAuth
behaviour is unchanged, including its best-effort DNS limitation.

MCP-02 permits administrator-supplied bearer tokens and explicitly named
authentication headers. The private relay stores them in the App's restricted
private registry and injects them only for the saved destination. Credentials
are not encrypted separately from App storage: protect App backups, which
include them, and revoke old credentials at their provider after restoring an
old backup. They are never written to Codex configuration or workspace files.
Credential requests use bounded, administrator-authenticated HTTP routes with
fixed errors and no-store responses, avoiding WebSocket debug-message logging.
The UI never reads saved values and clears submitted secrets even on failure.
Public credential endpoints require HTTPS and connection-time public IP pinning;
local HTTP also requires the existing unencrypted-transport acknowledgement.
Removal blocks new upstream requests and cancels active relay requests without
falling back to unauthenticated access. It cannot undo accepted server actions
or revoke credentials at their provider. Destination changes require removing
and recreating the connection.
Routing/protocol headers, cookies, duplicate headers and control characters are
rejected. The relay redacts literal and JSON-escaped reflected credentials across
response chunks. A trusted server receives its credential and can misuse or
transform it; redaction cannot make an untrusted provider safe.

### Proposed isolated stdio MCP boundary

[ADR 0009](docs/aegis/adr/0009-isolated-stdio-mcp.md) proposes a new,
separately reviewed execution boundary for MCP-03. It is **not implemented or
enabled in the released App**. Existing public HTTPS/OAuth and acknowledged
LAN/App relay rules remain in force. Enabling MCP or installing an App update
must not silently create or activate a stdio worker.

The first proposed stdio release supports approved Python 3.14 packages on
amd64 only. Packages and dependencies are pinned in a root-owned catalogue
within the signed immutable App image, with source, exact version, file digests
and fixed entrypoint recorded in a manifest. Activation re-verifies the
package bytes. The administrator must see the source, fixed command, grants
and selected tools before approval. A package update stages a new paused
revision, verifies it, waits for active work, then switches atomically with a
packaged previous revision available for rollback. Runtime package downloads,
user-uploaded executables, native Codex `command` entries and arbitrary
environment variables are not permitted by this proposal.

Codex would connect only to a generated, authenticated loopback HTTP endpoint
owned by the trusted Bridge. The Bridge would own each stdio session and pass
only bounded MCP JSON-RPC lines through private pipes. Its capability header,
HTTP authentication, Supervisor token and Codex sign-in state must never reach
the worker. A worker needs its own AppArmor child, Bubblewrap namespaces,
locked seccomp filter, no usable capabilities, clean allowlisted environment
and independent root startup proof. It sees only immutable package files and
bounded disposable scratch space. The first release grants no workspace,
Home Assistant, App-private or host files and no network access. File access
for a particular chat requires a proven request-to-chat ownership boundary;
the current global MCP binding does not provide that evidence.

New stdio connections start paused with no allowed tools. The administrator
chooses tools after discovery; descriptions and safety annotations supplied
by the package are untrusted. Configuration leases must keep pause, updates
and tool policy consistent across chats and scheduled tasks. Admission requires
a verified isolation proof and a resource budget coordinated with the
optional browser worker. Time, process, memory, file-descriptor, stdout,
stderr and message limits apply. Explicit cancellation, crashes, timeouts,
restarts, pause, removal and failed updates must revoke sessions and clean
up descendants and temporary state. Uncertain recovery blocks new work and
shows a bounded recovery action; it must never fall back to running as the
Bridge user. Native HAOS tests must prove these claims before publication.

OAuth login is
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

The App is published as a signed, immutable image. Current component versions
and user-facing changes are recorded in the [release notes](codex_bridge_app/CHANGELOG.md).
A passing startup sandbox check does not establish browser-worker isolation,
external proxy behaviour, or a complete restore. Keep those acceptance checks
separate. Native web search and image generation remain provider-gated and do
not relax model-controlled shell networking.

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
separate isolation requirements for App-owned browser automation. The browser
is disabled by default and needs its own root startup proof after explicit
enablement. Its public-network policy, process boundary, quotas and failure
states are documented in the [browser threat model](docs/acceptance/browser-worker.md).

## Scope notes

The project can investigate vulnerabilities in the Integration, Bridge, App
source, image build inputs, and documented deployment boundary. Report issues
in Home Assistant, Nabu Casa, Cloudflare, ChatGPT, OpenAI, or other upstream
products to their respective security processes as well.
