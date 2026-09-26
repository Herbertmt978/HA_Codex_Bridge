# MCP-03: Isolated stdio servers

Status: Released in App and Integration 1.8.0, with readiness and worker
startup corrections in 1.8.1 and 1.8.2. Native managed acceptance remains a
release check.

Tracking issue: [#99](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/99).

## Problem and outcome

Some MCP servers are local programs rather than HTTP services. Provide a
managed way to run approved stdio servers without giving them the Bridge's
private credentials or unrestricted host access.

## Security contract

The isolated worker and private adapter have their own reviewed boundary in
[ADR 0009](../aegis/adr/0009-isolated-stdio-mcp.md), [AGENTS.md](../../AGENTS.md),
[CONTEXT.md](../../CONTEXT.md) and [SECURITY.md](../../SECURITY.md). The existing
public HTTPS and private LAN relay contracts remain in force. A package digest
alone does not grant execution or tool access.

## Scope

- Define a separate worker boundary, installation model and supported runtimes.
  Pin and verify packages; show the command, source and access before approval.
- Give each server only its declared files, environment and network permissions.
  Do not inherit Supervisor tokens, Codex login state or arbitrary host variables.
- Provide start, stop, status, bounded diagnostics and package-update controls.

## Acceptance criteria

- A real stdio fixture completes discovery and tool calls in native HAOS.
- Attempts to read private credentials, sibling workspaces or undeclared paths
  fail. Network permissions match the reviewed server policy.
- Cancellation, crashes, restarts and removal clean up descendants and resources.
- Resource limits and failed package verification prevent activation, with clear
  UI recovery instructions.

## Dependencies and boundary

The first release supports a fixed, verified Python 3.14 package on amd64 HAOS:
Bridge Time 1.0.0. It has no network or workspace access and is disabled unless
both MCP App options are enabled. Its connection begins paused with no allowed
tools. MCP-05 and MCP-06 own pause and tool policy. No worker is enabled merely
by installing the release; arbitrary commands are not accepted.
