# HA-02: Completion notifications

Status: In development. Version and date: unassigned.

Tracking issue: [#104](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/104).

## Problem and outcome

Scheduled results currently remain in chat and run history. Let users choose
whether completion, failure or a request for attention should notify them.

## Scope

- Offer Off, attention/failures only, and all completed runs, with clearly
  described destinations through Home Assistant's notification facilities.
- Support a generic HA persistent notification and explicitly selected mobile
  notification targets; do not select every device by default. Home Assistant
  persistent notifications are visible to all HA users, so they cannot carry
  a task title, result excerpt or other private detail.
- Include a sign-in-protected link to the relevant chat. Make content previews
  optional because lock screens and notification histories may reveal them.

## Acceptance criteria

- A scheduled run attempts each selected destination at most once across retry,
  restart, reconnect and duplicate history delivery. A mobile service has no
  transactional acknowledgement: a Home Assistant crash after the receipt is
  saved but before delivery may lose that alert rather than sending it twice.
- Muted tasks remain quiet. Cancellation, skipped runs and expired sign-in have
  deliberate, documented notification semantics.
- Unavailable notification services do not turn a successful task into a failed
  task or trigger an uncontrolled retry loop.
- Native HA testing verifies selected mobile/persistent destinations using
  disposable recipients; existing household notifications are unaffected.

## Delivery policy

The default is **Off**. **Needs attention or failed** covers failed, blocked,
interrupted and skipped runs. **All outcomes** also covers completed and
cancelled runs, including manually started runs of a scheduled task. A sign-in
failure is a blocked or failed run and follows the attention policy.

Each run records the policy revision and selected destinations when claimed.
Changing notification settings or muting a task suppresses old runs, including
after muting and re-enabling. Home Assistant checks the bounded durable run
history once a minute. An unavailable destination is recorded without changing
the run result or retrying indefinitely.

Persistent notifications contain only a generic update and an administrator-only
Codex Bridge link. Selected phone notifications can show the task title and,
only after a separate opt-in, a 160-character final-answer excerpt. The phone's
lock screen and notification history may expose that excerpt. No raw tool
output, credentials or private paths are copied into the default notice.

## Dependencies and boundary

Depends on HA-01 or an equivalent durable result-delivery contract. Separate
task execution from notification delivery and retain run history when delivery
fails. Do not embed raw tool results or secrets in notification payloads.
