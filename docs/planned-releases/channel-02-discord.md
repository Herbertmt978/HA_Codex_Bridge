# CHANNEL-02: Discord access

Status: Local candidate with disposable native Discord acceptance on 25
September 2026; pending review and release. Version unassigned.
[Implementation and access guide](../discord-channel.md).

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

The disposable test passed closed-default refusal, allowed DM and shared
commands, private status and cancellation, reconnect/restart, no duplicate
delivery after restart, and a fixed diagnostic after bot send permission was
removed. Provider rate-limit responses were simulated in focused local tests;
a live 429 was not induced.

## Dependencies and boundary

Depends on HA-01 and the common channel identity policy. Discord identity is
not equivalent to an HA administrator account; privileged approvals stay in
Home Assistant. Do not request broad server permissions for convenience.
