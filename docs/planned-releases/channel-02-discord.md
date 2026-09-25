# CHANNEL-02: Discord access

Status: Local candidate, pending native Discord acceptance. Version and date:
unassigned. [Implementation and access guide](../discord-channel.md).

Tracking issue: [#109](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/109).

## Problem and outcome

Offer an optional Discord connection for bounded Codex chats and task results.

## Scope

- Require explicit user, server and channel allow-lists with closed defaults.
- Map conversations deliberately and explain who can see replies in a shared
  channel. Keep direct-message and shared-channel policies separate.
- Store bot credentials privately and provide revoke, status and cancellation
  controls. Define the minimal Discord permissions needed.

## Acceptance criteria

- Unauthorised users, channels, bots and webhook messages cannot start work.
- Shared threads cannot expose another user's private conversation or artifacts.
- Duplicate events, reconnects, provider limits and App restarts do not create
  duplicate runs or uncontrolled message delivery.
- Native tests use a disposable server/bot and exercise permission removal and
  secret-free diagnostics.

## Dependencies and boundary

Depends on HA-01 and the common channel identity policy. Discord identity is
not equivalent to an HA administrator account; privileged approvals stay in
Home Assistant. Do not request broad server permissions for convenience.
