# PLATFORM-01: ARM64 Home Assistant OS support

Status: In development; native qualification required. Version and date: unassigned.

Architecture-aware staging, image construction and runtime reporting are being
qualified. Stable publication remains amd64-only. See the
[development and native acceptance guide](../arm64-development.md).

Tracking issue: [#111](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/111).

## Problem and outcome

The published App currently supports amd64 only. Qualify aarch64 so supported
ARM64 Home Assistant machines can run the same core Bridge experience.

## Scope

- Verify availability and provenance of Codex, sandbox and browser dependencies
  for aarch64. Publish architecture-specific signed images and a verified manifest.
- Define supported hardware/resource requirements based on tests.
- Negotiate any optional capability that cannot be supported on ARM64 rather
  than advertising it or silently falling back to unsafe execution.

## Acceptance criteria

- A real ARM64 HAOS installation passes install, sign-in, chat, workspace,
  scheduling, terminal, MCP and update/restart checks for advertised features.
- Sandbox denial and process-cleanup tests pass natively, not just under emulation.
- Image signatures, inventory and source provenance are verified for both
  architectures, and existing amd64 installations still update correctly.
- Documentation identifies separately qualified browser/Host Access features
  and any resource limits without claiming every Raspberry Pi is supported.

## Dependencies and boundary

Requires an available native ARM64 test target and verified runtime assets.
No native target has been assigned by this plan. Adding `aarch64` to a YAML
list alone is not completion.
