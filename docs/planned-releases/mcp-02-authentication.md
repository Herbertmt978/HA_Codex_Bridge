# MCP-02: Token and API-key authentication

Status: Implemented for App and Integration 1.3.0. Check the
[release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/1.3.0)
for publication status.

Tracking issue: [#98](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/98).

## Problem and outcome

OAuth-only configuration excludes servers that use bearer tokens or API-key
headers. Add write-only authentication settings for compatible HTTP MCP servers.

## Security-contract decision

The owner authorised implementation on 22 September 2026. The revised
[AGENTS.md](../../AGENTS.md), [CONTEXT.md](../../CONTEXT.md) and
[SECURITY.md](../../SECURITY.md) define the credential boundary before code was
changed. [ADR 0008](../aegis/adr/0008-mcp-credentials.md) records the decision:
extend the existing private relay rather than expose secrets to native Codex
configuration. Secret-bearing requests use bounded HA administrator HTTP views,
not the WebSocket logging path. The App advertises `mcp_credentials_v1`.

## Scope

- Offer OAuth, bearer token and explicitly named authentication headers.
- Store credentials only in private App storage with restricted access. Show
  whether a credential exists, never its saved value. Support replacement and
  removal without asking users to put secrets in a prompt.
- Treat changing the destination as a new disclosure decision: never forward
  a retained secret to a different endpoint automatically.
- Explain token transport and backup exposure, including unencrypted local HTTP.

The implementation includes create, replace and remove controls in
the HA-MCP and generic server forms. Saved values are write-only. Removal keeps
the connection blocked across restart; changing its destination requires a new
connection. Existing OAuth and unauthenticated local connections remain available.
See the [setup guide](../home-assistant-mcp.md#tokens-and-api-keys).

## Acceptance criteria

- Native fixtures verify successful bearer and API-key authentication, rejection
  of an incorrect token, rotation and removal across restart.
- Tokens cannot appear in responses, validation errors, events, logs, chat
  history or browser persistence. Tests exercise malformed secret-bearing input.
  Literal and JSON-escaped upstream reflection is covered; transformed disclosure
  by a malicious server remains outside that guarantee.
- Reserved/routing headers, duplicate names and header injection are rejected.
- OAuth remains available and conflicting authentication choices are rejected.

## Dependencies and boundary

Requires the pinned Codex runtime's supported configuration contract and a
reviewed private credential store. Local destinations depend on MCP-01. This
does not add an OpenAI API-key login path or import existing host credentials.
