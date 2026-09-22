# ARM64 App development

The owner requested issue #111, PLATFORM-01, and authorised this workstation,
the simulator or an isolated Proxmox guest for hardware checks. Production HA
is outside this development task. Publication is a later decision.

Baseline: issue #111, its planned-release document, AGENTS.md, CONTEXT.md,
the release lock, App stager/Dockerfile/startup, sandbox/browser contracts,
release workflows and their tests. The private operations map governs machines
and lifecycle; no private inventory is copied into this repository.

Success means an ARM64 build and qualification path with truthful capability
reporting, preserved amd64 behaviour and verified immutable multi-architecture
publication when native acceptance is available. Emulation cannot establish
native kernel sandbox or hardware support. Stop as needs-verification if no
native ARM64 HAOS target is available; do not advertise ARM64 as qualified.

Work batches:

1. Audit ARM64 assets, build/runtime assumptions and available test machines.
2. Implement architecture-aware staging/runtime and guarded build qualification.
3. Verify both image architectures, sandbox failure behaviour and amd64 regression.
4. Qualify native ARM64 HAOS if a target is supplied; record missing evidence
   otherwise. Document optional browser/Host Access boundaries and restoration.

The existing release lock owns executable identities. The sandbox contract owns
the runtime architecture. App metadata owns published architectures. Optional
browser and Host Access support must not be inferred from a successful build.
No security fallback, production deployment or claim of Raspberry Pi support
follows from an emulated test.

The main checkout has unrelated work. This task owns branch
`Herb/arm64-qualification` and worktree `D:/CodexWork/ha-bridge-arm64-20260922`,
created from current main. Remove the clean temporary checkout after committing
the implementation or handoff; preserve branch history and unrelated files.
