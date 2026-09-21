# HA-03: Assist delegation

Status: Planned. Version and date: unassigned.

Tracking issue: [#105](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/105).

## Problem and outcome

Users should be able to ask their chosen HA Assist agent to delegate an approved
piece of work to Codex without opening the Bridge panel.

## Scope

- Provide a supported, opt-in Assist/LLM tool or exposed-script pattern that
  calls the bounded Bridge task actions.
- Let an administrator choose allowed projects and modes. Explain which prompt
  and context are sent to Codex and where results will appear.
- Return a useful acknowledgement for long work; deliver the eventual result
  through HA-01/HA-02 rather than keeping the conversation request open forever.

## Acceptance criteria

- Native Assist qualification starts a bounded task and follows its result.
- Requests cannot choose an unexposed project, obtain host access, answer an
  administrator approval or broaden MCP permissions through prompt text.
- Unavailable Codex, authentication expiry and duplicate requests return clear
  responses with no duplicate execution.
- Each supported Assist integration path is documented and tested; provider
  differences are not presented as universal compatibility.

## Dependencies and boundary

Depends on HA-01 and an explicit identity/exposure policy. Voice recognition
alone is not administrator authentication. This is optional delegation to
Codex, not a replacement for Home Assistant's ordinary device-control agent.
