# HA-01 native task-action qualification - Checkpoint

- Task ID: 2026-09-26-ha01-native
- Current todo: Complete local suite, publish candidate and repeat managed DEV fixture
- Active slice: Cancellation race and stale upgrade capabilities
- Blocked on: none
- Next step: Finish non-root Linux Bridge suite

## DriftCheckDraft

- Scope status: Existing task-action qualification and bounded bug fixes
- Compatibility status: Existing private API and permissions unchanged
- Retirement status: No duplicate owner or fallback added
- New risk signals:
- none
- Advisory decision: needs-verification

## Checkpoint Update

- Current todo: Finish clean Linux suite, publish 1.8.3 and repeat native DEV acceptance
- Active slice: Local release gates
- Completed todos:
- Native 1.8.2 fixture and cleanup; reproduce and repair cancellation race; real HA upgrade capability regression; frontend and image gates
- Evidence refs:
- docs/verification/ha-01-native-2026-09-26.md
- Blocked on: none
- Next step: Check final non-root suite before one verified push

## Checkpoint Update

- Current todo: Publish 1.8.3 candidate and repeat managed DEV acceptance
- Active slice: Publication and managed acceptance
- Completed todos:
- Local Bridge 2195 passed with 27 skips; HA 413 passed; frontend 478 unit and 85 browser; Windows 22; static, hassfest and image checks
- Evidence refs:
- docs/verification/ha-01-native-2026-09-26.md
- Blocked on: none
- Next step: Inspect and stage only intended files, commit and push
