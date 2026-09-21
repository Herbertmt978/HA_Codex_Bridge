# CHANNEL-03: WhatsApp access

Status: Planned; provider decision required. Version and date: unassigned.

Tracking issue: [#110](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/110).

## Problem and outcome

Offer an optional, explicitly configured WhatsApp route for bounded Codex tasks
and replies, with clear provider requirements and costs.

## Scope

- Select a supported provider/API and document its account, number, template,
  delivery-window and pricing requirements before implementation.
- Authenticate incoming webhooks, validate replay protection and allow only
  paired senders. Keep the Bridge and App private behind HA's approved route.
- Provide private credential storage, disconnect controls and per-sender chat
  isolation. Explain that messages pass through WhatsApp and the chosen provider.

## Acceptance criteria

- Forged signatures, replayed callbacks and unpaired senders cannot start tasks.
- Provider retries, delivery failures, template restrictions and expired windows
  have bounded outcomes without duplicate execution or unexpected sends.
- Attachments have explicit size/content controls and cannot expose arbitrary
  workspace or HA files.
- Qualification uses an approved sandbox/test account; no real messaging charges
  or production registration are incurred without separate authorisation.

## Dependencies and boundary

Depends on HA-01, the channel identity policy and a provider decision. No vendor,
subscription or phone number is selected by this plan. Privileged approvals
remain in HA. Amira's Twilio route is a comparison, not a committed design.
