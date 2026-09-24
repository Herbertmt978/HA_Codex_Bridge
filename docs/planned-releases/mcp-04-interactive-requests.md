# MCP-04: Interactive MCP requests

Status: Included in the paired 1.6.4 release candidate; target acceptance pending.

Tracking issue: [#100](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/100).

## Problem and outcome

Bridge currently declines all MCP elicitation requests. Some servers need the
user to answer a form or complete authorisation before a tool can finish.
Support the pinned runtime's compatible requests in attended chats.

The implementation uses the versioned `mcp_elicitation_v1` capability and the
existing pending-interaction lifecycle. One-time URLs remain in the live
app-server request and are omitted from persisted runtime state and events.
The 1.6.4 App and Integration advertise this capability together.

## Scope

- Show the requesting server, purpose and supported form fields in the active
  chat, with Accept, Decline and Cancel actions where the protocol allows.
- Display authorisation URLs as deliberate user actions with destination checks;
  never silently navigate, persist one-time URLs or execute server-provided HTML.
- Bind each response to the original server, account, chat, turn and request.

## Acceptance criteria

- Native fixtures verify supported form types and URL requests, schema validation,
  rejection of unsupported fields, timeout and cancellation.
- Late, duplicate or cross-chat answers cannot satisfy another request.
- Disconnect, account change, server removal and App restart end pending requests
  without leaving a turn waiting indefinitely.
- Scheduled/unattended work continues to decline requests and records why it
  could not complete; it must not manufacture answers.

## Dependencies and boundary

Requires a versioned interaction contract and accessible panel controls.
Server text is untrusted, and an MCP question cannot grant additional Bridge or
host permissions. Sensitive credentials should use the dedicated authentication
flow, not a general-purpose form.
