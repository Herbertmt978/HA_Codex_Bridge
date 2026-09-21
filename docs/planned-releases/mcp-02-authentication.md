# MCP-02: Token and API-key authentication

Status: Planned. Version and date: unassigned.

Tracking issue: [#98](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/98).

## Problem and outcome

OAuth-only configuration excludes servers that use bearer tokens or API-key
headers. Add write-only authentication settings for compatible HTTP MCP servers.

## Scope

- Offer OAuth, bearer token and explicitly named authentication headers.
- Store credentials only in private App storage with restricted access. Show
  whether a credential exists, never its saved value. Support replacement and
  removal without asking users to put secrets in a prompt.
- Treat changing the destination as a new disclosure decision: never forward
  a retained secret to a different endpoint automatically.
- Explain token transport and backup exposure, including unencrypted local HTTP.

## Acceptance criteria

- Native fixtures verify successful bearer and API-key authentication, rejection
  of an incorrect token, rotation and removal across restart.
- Tokens cannot appear in responses, validation errors, events, logs, chat
  history or browser persistence. Tests exercise malformed secret-bearing input.
- Reserved/routing headers, duplicate names and header injection are rejected.
- OAuth remains available and conflicting authentication choices are rejected.

## Dependencies and boundary

Requires the pinned Codex runtime's supported configuration contract and a
reviewed private credential store. Local destinations depend on MCP-01. This
does not add an OpenAI API-key login path or import existing host credentials.
