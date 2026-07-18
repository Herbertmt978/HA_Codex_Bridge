# Changelog

All notable App changes are recorded here.

## 1.0.0

- Promotes the Supervisor App from Home Assistant's experimental lifecycle
  stage to stable while retaining the explicit `amd64` support boundary.
- Enables the composer Send action immediately when a prompt is entered or
  pasted, removing the need for an unrelated refresh before clicking Send.
- Adds an explicit, monotonic `--set-version X.Y.Z` release-sync path so major
  and minor App releases update every owned immutable-image projection without
  manual edits, with rollback if a later file replacement fails.
- Carries forward the account-neutral Home Assistant chat contract accepted on
  target HAOS in `0.8.11`: local chats and history remain visible while the
  currently signed-in ChatGPT account starts fresh private provider continuity.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.6` without changing its Integration API compatibility.

## 0.8.11

- Keeps Home Assistant chats, projects, transcripts, files, workspace settings,
  archive state, and automation targets independent of the signed-in ChatGPT
  account.
- Privately binds native Codex thread handles to their owning ChatGPT account;
  after an account change, the same Home Assistant chat starts a fresh provider
  thread instead of attempting to resume one owned by the previous account.
- Detaches unowned pre-0.8.11 provider handles once during migration without
  deleting or partitioning local history. No account identity or binding marker
  is exposed through the panel, API, events, diagnostics, or logs.
- Reconciles account-update hints through an authoritative account read under
  the runtime gate. Identity-less reads detach private provider continuity and
  keep UI and automation turns blocked until ownership is verifiable.
- Publishes each account-change hint as `checking` and auth-required before the
  authoritative read begins, closing direct-prompt and automation admission
  before local mutation while deferring catalogue invalidation until the
  settled account result.
- Fails closed if an App-server generation change needs account reconciliation
  while a turn owns the runtime gate, preventing the previous generation's
  ready status from admitting work before account ownership is reverified.
- Discards an active device login when its App-server generation restarts,
  releases the obsolete auth lease outside the coordinator lock, and performs
  a fresh authoritative account read instead of leaving all turns blocked.
- Returns the existing fail-closed auth snapshot while runtime ownership blocks
  reconciliation, preventing frequent Home Assistant status polls from
  generating duplicate revisions and durable `auth.status_changed` events.
- Invalidates shared model and provider-capability catalogues when the private
  account owner changes even if both accounts report the same plan, so model
  and reasoning choices always reflect the account currently signed in.
- Invalidates an account read or device-login poll if a newer account-update
  hint arrives before it finishes, so a stale account can never be published
  ready or rebound after the user switches accounts.
- Rechecks account admission when each queued prompt becomes active. Prompts
  queued before an account switch stop locally without starting or resuming a
  provider thread, while the Home Assistant chat and its prompt remain visible.
- Rechecks account admission inside the active-run branch before accepting an
  interactive follow-up, so an account-update hint raced against the request
  cannot persist or send a stale `turn/steer`.
- Requires the authoritative auth state to be fully `ok` before broker
  admission or automation target preparation, so transient account checks and
  sign-out cannot leave behind local chat mutations.
- Gives scheduled runs a one-shot broker admission before creating or changing
  their target chat, transfers that exact lease into prompt submission, and
  replays an existing automation outcome without creating or editing a chat.
  Logout and automation dispatch therefore have one atomic winner.
- Reserves prompt ownership before the final account-admission check and
  provider-thread lookup, making account rebinding atomic with provider
  continuity selection and preventing a detached previous-account thread from
  being resumed by a newly accepted turn. Any admission or local run-state
  validation failure releases that reservation without accepting the prompt.
- Runs authoritative account reconciliation outside the runtime broker lock,
  then repeats deletion and idempotency validation before acceptance, avoiding
  the storage-to-broker lock inversion with concurrent chat/project deletion.
- Rebinds only provider/runtime metadata without resolving unchanged historical
  workspaces, attachments, or artifacts, so one old chat whose backing files
  were removed outside the Bridge cannot block a healthy account switch.
- Settles recovered runtime checkpoints before account binding, preventing an
  interrupted run from restoring a previous account's provider thread during
  startup.
- Keeps provider session/thread/turn handles, active runtime ownership, and
  queued prompt internals out of browser-facing thread responses while
  preserving the complete private record inside the App.
- Addresses approvals and questions through an App-local interaction ID. The
  browser no longer receives provider run, turn, or item locators, and durable
  event replay redacts legacy provider-continuity fields at read time.
- Advertises the safer interaction contract only from the HA-native profile,
  so a newer Integration fails locally and actionably when temporarily paired
  with an older App while the legacy external profile promises no absent route.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.6` without changing its Integration API compatibility.

