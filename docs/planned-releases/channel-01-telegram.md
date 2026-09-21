# CHANNEL-01: Telegram access

Status: Planned. Version and date: unassigned.

Tracking issue: [#108](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/108).

## Problem and outcome

Offer optional chat and task-result access through a Telegram bot for users who
prefer messaging to the HA panel.

## Scope

- Pair explicitly allowed Telegram users with a bounded Bridge project/chat.
  Prefer outbound polling when feasible to avoid a new public HA endpoint.
- Store the bot token privately; provide revoke/disconnect controls and clear
  disclosure that prompts, replies and permitted attachments pass through Telegram.
- Preserve per-user conversation isolation and support cancellation/status.

## Acceptance criteria

- Messages from unpaired users, groups and forwarded contexts cannot start work.
- Retries, reconnects and duplicate updates do not duplicate tasks or replies.
- Account/channel revocation, rate limits, provider outages and attachment limits
  have bounded, tested outcomes.
- A disposable bot/account passes native end-to-end qualification. No production
  bot or household recipient is used merely to test the release.

## Dependencies and boundary

Depends on HA-01, a channel identity policy and private credential handling.
Remote messages cannot grant host access or answer administrator approvals.
Provider setup and any costs remain the user's explicit choice. Amira is a
feature reference only; do not copy its noncommercial implementation.
