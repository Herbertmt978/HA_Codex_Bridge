# Changelog

All notable App changes are recorded here.

## 0.6.5

- Recovers visible model and reasoning-level choices from Codex's bundled
  catalogue when live app-server discovery is temporarily unavailable. This
  remains dynamic and exposes the installed runtime's GPT-5.6 models plus
  model-specific `max` and `ultra` levels without hard-coded model names.
- Retries provisional catalogues quickly, prefers a verified last-known-good
  catalogue, and keeps the small static list as the final emergency fallback.
- Bundles Bridge `0.5.5` with the Sigstore-verified Codex `0.144.4` runtime;
  the paired Integration `0.6.5` also introduces a compact chat tree and keeps
  transient artifact reservations from becoming false connection failures.

## 0.6.4

- Publishes the Supervisor-assigned private App IP, rather than the App
  hostname, so discovery reaches the Bridge on Home Assistant OS.
- Adds a fresh, validated non-secret publication marker on each App start so
  Supervisor re-pushes discovery while retaining its issued UUID.
- Categorizes discovery failures without logging tokens or endpoint credentials,
  and migrates the dedicated `/config` mapping to `app_config:rw`.
- Keeps Bridge `0.5.4` and Codex `0.144.4` without changing Integration API
  compatibility.

## 0.6.3

- Recovers ChatGPT device sign-in automatically when Codex omits a login
  correlation ID or a completion notification is delayed. A bounded account
  check preserves the active one-time code until sign-in is authoritative.
- Invalidates the signed-out model catalogue as soon as ChatGPT entitlements
  change, so newly available Codex models and reasoning levels such as `max`
  and `ultra` are discovered immediately instead of after the cache expires.
- Classifies usage windows by their advertised duration, keeping a weekly-only
  allowance under **Week** and reporting the absent five-hour window as off.
- Keeps a successfully created chat selected and usable while secondary list,
  event, artifact, status, or interaction snapshots retry.
- Bundles Bridge `0.5.4` and the Sigstore-verified Codex `0.144.4` runtime
  without changing the Integration API compatibility.

## 0.6.2

- Fixes a false startup failure when Codex `0.144.4` reports its bounded
  supplemental tool directories in `writableRoots`. Every reported root must
  now be canonical and contained by the selected workspace; sibling, parent,
  relative, duplicate, traversal, and malformed roots remain rejected. The
  same rule now protects both startup attestation and normal thread
  start/resume validation.
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
- Limits the writable host mapping to `app_config:rw`, with workspaces under
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
