# Changelog

All notable App changes are recorded here.

## 0.8.2

- Restores aggregate workspace-scan failures to the typed, retryable
  `filesystem_scan` conflict contract instead of misreporting an unrelated
  root entry as unsafe content in the selected chat.
- Adds regression coverage for ordinary `.agents/skills` trees and PDF
  artifacts alongside operational aggregate-scan failures.
- Keeps artifact-index and preview failures local to **Files**, preserving a
  successful reply and healthy connection state while offering a bounded retry.
- Calibrates the wide Home Assistant panel to Codex desktop geometry: a wider,
  theme-derived navigation plane, one shared 900-pixel conversation axis, and
  a full-height context surface that does not disturb tablet, mobile-drawer,
  keyboard, or reduced-motion behavior.
- Adds a user-invoked, standards-based **Focus mode** for the full Codex-style
  three-pane canvas, with the desktop rail tint and floating context surface,
  native Escape handling, and accessible focus restore.
- Corrects the live-response activity suffix and strengthens semantic
  navigation/header typography without inflating transcript density.
- Moves healthy component telemetry into **Context -> System**, keeps runtime
  warnings in the conversation, quiets user bubbles and Context tabs, and
  rounds the compact composer to match the Codex desktop treatment.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.2` without changing its Integration API compatibility.

## 0.8.1

- Replaces the conflicting read-only global artifact-quota check with a bounded
  artifact manifest, so a typed `reservation_conflict` does not persist after
  capacity is available.
- Reconciles artifacts after a terminal run releases capacity.
- Makes the panel perform bounded retries for typed artifact conflicts, retain
  local **Files** status, and offer an explicit retry instead of presenting a
  false global connection outage.
- Remains a candidate pending installation and target-Home-Assistant
  validation. The 0.8.0 PDF-creation check succeeded, but PDF indexing/archive
  returned persistent HTTP 409 after an App restart; that acceptance remains
  failed/pending.
- Tracks secure App-owned browser-worker follow-up in issue #43. Interactive
  Chromium remains deferred under ADR 0006.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.1` without changing its Integration API compatibility.

## 0.8.0

- Adds Codex-style live run stages, current-action copy, file/line counters, and
  bounded subagent status to the transcript, with a responsive activity
  popover and truthful completed/failed states.
- Adds an authenticated, bounded PDF artifact viewer rendered locally with the
  bundled PDF.js canvas runtime. PDF scripting, XFA, eval, native embeds, and
  remote document frames stay disabled; open and download remain explicit
  fallbacks.
- Hardens artifact previews with declared-size and streamed-byte limits,
  zero-byte range handling, stale-request invalidation, and blob URL cleanup.
- Documents the separate fail-closed architecture required before interactive
  Chromium automation can be enabled inside the Home Assistant App.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that
  runtime.
- Bundles Bridge `0.7.0` and paired Integration `0.8.0` without changing API
  v1 compatibility.

## 0.7.5

- Makes Live mode reliably select Codex's native web-search tool for weather,
  news, schedules, prices, rules, and other current information while keeping
  model-controlled shell networking blocked.
- Replaces the tall Limits/Model/Thinking dashboard beneath the prompt with a
  compact Codex-style utility row. Full quota details remain available in the
  Usage view, and keyboard guidance remains exposed to assistive technology.
- Makes release-contract tests derive App, Bridge, and Codex versions from
  their canonical authorities so future verified runtime updates do not fail
  on stale literal version assertions.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.6.3` and paired Integration `0.7.5` without changing API
  v1 compatibility.

## 0.7.4

- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.6.2` without changing its Integration API compatibility.

## 0.7.3

- Adds provider-gated native live web search for Supervisor prompts and
  automations. The Integration preference defaults to Live, activates only
  after the App advertises the capability, and re-negotiates automatically
  after ChatGPT sign-in; model-controlled shell networking remains disabled.
- Adds signed-in ChatGPT-account image generation only when Codex advertises
  both `imageGeneration` and `namespaceTools`. Generated image results are
  persisted as private, bounded PNG, JPEG, or WebP artifacts; no OpenAI API key
  is used.