## 0.8.10

- Coalesces adjacent Codex text deltas into ordered, bounded batches before the
  Bridge's durable callback work, so one high-rate long response no longer
  consumes one callback slot per text fragment and restarts after a short prefix.
- Exercises the real App-server client and Runtime Broker callback path against
  a scripted peer with 5,000 distinct streamed words, exact completed-message
  reconstruction, one successful terminal event, and no generation change.
- Keeps terminal notifications behind every accepted text batch and retains
  the existing fail-closed overload path for genuinely heterogeneous callback
  backlogs.
- Shows a typed runtime restart as **Run interrupted**, preserves the partial
  response, and provides a safe explanation instead of mislabelling the event
  as a generic **Run failed** state.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.5` without changing its Integration API compatibility.

## 0.8.9

- Keeps a long streamed assistant response visible as **Partial response** when
  the provider stream fails, and shows a safe, specific failure category rather
  than collapsing the turn to three lines plus a generic **Run failed** state.
- Classifies every failed-turn variant advertised by bundled Codex `0.144.5`
  without persisting provider messages, paths, HTTP details, or credentials.
- Removes a stale usage-limit lock after a newer healthy snapshot and serializes
  competing limit refresh/failure writes so the account state cannot race.
- Prevents the compact **Create chat** action from wrapping or clipping, quiets
  the refresh control, and removes native scrollbar arrow buttons from the chat
  rail.
- Preserves completed and interrupted long-response payloads once delivered to
  the Runtime Broker. Rapid token-delta callback backpressure was not yet
  covered in this release and is corrected in `0.8.10`.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.4` without changing its Integration API compatibility.

## 0.8.8

- Shows the complete two-step download state on generic Files rows: authenticated
  outputs move from **Prepare download** through **Preparing...** to a synchronous
  **Save file** handoff, matching the generated-image and PDF controls.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.3` without changing its Integration API compatibility.

## 0.8.7

- Reuses a complete, authenticated artifact preview for the browser download
  handoff, keeping generated-image downloads inside the user's active click.
- Retains each temporary download anchor and blob URL together for a bounded
  60-second grace period before removing and revoking them.
- Expires an unsaved prepared blob after 60 seconds and clears it immediately
  when the panel disconnects or its chat/file context changes.
- Gives artifacts that were not safely cached in full an explicit two-stage
  **Prepare download** then **Save file** flow, so the final browser handoff is
  also synchronous without putting Home Assistant credentials in a URL.
- Covers cached user-activation downloads, authenticated delayed downloads,
  filename and byte integrity, and bounded cleanup in unit and browser tests.
- Bundles the Sigstore-verified Codex runtime `0.144.5` and Bridge `0.7.3`.

## 0.8.6

- Makes artifact downloads browser-safe by handing the fetched blob to an
  attached, hidden download anchor and revoking its object URL only after the
  browser has accepted the click.
- Covers delayed multi-megabyte generated-image downloads, filename and byte
  integrity, failed-fetch behavior, anchor cleanup, and deferred URL cleanup.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.3` without changing its Integration API compatibility.

## 0.8.5

