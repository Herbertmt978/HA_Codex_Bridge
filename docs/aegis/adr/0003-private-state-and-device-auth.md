# ADR 0003: Private state and ChatGPT device authentication

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

Home Assistant mode keeps Bridge state and the Codex credential home in
App-private `/data` directories, while reviewed workspaces live below the
dedicated `/config/workspaces` mapping. The Integration stores neither the
browser's Home Assistant credential nor the App's Bridge bearer token in the
panel surface. The App creates and verifies its bearer-token file as private
runtime state and starts the long-lived process with a sanitized environment.

ChatGPT device authentication is initiated and coordinated by the App's
app-server path. External Bridge conversations, state, and credentials are not
imported into the App.

## Evidence and consequences

`initialize_runtime.py`, `settings.py`, and the App run service establish the
private state, token-file, and environment contract. The repository's auth and
configuration-flow tests cover device-auth and credential isolation behavior.
The [implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md)
keeps the storage and recovery limits explicit.

A cold backup/restore has not completed release acceptance and must not be
represented as proven by this decision.
