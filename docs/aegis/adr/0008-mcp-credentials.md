# ADR 0008: MCP credentials belong to the private relay

Status: Accepted for owner-authorised MCP-02 implementation, 22 September 2026.

## Decision

Extend the existing relay to own local connections and public HTTPS connections
with static authentication. Keep its private registry location so existing local
connections migrate in place. Read version 1, write version 2; do not create a
second credential store. The registry is private, bounded, atomic and no-follow.
App backups include its plaintext credentials; the UI and guide disclose this.

Native Codex receives only a generated relay capability. Bearer or API-key
credentials are injected after connection-time destination validation, with TLS
verification and no redirects, cookies or environment proxies. Public static
authentication uses global IPs only, pinned to the addresses approved at creation.
Local connections retain their separate enablement and endpoint acknowledgement.

One explicit auth mode is selected: existing native OAuth/no static credential,
bearer token, or one to eight named authentication headers. Reject routing,
protocol, cookie, forwarding and reserved headers, case-insensitive duplicates,
control characters and oversized input. Keep actual values out of representations.

Use HA administrator HTTP views for secret-bearing create/replace requests;
WebSocket debug logging can record request payloads. No read credential endpoint
exists. Fixed errors, no-store responses, masked UI controls and immediate draft
clearing avoid accidental reflection and persistence. Literal and JSON-escaped
upstream echoes are redacted across streaming chunk boundaries.

Replacement revokes active requests, atomically changes the saved credential and
reloads native MCP sessions. Removal clears the credential but retains a disabled
connection that can receive a replacement. Cancellation cannot undo accepted
upstream actions or revoke a token at its provider. No destination-edit endpoint exists,
so a saved secret can never follow an automatic endpoint change.

## Alternatives and limits

Writing native http_headers would expose upstream secrets to config/read and
runtime diagnostics. Environment variables would expand process exposure and
complicate rotation. Separate at-rest encryption with its key in the same App
backup would not protect a compromised App or backup, so it is not claimed.

The App and explicitly enabled Host Access remain trusted. An upstream server
receives its own credential; transformed or malicious disclosure cannot be
prevented by output redaction. OAuth, stdio and interactive question expansion
remain separate work. No production credential or installation changes are
required to implement or qualify this enhancement.

## Verification obligations

Use synthetic secrets for bearer/header success and failure, rotation/removal,
restart, malformed input, duplicate/header injection, mixed/rebinding DNS,
redirects, reflected response chunks, private storage permissions and native
configuration inspection. Exercise HA administrator/older-App rejection and
browser clearing/no-persistence behaviour. Native fixtures must use the same
pinned Codex runtime as the App.
