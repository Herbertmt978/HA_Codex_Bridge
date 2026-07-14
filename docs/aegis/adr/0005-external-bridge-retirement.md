# ADR 0005: Bounded external Bridge retirement

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

The external Bridge, including the Windows path, remains a private rollback and
compatibility carrier only through the 0.6.x line. It uses the explicit v0
adapter and retains its own workspace and credentials. The App does not import
or delete that state, stop a VM, move workspaces, or copy credentials.

The protected HAOS sandbox gate has passed. Retirement is permitted only after
the remaining remote-flow, cold backup/restore, and App update/rollback evidence
also passes and the user explicitly removes the VM fallback. The next breaking
release is the earliest deletion boundary.

## Evidence and consequences

The compatibility boundary is exercised by the Integration protocol and
configuration-flow tests, while `docs/migration-from-windows.md` preserves the
manual recovery guidance. The [implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md)
and the [Task 24 plan](../plans/2026-07-12-home-assistant-native-codex.md#task-24-complete-compatibility-migration-adr-and-architecture-retirement-records)
state the bounded window and acceptance conditions.

The trigger has not been met merely because the repository implementation and
its local tests exist.
