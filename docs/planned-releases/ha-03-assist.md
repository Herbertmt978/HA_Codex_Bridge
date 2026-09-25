# HA-03: Assist conversation agent

Status: In development. Version and date: unassigned.

Tracking issue: [#105](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/105).

## Problem and outcome

Users should be able to select Codex Bridge as an HA Assist conversation agent
and receive Codex's answer directly, without opening the Bridge panel.

## Scope

- Provide an opt-in conversation entity backed by bounded Bridge task actions.
- Let an administrator select one project. Restrict Assist turns to observe
  mode, disabled web search and no host access; explain what workspace material
  Codex can read and what is returned as speech.
- Return Codex's final text directly when it completes within the response
  window. Preserve a longer-running task and explain where to review it.

## Acceptance criteria

- Native Assist qualification starts a bounded task and returns its final text.
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
