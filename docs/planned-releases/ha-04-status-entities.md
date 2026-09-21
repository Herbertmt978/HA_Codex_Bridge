# HA-04: Status and usage entities

Status: Planned. Version and date: unassigned.

Tracking issue: [#106](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/106).

## Problem and outcome

Bridge status and Codex limits are visible in the panel, but the Integration
currently has no entity platforms. Expose useful, privacy-preserving HA entities
for dashboards and automations.

## Scope

- Define connection/authentication health, task-running state and last outcome.
- Expose reported account usage windows and reset times with accurate units and
  semantics. Missing or unlimited values must not become invented zeroes.
- Keep prompt text, chat contents, account identifiers and credentials out of
  states and attributes. Prefer diagnostic entities where appropriate.

## Acceptance criteria

- Native HA registry, availability, update and reload tests prove stable unique
  IDs without duplicate entities or listeners.
- Account switching, missing usage windows, expired readings and App outages
  correctly update availability and clear stale account data.
- A dashboard can display limits and an automation can react to task state
  without polling the private Bridge independently.
- Document recorder implications and disable noisy/nonessential entities by
  default where the HA entity design calls for it.

## Dependencies and boundary

Requires a stable source of status updates and event ownership. Reuse the
existing coordinator/broker instead of adding competing polling loops.
Reference: Codex for Home Assistant documents task and usage sensors.
