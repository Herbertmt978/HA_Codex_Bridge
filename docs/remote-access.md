# Remote access

## The invariant

Remote traffic terminates at Home Assistant. The browser stays on the same Home
Assistant origin for the panel, HTTP requests, and WebSocket connection:

```text
Browser -> HTTPS proxy or Home Assistant remote service -> Home Assistant
        -> private Integration-to-App connection -> Codex / OpenAI
```

Do not publish the App, private Bridge, Supervisor endpoint, or a separate
Codex endpoint to a LAN or WAN.

MCP is a separate outbound integration, not a remote-access shortcut. It is
disabled by default and must be explicitly enabled in the App configuration
before restart. Configure only a trusted HTTPS MCP server from the administrator
panel. Literal IPs, localhost/internal names, known non-public DNS answers, and
bearer-token settings are rejected. DNS checks are best effort rather than a
connection-time IP allowlist. OAuth login opens the provider's authorization
flow from an explicit action; do not expose,
bookmark, log, or cache the one-shot authorization URL. The App/Bridge still
has no public listener.

## ChatGPT device authentication

After the ChatGPT session is established, normal Codex Bridge panel use can
remain on the Home Assistant origin. Initial sign-in and re-authentication are
different: the browser must be able to open the approved ChatGPT device-auth
page. Test that access from the actual user network before depending on remote
operation.

Use **Sign in with ChatGPT** to begin the flow. **Cancel** only ends a pending
flow; **Sign out** removes the connected ChatGPT session.

## Before enabling remote use

- Secure Home Assistant according to its own remote-access guidance.
- Confirm the external address opens Home Assistant normally before opening the
  Codex Bridge panel.
- Preserve HTTPS, the public host header, WebSocket upgrades, and timeouts
  suitable for an interactive Home Assistant session.
- Do not cache authenticated panel/API responses or rewrite the panel to a
  different origin.
- Keep proxy limits compatible with the Integration's bounded chunked-file
  protocol. Do not replace it with a direct App upload endpoint.

### Provider notes

Use the normal Home Assistant remote URL for Nabu Casa, Cloudflare, a VPN, or a
LAN-only setup. No App URL, port forwarding, or Codex-specific direct browser
endpoint is required. For reverse proxies, preserve Home Assistant's supported
HTTPS and WebSocket behavior exactly as required by Home Assistant.

## Test and recover

Remote-access acceptance remains release work. The synthetic proxy contract and
offline redacted-evidence validator are documented in the
[remote-path acceptance runbook](acceptance/remote-access.md); they do not
replace an authorized run from the actual external network. Before relying on a
route, test it with a harmless workspace:

1. Sign in to Home Assistant through the intended route.
2. Complete a ChatGPT sign-in, cancel a separate pending sign-in, and test
   re-authentication from the actual user network.
3. Create a small chat, exercise an approval, and confirm the connection
   resumes after a brief browser/proxy interruption without submitting a second
   turn.
4. Test a small import/download through the panel, never a direct App URL.
5. If using MCP, first enable **Enable MCP** in the App configuration and
   restart it. Add a harmless trusted HTTPS server and complete its explicit
   OAuth flow through the panel; verify provider tools appear without changing
   the browser origin. Do not test with a private-IP endpoint.

For release acceptance, also exercise the full v1 8 MiB resumable chunk,
response-lost idempotent retry, cancellation, artifact `Range`/`If-Range`
resume, and unsatisfied `416` path. Record only the redacted categorical
summary accepted by the offline collector; do not export raw browser traffic.

If the panel loses connection, reload the Home Assistant page and use normal HA
diagnostics. Do not expose the Bridge or create an alternate browser-to-Codex
path as a workaround.
