# TASK-01: Scheduling through chat

Status: Included in the paired 1.6.0 release.

Tracking issue: [#112](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/112).

## Problem and outcome

The existing editor supports recurring and one-off tasks. Let a user describe
the task and timing conversationally, review the interpretation, then save it.

## Scope

- Propose a title, instructions, destination chat, frequency and HA time zone
  from a natural-language request.
- Show the next runs and ask about genuinely ambiguous times or recurrence.
- Reuse the existing schedule store and editor for create, update, pause and
  cancel. Preserve model, reasoning and permission choices explicitly.

## Acceptance criteria

- Tests cover daily/weekly/interval/one-off requests, daylight-saving changes,
  ambiguous dates, edits and cancellation.
- A proposed schedule cannot run before confirmation or duplicate an existing
  task through retries. Cancellation affects the intended schedule only.
- Host-access and other unattended grants retain their separate acknowledgements;
  an instruction inside task content cannot provide consent.
- Native HA runs use the existing scheduler with the browser closed and record
  overlap, approval-needed and missed-window outcomes accurately.

## Dependencies and boundary

Depends on the existing scheduler and a bounded schedule-proposal tool contract.
Notifications depend on HA-02. This is not a second scheduler inside the model,
browser or messaging connector.
