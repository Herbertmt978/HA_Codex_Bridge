# Checkpoint

Build preparation is implemented and locally verified. Issue #111 remains open
and needs native ARM64 HAOS verification. On 22 September 2026 the owner
confirmed no spare ARM device is available. The available PCs and Proxmox
host are x86_64; emulation cannot close the native acceptance criteria.

Implemented: dual-architecture staging and development builds, correct runtime
architecture reporting, rejection of mismatched build/contract architectures,
and explicit ARM browser unavailability. The verification workflow has separate
amd64 and ARM64 jobs. Release publication, Supervisor metadata and Host Access
remain amd64-only. App/Integration 1.3.0 and Bridge 0.10.0 are unchanged.

Both image builds and ABI/import probes passed. ARM64 ran under emulation;
amd64 additionally passed native HAOS-DEV startup, sandbox attestation and
authenticated MCP credential lifecycle checks. Full Linux suites passed:
1,983 Bridge tests, 351 Integration tests and eight root-restoration checks.
See [evidence](90-evidence.md) for limits and supporting checks.

The test guests were started from stopped, tested, and gracefully returned to
stopped after activity checks. Candidate DEV containers, volumes, image and
transfer archive were removed. Installed DEV remains 1.1.2; three backups and
6.3 GiB free retained. Production was not accessed. Local Docker remains
running as found; its two development images and evidence on D: are retained.

Resume from branch `Herb/arm64-qualification`. The clean temporary checkout
can be removed after the local commit; preserve the branch. Evidence and probe
scripts remain at `D:/CodexWork/ha-bridge-arm64-evidence-20260922`.
No push, public PR, release, issue closure or production update was performed.

Next: obtain native ARM64 HAOS hardware, record its initial state, and execute
the [native acceptance checklist](../../../arm64-development.md). Only then
extend publication and verify both signed images, software inventories,
provenance and the multi-architecture manifest. Optional browser and Host
Access support require their own native qualification.

Drift: hardware availability is now confirmed absent. Scope and security
boundaries are unchanged; full platform support is not claimed.

## Release follow-up

The owner subsequently authorised publication and a GitHub enhancement update.
Release 1.3.1 packages the verified build preparation, while leaving advertised
hardware and published images amd64-only. App/Integration/panel versions move
together; Bridge 0.10.0 and Codex 0.155.1 are unchanged. Host Access remains
optional and amd64-only. Issue #111 stays open for native acceptance.

Release work uses the same branch in the temporary checkout
`D:/CodexWork/ha-bridge-arm64-release-20260922`, because the main checkout still
contains unrelated edits. Remove this clean checkout after the release and
preserve the branch until integration is verified. Fresh release evidence is
under `D:/CodexWork/ha-bridge-arm64-release-evidence-20260922`.

Publication must follow successful versioned local checks, PR checks and review,
then verify the signed published image, SBOM, exact-source provenance and paired
Integration release. The existing native ARM64 hardware blocker does not block
this amd64 maintenance release and must not be described as resolved.
