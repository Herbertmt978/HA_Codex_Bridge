# MCP-06: Tool permissions

Status: Planned. Version and date: unassigned.

Tracking issue: [#102](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/102).

## Problem and outcome

Connecting a server should not require accepting every tool it advertises.
Let administrators inspect tools and select which Codex may use.

## Scope

- List tool names and descriptions with the server identity and available
  read/write/destructive annotations. Explain that annotations are claims made
  by the server, not independently verified safety guarantees.
- Persist an explicit allow-list or supported deny-list through the native
  runtime. Define the default for newly discovered tools.
- Show permission changes clearly and apply them to the appropriate chat/turn
  lifecycle without leaving stale callable tools.

## Acceptance criteria

- Actual native tool calls prove allowed tools work and blocked tools cannot run.
- Restart, server upgrades, renamed tools and newly added tools preserve the
  selected policy; stale catalogues are visibly marked.
- Tests cover malicious descriptions, large catalogues, duplicate names and
  concurrent changes during work.
- Scheduled tasks obey the same tool policy and cannot broaden it.

## Dependencies and boundary

Build on MCP-05 and verify tool filtering in the pinned runtime. Server access
and host access remain separate. A tool named read-only is not automatically
treated as safe, and filtering does not sandbox the remote server itself.
