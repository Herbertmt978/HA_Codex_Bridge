# ADR 0004: Immutable Home Assistant App distribution

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

The App is distributed as a versioned container image built from repository
source and the locked Codex release record. Python wheels and source archives
are transient build inputs or outputs, not repository artifacts. The release
workflow refuses an existing version tag, publishes and verifies a digest, and
records signature, SBOM, and provenance evidence for that immutable digest.

Updates are reviewed source and release-metadata changes; the long-lived App
does not self-modify its installed runtime.

## Evidence and consequences

`scripts/stage_app_context.py`, `codex_bridge_app/codex-release.json`, and the
`build-app.yml` and `release.yml` workflows define this path. The repository
test prevents committed `bridge_service/dist` wheel or source-distribution
artifacts. The [implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md)
distinguishes implemented workflow policy from a completed update or rollback
exercise.

No published image, automatic update, or rollback success is asserted by this
record alone.
