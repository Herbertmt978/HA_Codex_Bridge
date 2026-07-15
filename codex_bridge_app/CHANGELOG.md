# Changelog

All notable App changes are recorded here.

## 0.6.2

- Fixes a false startup failure when Codex `0.144.4` reports its bounded
  supplemental tool directories in `writableRoots`. Every reported root must
  now be canonical and contained by the selected workspace; sibling, parent,
  relative, duplicate, traversal, and malformed roots remain rejected.
- Hardens `lsm_get_self_attr` parsing by consuming the complete variable-length
  record stream and rejecting mismatched counts, trailing bytes, malformed
  contexts, and unexpected AppArmor state.
- Preserves Codex's official `--no-proc` restrictive-container fallback on
  HAOS. User, PID, and network namespaces, the read-only filesystem, AppArmor,
  seccomp, zero capabilities, and `no_new_privs` remain enforced without
  requesting `SYS_ADMIN`.
- Adds a distinctive Codex Bridge SVG identity, generated Home Assistant PNG
  assets, and a repository social-preview card.
- The candidate files passed the complete production sandbox self-test on the
  target HAOS host. Immutable-image startup and authenticated readiness remain
  post-release gates.

## 0.6.1

- Fixes Supervisor discovery on Home Assistant OS by using Bashio's supported
  App-hostname helper. The ready Bridge can now publish its private endpoint
  instead of remaining in a retry loop.
- Verifies at image-build time that the pinned Home Assistant base exports the
  required discovery helper.
- Continues to bundle Bridge `0.5.3` and Codex `0.144.4` without changing the
  Integration API contract.

## 0.6.0

- Introduces the experimental private Home Assistant Codex Bridge App for
  `amd64` and its private Supervisor connection to the independently released
  `0.5.4` Integration.
- Limits the writable host mapping to `addon_config:rw`, with workspaces under
  `/config/workspaces`, and fails closed when the locked tool sandbox cannot
  complete its boot-time attestation.
- Selects and verifies separate managed Codex permission profiles: Observe is
  read-only, while Edit and Full auto are confined to the selected writable
  workspace. Model-controlled tool networking remains disabled in every mode.
- Uses ChatGPT device login; no OpenAI API-key setup is part of the App flow.
- Discovers models and supported reasoning levels from the installed Codex
  runtime, preserving configured selections during marked temporary recovery.
- Uses immutable versioned images. The running container does not self-update.
  App-image rollback is not yet validated; recovery is a cold backup or an
  existing private external Bridge until an earlier immutable tag and restore
  procedure are published and tested.

The public App 0.6.1 release is a signed immutable image with an SPDX SBOM and
build provenance, but remains experimental and is known-bad on target HAOS.
Pinned Codex `0.144.4` correctly rebuilt its sandbox in official `--no-proc`
restrictive-container mode. Readiness instead failed because App 0.6.1 required
`writableRoots` to equal the workspace exactly, while Codex reports bounded
supplemental tool directories beneath it. The candidate 0.6.2 files passed the
complete production sandbox self-test on the target host; the released
immutable image remains the authoritative startup and readiness gate.
