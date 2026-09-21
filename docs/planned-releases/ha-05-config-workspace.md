# HA-05: Scoped Home Assistant configuration access

Status: Planned; storage design required. Version and date: unassigned.

Tracking issue: [#107](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/107).

## Problem and outcome

Ordinary Bridge workspaces are separate from Home Assistant configuration, while
the optional Host Access App grants root rights. Add a narrower way to review
and edit explicitly selected configuration files without requiring host root.

## Scope

- Design a restricted mount or mediated file interface with explicit read/edit
  permissions, selected paths and a clear view of proposed changes.
- Exclude secrets, authentication stores, unrelated App data and backups by
  default. Define handling of symlinks and generated/storage-managed files.
- Provide diff review, validation and a supported recovery workflow. Configuration
  reload or Core restart must be a separate authorised action.

## Acceptance criteria

- Native DEV tests show permitted files can be read/edited and excluded paths
  cannot be reached through traversal, symlinks or tool commands.
- Validation failure preserves the usable configuration; concurrent external
  edits are detected rather than overwritten.
- Recovery is tested against disposable configuration and follows the user's
  backup policy without accumulating an unbounded archive.
- Removing access invalidates retained sessions and unattended grants.

## Dependencies and boundary

Requires a reviewed file boundary, rollback model and HA validation interface.
HA-MCP may already meet some users' needs and remains a supported alternative.
Do not turn the ordinary workspace into a broad writable `/config` mount.
