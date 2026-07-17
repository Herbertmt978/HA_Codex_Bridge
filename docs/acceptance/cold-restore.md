# Cold restore and retained-image recovery acceptance

This is a target-Home-Assistant administrator procedure. It is intentionally
not automated by the repository: the collector below is an offline, read-only
**evidence-format and snapshot-consistency validator** and has no Supervisor or
Home Assistant client. A successful collector result does not prove that a
backup, restore, or rollback happened; only target-HA evidence captured while
performing the operator procedure can close that acceptance gate.

## Stop conditions

Before any mutation, stop unless all of these are true:

- the target is a designated test Home Assistant, identified by a stable,
  non-secret `test_ha` label and Supervisor UUID;
- a completed cold backup has a safe identifier and has been independently
  verified in Home Assistant;
- the current immutable App image and a distinct retained previous immutable
  image are healthy, with a documented watchdog/reconnect path; and
- the operator can use Home Assistant's supported UI or API to recover. Do not
  grant the App broad Supervisor rollback permission.

Never place Home Assistant URLs, tokens, cookies, authorization headers,
prompts, account identifiers, or backup contents in the snapshots.

## Snapshot format

Capture `pre.json` before the operation and `post.json` after it. Both are
separate regular, non-symlink files with exactly this shape (all hashes are
SHA-256 hex):

```json
{
  "schema_version": 1,
  "capture": {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "phase": "pre",
    "captured_at": "2026-07-17T10:00:00Z"
  },
  "test_ha": "acceptance-ha-01",
  "supervisor_uuid": "11111111-1111-1111-1111-111111111111",
  "components": {
    "app": {"version": "0.8.4", "digest": "sha256:<64-lowercase-hex>"},
    "integration": {"version": "0.8.4", "digest": "sha256:<64-lowercase-hex>"},
    "bridge": {"version": "0.7.3", "digest": "sha256:<64-lowercase-hex>"},
    "codex": {"version": "0.144.5", "digest": "sha256:<64-lowercase-hex>"}
  },
  "backup": {"id": "backup-acceptance-001", "verified": true},
  "readiness": {"home_assistant": "ready", "app": "ready", "bridge": "ready"},
  "sandbox": {"status": "passed"},
  "account": {"state": "authenticated"},
  "fingerprints": {
    "workspace": "<64-lowercase-hex>",
    "chat": "<64-lowercase-hex>",
    "artifact": "<64-lowercase-hex>",
    "automation": "<64-lowercase-hex>"
  },
  "recovery": {
    "retained_image": {"healthy": true, "components": {"app": {"version": "0.8.3", "digest": "sha256:<64-lowercase-hex>"}, "integration": {"version": "0.8.3", "digest": "sha256:<64-lowercase-hex>"}, "bridge": {"version": "0.7.2", "digest": "sha256:<64-lowercase-hex>"}, "codex": {"version": "0.144.5", "digest": "sha256:<64-lowercase-hex>"}}},
    "rollback": null
  }
}
```

For `post.json`, generate a new UUID, set `phase` to `post`, and use a strictly
later timezone-aware RFC 3339 timestamp. Timestamps allow seconds and at most
six fractional digits. Reusing a capture UUID, swapping phases, copying one
snapshot over the other, or passing the same/hard-linked file fails closed.

Use category values and opaque identifiers only. The collector accepts no
extra fields, which intentionally rejects URLs and credential-shaped fields.
It does not retain the raw Home Assistant identity, Supervisor UUID, or backup
identifier in its output; it emits only SHA-256 fingerprints for those values
and the two capture UUIDs. All three readiness fields must be exactly `ready`,
the sandbox must be exactly `passed`, and the account must be exactly
`authenticated` in both snapshots.

## Cold backup and restore

1. Record the preflight snapshot and validate that its backup and retained
   image are verified. The retained App version/digest pair must differ from
   the current App pair; equality is not a rollback point.
2. In Home Assistant's supported administrator UI/API, create the cold backup,
   make one controlled reversible test mutation, and restore that backup.
   Do not use this repository helper to perform any of these operations.
3. After Home Assistant, the App, and Bridge are ready, record `post.json`.
   For a cold restore the complete `components`, categories, backup identifier,
   and all four fingerprints must match the preflight snapshot.
4. Validate it offline:

```text
python scripts/acceptance/collect_recovery_acceptance.py --mode cold-restore --pre pre.json --post post.json --output cold-restore-result.json
```

## Retained previous-image recovery

Perform this as a separate evidence run. Update to a newer immutable App image
using the supported Home Assistant path, then recover only to the preflight
retained image through that same supported path. In the post snapshot, set
`recovery.rollback` to a verified object containing `from_components` (the
preflight current components) and `target_components` (the preflight retained
components). The post `components` must equal that target exactly.

```text
python scripts/acceptance/collect_recovery_acceptance.py --mode retained-image --pre pre.json --post post.json --output retained-image-result.json
```

The result manifest has status `evidence_format_validated` and scope
`offline_snapshot_consistency`; it must not be reported as target-HA recovery
acceptance on its own. It is canonical JSON and contains safe component
versions/digests, capture timestamps, and redacted capture, identity, and
backup fingerprints. A failure writes no result and never changes an existing
result file.
