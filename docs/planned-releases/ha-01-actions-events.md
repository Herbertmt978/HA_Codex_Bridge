# HA-01: Task actions and result events

Status: Planned. Version and date: unassigned.

Tracking issue: [#103](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/103).

## Problem and outcome

The Bridge panel and its scheduler can run tasks, but HA scripts and automations
do not have a supported public service-action interface. Add actions to start,
continue and cancel bounded tasks, plus structured lifecycle/result events.

## Scope

- Define documented HA actions with selectors for an approved project or chat,
  prompt, execution mode and supported model overrides.
- Return a durable task reference promptly. Report completion, failure and
  interaction-needed outcomes through a documented event contract.
- Reuse the existing Bridge scheduler, admission, cancellation and run history.
  Establish the permissions of automation-originated calls explicitly.

## Acceptance criteria

- Native HA scripts start and continue a fixture task and receive its outcome;
  cancellation, restart and retry do not duplicate execution or result events.
- Invalid targets, stale grants, overlapping work and unavailable Apps produce
  understandable outcomes without silently gaining broader access.
- Event payloads exclude tokens, raw tool output and private paths. Result text
  is included only under an explicit, documented disclosure policy.
- Integration setup/unload/reload correctly owns all services and listeners.

## Dependencies and boundary

Requires an idempotent task contract and a decision on unattended permissions.
Provides the basis for HA-02 and HA-03. This does not expose a public Bridge API
or allow an ordinary HA user to bypass administrator controls.

Reference: [Codex for Home Assistant](https://github.com/moryoav/home-assistant-codex)
documents actions and task-result events. Review any reusable MIT code and
preserve attribution; do not import its access model unchanged.
