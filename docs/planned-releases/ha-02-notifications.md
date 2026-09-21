# HA-02: Completion notifications

Status: Planned. Version and date: unassigned.

Tracking issue: [#104](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/104).

## Problem and outcome

Scheduled results currently remain in chat and run history. Let users choose
whether completion, failure or a request for attention should notify them.

## Scope

- Offer Off, attention/failures only, and all completed runs, with clearly
  described destinations through Home Assistant's notification facilities.
- Support a private HA persistent notification and explicitly selected mobile
  notification targets; do not select every device by default.
- Include a sign-in-protected link to the relevant chat. Make content previews
  optional because lock screens and notification histories may reveal them.

## Acceptance criteria

- A scheduled run produces the chosen notification exactly once across retry,
  restart, reconnect and duplicate event delivery.
- Muted tasks remain quiet. Cancellation, skipped runs and expired sign-in have
  deliberate, documented notification semantics.
- Unavailable notification services do not turn a successful task into a failed
  task or trigger an uncontrolled retry loop.
- Native HA testing verifies selected mobile/persistent destinations using
  disposable recipients; existing household notifications are unaffected.

## Dependencies and boundary

Depends on HA-01 or an equivalent durable result-delivery contract. Separate
task execution from notification delivery and retain run history when delivery
fails. Do not embed raw tool results or secrets in notification payloads.
