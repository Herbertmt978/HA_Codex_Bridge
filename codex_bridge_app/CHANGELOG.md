# Changelog

All notable App changes are recorded here.

## 0.6.0

- Introduces the experimental private Home Assistant Codex Bridge App for
  `amd64` and its private Supervisor connection to the `0.5.3` Integration.
- Limits the writable host mapping to `addon_config:rw`, with workspaces under
  `/config/workspaces`, and fails closed when the locked tool sandbox cannot
  complete its boot-time attestation.
- Uses ChatGPT device login; no OpenAI API-key setup is part of the App flow.
- Discovers models and supported reasoning levels from the installed Codex
  runtime, preserving configured selections during marked temporary recovery.
- Uses immutable versioned images. The running container does not self-update.
  App-image rollback is not yet validated; recovery is a cold backup or an
  existing private external Bridge until an earlier immutable tag and restore
  procedure are published and tested.

This source release remains experimental. A private immutable image completed
sandbox self-test and authenticated readiness on an amd64 Home Assistant OS
development VM on 14 July 2026. It is not a public App-image release.
