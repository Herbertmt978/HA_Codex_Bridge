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
upstream actions or revoke a token at its provider. MCP-05 adds destination edits
only while paused, with an explicit keep, replace or remove decision. A saved
secret never follows an automatic endpoint change. See the connection management
addendum below.

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

## Connection management addendum — 22 September 2026

The native `enabled` field owns paused state. The relay derives its active
bindings from that field; it does not persist a competing pause flag. Pause and
resume hold the existing configuration lease, excluding active and queued turns.
They update native configuration with its expected version and reload sessions.
Failed reloads restore the previous definition; uncertain recovery closes the
runtime gate and requires an App restart. A conflicting external edit is not
overwritten during recovery.

Destination editing requires pause first and retains the network class. The
administrator enters the new URL and explicitly chooses whether its destination
may receive the saved credential. URLs and credentials never prefill the form.
For a relayed connection the native binding stays unchanged. A private journal
retains the previous registry until the new record commits; startup recovers an
interrupted edit before activating bindings. The journal has the same storage
permissions and backup exposure as the private registry.

Public OAuth URL editing retains the optional client/resource configuration and
native authentication store. Native credentials are bound to the server name
and URL; a changed URL may need sign-in again. Editing is not provider logout.
Opaque form revisions include native configuration state and private mutations,
and expire across restart. No new connection or permission is enabled by upgrade.
