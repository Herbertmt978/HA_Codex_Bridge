# MCP-05: Connection management

Status: Implemented for 1.4.0.

Tracking issue: [#101](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/101).

## Problem and outcome

The current UI can add, list, sign in to and remove servers. Users need to edit,
pause and resume connections and understand failed startup without recreating
their configuration.

## Scope

- Edit supported connection fields and pause/resume individual servers.
- Retain paused configuration privately. State explicitly whether changes apply
  to existing chats, new turns or only new chats.
- Show bounded connection/authentication status, tool/resource counts and useful
  recovery actions without displaying private endpoint paths or provider errors.

## Acceptance criteria

- Native tests verify add/edit/pause/resume/remove and behaviour after restart.
- Paused servers do not make calls, including from retained chats.
- Concurrent edits, active runs and reload failures produce recoverable outcomes;
  failed saves cannot claim success or discard the last usable configuration.
- Secrets never prefill the form; changing endpoints cannot reuse credentials
  without an explicit decision. Older Apps show update guidance.

## Dependencies and boundary

Requires runtime configuration leases and fresh capability negotiation. Uses
MCP-02 for secret replacement and MCP-01 for local destinations. Inspired by
connection lifecycle controls documented by Amira; implementation will be our
own. This is not permission to install or activate servers automatically.
