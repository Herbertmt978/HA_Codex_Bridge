# MCP-03: Isolated stdio servers

Status: Planned; architecture required. Version and date: unassigned.

Tracking issue: [#99](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/99).

## Problem and outcome

Some MCP servers are local programs rather than HTTP services. Provide a
managed way to run approved stdio servers without giving them the Bridge's
private credentials or unrestricted host access.

## Required security-contract review

The current MCP contract permits trusted remote HTTPS servers, not executable
stdio servers. Before implementation, approve the worker isolation and transport
design and deliberately revise [AGENTS.md](../../AGENTS.md),
[CONTEXT.md](../../CONTEXT.md) and [SECURITY.md](../../SECURITY.md) to define the new
boundary. Package verification and process controls do not by themselves permit
an exception. This plan remains conditional on that review; the existing
HTTPS-only runtime contract is unchanged.

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

Requires an approved isolation design, resource budget and package distribution
process. Integrates with MCP-05 and MCP-06. An arbitrary command text box running
as the Bridge user is not an acceptable implementation. No production worker is
enabled merely by installing a release.