- Keeps the compact panel controls and fixes the updater's pinned `jsonschema`
  dependency installation for contract generation.
- Bundles the Sigstore-verified Codex runtime `0.144.4`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.6.2` and paired Integration `0.7.3` without changing API
  v1 compatibility.

## 0.7.2

- Fixes the signed-in Codex plugin catalogue failing with HTTP 503. The Bridge
  now accepts a bounded 8 MiB app-server message and a bounded 60-second cold
  catalogue request; the exact target catalogue is roughly 4 MiB and takes
  about 36 seconds before its cache is warm.
- Projects up to 4,096 plugins instead of silently stopping at 512, covering
  the current 1,916-plugin ChatGPT catalogue while retaining a finite limit.
- Gives only plugin catalogue HTTP reads a 75-second Integration deadline and
  an 8 MiB response cap. Other private Bridge requests keep their shorter
  timeout policy.
- Loads plugins and marketplaces from one Codex catalogue request instead of
  issuing two identical concurrent requests.
- Stabilizes the queued-run total-deadline regression test across every valid
  terminal race without weakening runtime deadline behavior.
- Bundles the Sigstore-verified Codex runtime `0.144.4`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.6.1` and Integration `0.7.2` without changing API v1
  compatibility.

## 0.7.1

- Preserves unsaved Scheduled, Skill, marketplace, and MCP form values when
  live status refreshes re-render the administrator panel, preventing empty or
  partial management requests.
- Adds regression coverage for management-form drafts and clears each draft
  only after cancellation or a confirmed successful mutation.
- Bundles the Sigstore-verified Codex runtime `0.144.4`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.6.0` without changing its Integration API compatibility.

## 0.7.0

- Adds administrator-only durable automations and scheduled-task controls.
  Home Assistant owns wall-clock scheduling; Bridge claims are revision-checked
  and idempotent, with explicit pause, overlap, capacity, and misfire outcomes.
- Adds workspace skills, global/project `AGENTS.md`, plugin and marketplace
  management, with workspace confinement, bounded content, and private backup
  snapshots for instruction files.
- Adds explicitly opt-in outbound MCP server management, disabled by default.
  Disabled startup suppresses and removes stale MCP server configuration while
  preserving other Codex extensions. Enabled MCP applies HTTPS hostname and
  best-effort DNS address validation, explicit OAuth login, no bearer-token
  configuration, no-store authorization responses, and decline-only MCP
  elicitation handling. Adding a server never publishes the App or Bridge.
- Adds granular authenticated feature advertisement, so a newer Integration
  reports an actionable App-update requirement instead of sending unsupported
  automation, MCP, skill, plugin, or instruction requests to an older App.
- Adds Codex-style live run feedback: a busy indicator on the active chat,
  safe streamed assistant text, a quiet current-action line, and a step chip
  with file/addition/deletion counts and an accessible activity-history popover.
- Adds application-style Scheduled, Skills, Plugins, MCP, Instructions,
  keyboard-shortcut, About, Security, and system-information surfaces while
  preserving the Home Assistant theme, administrator boundary, and responsive
  transcript/composer layout.
- Keeps model and reasoning choices dynamically discovered from the bundled
  Codex runtime, including GPT-5.6 and model-specific `max`/`ultra` levels when
  the signed-in account and runtime advertise them.
- Coordinates Integration/App/package/panel asset release `0.7.0`, Bridge
  `0.6.0`, and the Sigstore-verified Codex runtime `0.144.4`.

## 0.6.6

- Reworks the panel around a clean Codex-style left navigation tree with
  title-first chat rows and one action menu per chat.
- Makes archive groups collapse and search correctly, including the corrected
  search icon, while retaining accessible status and selection cues.
- Places approvals and questions after the active transcript, keeps every
  decision reachable in the natural mobile scroll flow, and removes clipped or
  stale navigation action menus.
- Enlarges mobile controls to 44px targets and folds limits, model, and thinking
  controls behind a compact mobile disclosure so chat work remains the focus.
- Keeps the paired Integration/App/package/panel asset release at `0.6.6`,
  Bridge at `0.5.5`, and the Sigstore-verified Codex runtime at `0.144.4`.

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
