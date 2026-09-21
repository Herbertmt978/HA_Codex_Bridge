# MCP-01: Local MCP connections

Status: Implemented for 1.2.0. See the [release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases) for publication status.

Tracking issue: [#97](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/97).

## Problem and outcome

The current Bridge rejects local names, private IP addresses and HTTP MCP URLs.
Users cannot connect directly to HA-MCP on the same installation. Add an
explicit local-network option while retaining public HTTPS as the default.

## Required security-contract review

The owner authorised implementation after the roadmap review. The chosen
destination policy uses a private relay, approved private IP addresses,
connection-time checks, normal TLS verification and no redirects. The explicit
local exception is recorded in [AGENTS.md](../../AGENTS.md),
[CONTEXT.md](../../CONTEXT.md), [SECURITY.md](../../SECURITY.md) and
[ADR-0007](../aegis/adr/0007-local-mcp-relay.md). Endpoint acknowledgement does
not replace those controls.

## Scope

- Support trusted LAN and HA App-network HTTP/HTTPS endpoints through the
  existing private App. Explain that localhost refers to the App itself.
- Explain which server will receive requests and that HTTP is unencrypted.
  Require administrator acknowledgement before adding local access.
- Keep metadata services, unexpected loopback targets and unrelated internal
  services blocked. Define redirect, proxy and DNS-rebinding behaviour.
- Extend the guided HA-MCP setup and retain the custom-server flow.

## Acceptance criteria

- A native HAOS test connects to a disposable local MCP server, lists its tools
  and completes a tool call without a public tunnel.
- Tests cover IPv4/IPv6, App DNS names, mixed DNS answers, redirects, reserved
  destinations and an unavailable server.
- Disabling local access prevents subsequent local connections; restart and
  saved-config migration preserve the declared policy.
- Existing HTTPS/OAuth servers and older App connections still work.

## Dependencies and boundary

Requires a reviewed destination policy and capability negotiation. Combine with
MCP-02 when credentials are needed. This does not grant shell networking, root
access, browser LAN access or automatic HA-MCP installation.
