# ADR 0005: Bounded external Bridge retirement

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

The external Bridge, including the Windows path, remains a private rollback and
compatibility carrier only through the 0.6.x line. It uses the explicit v0
adapter and retains its own workspace and credentials. The App does not import
or delete that state, stop a VM, move workspaces, or copy credentials.

Codex `0.144.4`'s official `--no-proc` fallback worked on target HAOS: denial of
a fresh `/proc` mount left the user, PID, and network namespaces, read-only
filesystem, AppArmor, and seccomp intact. App 0.6.1's fatal readiness cause was
instead a sandbox-self-test contract mismatch: it required `writableRoots`
exactly `[workspace]`, while the real `ha_bridge` `workspaceWrite` response
includes bounded supplemental roots (`.agents`, `.codex`, `.cursor`, `.git`,
and `.vscode`) beneath the workspace. The proc-less probe already used direct
`capget`/`prctl`/`lsm_get_self_attr` calls, without requesting `SYS_ADMIN` or
weakening isolation. App 0.6.2 validates canonical contained supplemental roots
and hardens `lsm_get_self_attr` record parsing;
candidate files passed the complete production self-test on target HAOS, but
immutable image startup and authenticated readiness remain pending
release/post-release checks. Retirement is permitted only after those checks
and the remaining remote-flow, cold backup/restore, first automatic update, and
App-image rollback evidence pass, and the user explicitly removes the VM
fallback. The next breaking release is the earliest deletion boundary.

## Evidence and consequences

The compatibility boundary is exercised by the Integration protocol and
configuration-flow tests, while `docs/migration-from-windows.md` preserves the
manual recovery guidance. The [implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md)
and the [Task 24 plan](../plans/2026-07-12-home-assistant-native-codex.md#task-24-complete-compatibility-migration-adr-and-architecture-retirement-records)
state the bounded window and acceptance conditions.

The trigger has not been met merely because the repository implementation and
its local tests exist.