- Makes generated-image **Open preview** reliably reveal the authenticated
  Files preview, including when the image was already selected automatically.
- Adds an inline browser Download action for generated images while retaining
  the existing Home Assistant-authenticated download path for every artifact.
- Keeps the Home Assistant document fixed and constrains scrolling to the chat
  transcript at desktop and narrow widths, so the composer and workspace rails
  remain in place.
- Describes image generation truthfully as Codex's provider-native ChatGPT
  capability and removes unsupported `$imagegen`/local-skill guidance.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.3` without changing its Integration API compatibility.

## 0.8.4

- Repairs the typed PDF **Files** `409` by keeping selected-workspace listing,
  archive, preview, and download independent of unrelated stale workspace-root
  debris while preserving fail-closed aggregate quota checks for mutations.
- Removes only exact stale sandbox self-test locators during root-side startup,
  with descriptor-bound identity checks and ownership rollback on partial
  cleanup failure.
- Adds ChatGPT-account image generation behind runtime-advertised
  `imageGeneration` and `namespaceTools` capabilities. Generated PNG, JPEG,
  and WebP output is strictly validated, revocation-leased, and stored only as
  a private Home Assistant artifact; no API key or browser/provider route is
  introduced.
- Adds a provider-neutral LAN, Nabu-shaped, and Cloudflare-shaped transport
  harness plus redacted offline evidence collectors for external-route and
  cold-restore/retained-image acceptance. Synthetic or hand-written evidence
  cannot mark either real-system gate accepted.
- Adds the fixed high-level App-owned browser protocol, broker, policy proxy,
  and pinned worker scaffold. The capability remains absent because current
  HAOS isolation cannot prove that Chromium is separated from App-private
  data and direct sockets; no sandbox bypass or extra privilege is enabled.
- Limits hassfest discovery to the Integration tree so ignored local build
  manifests cannot be mistaken for Home Assistant integrations.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.3` without changing its Integration API compatibility.

## 0.8.3

- Brings the Home Assistant workspace materially closer to Codex desktop with
  one shared 840-pixel reading rail, a compact floating composer, a quieter
  navigation plane, and a 330-360-pixel floating Activity card.
- Replaces the remaining nested transcript regions with one continuous scroll
  surface, so messages, safe live actions, approvals, questions, and run stages
  stay in their natural reading order.
- Adds compact Codex-style **Outputs**, **Subagents**, **Background activity**,
  **Browser**, and **Sources** sections. Active agents show bounded working and
  completed counts without exposing names, prompts, commands, or paths.
- Settles stale subagent working counts when a run completes, fails, or is
  cancelled while retaining completed and needs-attention totals.
- Keeps artifact-index, archive, and preview failures local to **Files** after a
  successful thread/status refresh, preventing a valid reply from becoming a
  false global **Connection issue**.
- Repairs orphaned busy thread projections when a private runtime checkpoint is
  missing after restart, and prevents an authoritative idle chat from reviving
  stale **Working**, **Preparing a response**, streaming, or steer controls.
- Adds bounded local retry states for file indexing, archive creation, and
  previews without replacing healthy connection or transcript state.
- Makes the run-stage activity popover viewport-safe on mobile and preserves
  reduced-motion, keyboard, touch-target, and screen-reader behavior.
- Aligns the header, transcript, live activity, interactions, and composer to
  the same content measure and compensates the Activity card's visual gutter so
  desktop rails remain pixel-aligned.
- Preserves that 840-pixel reading measure at 1280- and 1440-pixel widths by
  moving Activity into an accessible right drawer before the three-column shell
  can compress the conversation; exact responsive boundaries are browser-tested.
- Hides empty Background, Browser, and Sources blocks until a run has real
  activity, while keeping Outputs and bounded Subagent status immediately visible.
- Bundles the Sigstore-verified Codex runtime `0.144.5`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.7.2` without changing its Integration API compatibility.

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
