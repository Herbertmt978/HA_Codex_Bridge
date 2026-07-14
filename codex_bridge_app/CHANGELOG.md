# Changelog

All notable App changes are recorded here.

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

This release remains experimental. Its public immutable image is signed and is
accompanied by an SPDX SBOM and build provenance. A protected-runtime image
completed sandbox self-test and authenticated readiness on an amd64 Home
Assistant OS development VM on 14 July 2026.
