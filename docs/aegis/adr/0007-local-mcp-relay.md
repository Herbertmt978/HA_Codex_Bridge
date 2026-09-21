# ADR-0007 - Opt-in local MCP through a confined relay

Status: Accepted and implemented; release verification in progress.
Date: `2026-09-21`

## Source Evidence

- Owner-authorised implementation and release of MCP-01
## Context

Native local URLs cannot enforce per-connection destination and redirect confinement.

## Decision

Use a private authenticated loopback relay with approved private addresses, connection-time IP pinning, verified original TLS hostname, exact saved destination, no redirects and no environment proxies. Keep public HTTPS/OAuth unchanged.

## Alternatives Considered

- Direct native local URLs lack connection-time enforcement.
- A global Codex proxy mixes provider and MCP permissions.
## Consequences

- Local HTTP is unencrypted. DNS address changes require re-addition. A generated private relay header is not a user-configurable authentication feature. Local OAuth and bearer settings remain unsupported.
## Compatibility Boundary

Default-disabled App option plus per-endpoint consent; no shell, browser or host permission changes.

## Retirement Impact

No transport retired. Disabling local access removes relay bindings.

## Baseline Sync

- Needed: needed
- Target: AGENTS.md, CONTEXT.md, SECURITY.md, docs/home-assistant-mcp.md
- Action: update baseline
- Reason: The explicit local relay exception revises the previous public-HTTPS-only contract.

## Evidence References

- docs/planned-releases/mcp-01-local-connections.md
- Policy and real-socket relay regression tests cover private addresses, DNS
  changes, redirect refusal, HTTP/SSE, TLS trust and hostname verification,
  private storage, revocation, bounded bodies and incomplete-stream termination.
- Native Codex expands defaults in its effective MCP configuration. Read-time
  recognition accepts only the observed local defaults; persisted bindings
  still require exactly the generated URL and capability header.
- Independent consultation suggestions to permit loopback, disable HTTPS,
  forward OAuth challenges or relax registry writes were rejected because they
  contradict this boundary. Uvicorn's signal hook was checked against the pinned
  implementation. MCP session-expiry status handling and streaming termination
  received explicit regression coverage.
- Native restart testing exposed recursive config merging: an empty CLI table
  does not mask saved MCP entries. Bootstrap now supplies disabled placeholder
  transports for bounded private user entries, verifies the effective result
  before accepting application requests, and retains strict rejection of MCP
  from other layers. Validated connections activate in a new generation.

## Boundary

This ADR is an advisory Aegis Method Pack record. It does not grant completion authority or replace project-authoritative architecture sources.
