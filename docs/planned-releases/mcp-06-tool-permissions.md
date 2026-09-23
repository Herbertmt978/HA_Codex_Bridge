# MCP-06: Tool permissions

Status: Implemented in source; paired release and Home Assistant acceptance pending.
Version and date: unassigned.

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

The implementation keeps existing connections unrestricted until an administrator
saves an explicit selection. New connections created by the paired UI start with
an empty allow-list; older Integration versions retain their existing add flow.
Once selected, Codex's native `enabled_tools`
allow-list blocks new and renamed tools by default. The status API hides blocked
tools, so the Bridge briefly reads the full catalogue under an exclusive
no-active-turn lease and restores the saved policy before accepting work. A
durable marker pauses all MCP connections during startup if that read is ever
interrupted. Paused or unavailable servers cannot have their selection edited
until discovery succeeds. The pinned Codex 0.156.1 runtime was probed with a
synthetic streamable-HTTP server: an allowed tool call completed, a blocked call
was rejected before reaching the server, and an empty allow-list blocked both
tools. Paired App/Integration release and Home Assistant acceptance remain.
