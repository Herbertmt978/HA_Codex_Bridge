# Home Assistant-native Codex — Evidence Bundle Draft

## Baseline evidence

| Date | Scope | Command | Result |
|------|-------|---------|--------|
| 2026-07-12 | Existing Bridge suite in isolated worktree | `python -m pytest -q` from `bridge_service` | 115 passed in 25.03s |

## Review evidence

- Design spec independently challenged for HA Community fit, auth streaming, container/release, security, and UX.
- Implementation plan independently reviewed; release-lock ordering and JSON/SQLite crash consistency issues were fixed; reviewer returned Approved.

## Task 1A — API/build contract

| Evidence | Result |
|----------|--------|
| Initial RED | Missing `api_contract` module during collection |
| Credential-hardening RED | 43 failures, then 55 failures after realistic token/identifier cases |
| Focused GREEN | 134 passed |
| Full Bridge GREEN | 249 passed |
| Spec review | Approved |
| Code-quality review | Approved after two metadata grammar fixes |
| Commits | `0739475`, `45597a5`, `afadc02` |

Build metadata now accepts only bounded SemVer, supported architectures, exact Git/OCI image hashes, and exact SHA-256 lock digests. Realistic GitHub/OpenAI/JWT/Bearer values are rejected and absent from serialization.

## Task 1B — authenticated readiness and additive diagnostics

| Evidence | Result |
|----------|--------|
| Initial RED | 4 readiness failures; combined slice 6 failed and 5 passed |
| Implementer focused GREEN | 153 passed |
| Implementer full Bridge GREEN | 255 passed |
| Independent focused GREEN | 153 passed in 1.04s |
| Independent full Bridge GREEN | 255 passed in 17.98s |
| Diff hygiene | `git diff --check` passed |
| Spec review | Approved with no findings; reviewer-focused run 182 passed |
| Code-quality review | Ready: Yes; no Critical, Important, or Minor findings |
| Commit | `e690cdd` |

`/ready` is still bearer-token protected and now returns frozen typed API, component-version, image, capability, architecture, and readiness records. `create_app` captures validated build metadata once; `/status` retains its existing shape and gains safe version/build diagnostics. Readiness intentionally remains statically `ready` until Task 10 wires runtime and sandbox health.

## Task 2 — isolated Codex subprocess environments

| Evidence | Result |
|----------|--------|
| Initial RED | 29 failed and 18 passed; inherited parent values crossed the subprocess boundary |
| Hardening RED | 21 credential/PATH failures, then 13 provider/locale failures, then 7 Bridge/HA alias failures |
| POSIX compatibility RED | `relative:/usr/bin` incorrectly discarded the valid absolute entry |
| Independent focused GREEN | 95 passed |
| Independent full Bridge GREEN | 330 passed in 17.09s |
| Real Windows environment probe | 43 absolute PATH entries, zero empty entries, nine allowlisted keys, dedicated HOME/CODEX_HOME present |
| Spec review | Approved after credential-carrier and Bridge/HA alias fixes |
| Code-quality review | Ready: Yes; final confirmation found no findings after the POSIX compatibility fix |
| Diff hygiene | `git diff --check` passed; worktree clean |
| Commits | `61ad49a`, `649af01`, `6982cd7`, `c37042a` |

Codex subprocesses no longer copy the parent environment. The builder retains only validated executable paths, dedicated home/Codex home, safe temporary paths, structured locales, platform essentials, and existing certificate paths. Supervisor, HA, Bridge, OpenAI, GitHub, CI, cookie, authorization, proxy, and unrelated values are excluded. Realistic carrier forms are rejected even when embedded in an otherwise allowlisted value. Legacy fake-runner controls are injected only inside their tests.

## Task 3A — descriptor-anchored workspace boundary

| Evidence | Result |
|----------|--------|
| Initial RED | New test module failed collection because `codex_bridge_service.workspace` did not exist |
| Initial platform GREEN | Windows 40 passed/5 unavailable; Linux 45/45 |
| Review challenge | Initial implementation rejected for Windows TOCTOU fallback, path-based list/walk races, FIFO blocking, traceback causes, and portable-name gaps |
| Hardened Windows GREEN | 54 passed, 15 protected-I/O capability skips |
| Hardened Linux GREEN | 69 passed, zero skips, including descriptor/root-ancestor swaps, symlinks, FIFO, and special files |
| Full Bridge GREEN | 384 passed, 15 Windows capability skips in 18.75s |
| Spec review | Approved with no remaining findings after Unicode/device-alias and uniform link-error fixes |
| Code-quality review | Ready: Yes after reproducing and fixing the root-ancestor escape; final Minor regression suggestion also fixed |
| Build/diff hygiene | `compileall` and `git diff --check` passed; worktree clean |
| Commits | `ccfbb20`, `ee38aed`, `13baaeb`, `f2072b0`, `6f3ffb6` |

The accepted boundary holds a trusted root descriptor and duplicates it for every protected operation. POSIX `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`, exclusive creation, nonblocking special-file checks, descriptor-based listing/walking, and inode verification prevent lexical, symlink, ancestor-swap, and final-entry races. Unsupported platforms retain validation-only behavior and reject protected I/O; the Windows external legacy profile remains separate. Public names and errors are relative and redacted, including formatted exception chains.

## Task 3B — Home Assistant-owned filesystem integration

| Evidence | Result |
|----------|--------|
| Runtime/profile integration | HA and external profiles retain distinct storage contracts; public HA paths remain relative |
| Project/thread integration | HA-owned project and thread directories are descriptor-anchored and portable-name validated |
| Runner integration | Codex receives the selected HA workspace without broad upload-directory exposure |
| Attachment security | Selected files are copied into sealed Linux memory descriptors, reopened read-only, and passed individually through `/proc/self/fd`; private paths and sibling files are not exposed |
| Artifact security | Download metadata is source-qualified and relative; responses stream an already-open immutable snapshot with safe headers and generic failures |
| Archive security | Sources are strict-walked and snapshot one at a time; private ZIPs publish only after successful close, fsync, identity validation, metadata save, and event append |
| Concurrency and cleanup | Upload, stale-writer, artifact-dedup, archive-builder, cancellation, and thread-deletion races are covered; partial private files are removed on failure |
| Windows full suite | 403 passed, 107 skipped |
| Linux full suite | 499 passed, 1 skipped |
| Artifact/archive focused suite | 26 passed |
| Spec reviews | Approved for attachments, artifacts, and archives |
| Code-quality reviews | Ready: Yes for each accepted slice |
| Build/diff hygiene | `compileall` and `git diff --check` passed; worktree clean |
| Commits | `51e1fc9`, `12d648a`, `1e5b1f3`, `b175578`, `e07d212`, `e3a7e0a`, `e3b7c24` |

The HA profile now owns every private path involved in a run. Attachment, artifact, and archive payloads cross trust boundaries through verified descriptors and immutable snapshots, while serialized records expose only validated relative locators. Append-preserving saves and canonical locks prevent stale writers from erasing concurrent state. The external profile remains compatible with its existing path-based behavior.

The remaining Task 3 runtime fact is target-system evidence: a real HA App acceptance run must confirm inherited file-descriptor behavior under the final container and sandbox configuration. That is an explicit release gate, not evidence claimed by the host test suites.

## Task 4 — immutable resource limits and safe archives

| Evidence | Result |
|----------|--------|
| Atomic limit primitive | Immutable defaults; SQLite `BEGIN IMMEDIATE` reservations; process-lock fencing; crash-owner recovery; shared-filesystem and free-floor accounting |
| Storage integration | HA-only workspace/private/transient pools; reserve-before-mutate uploads; immutable attachment/download leases; observed workspace growth; conservative cleanup |
| Ingress boundary | Bearer authentication and raw request ceilings run before multipart parsing for both declared and chunked bodies |
| Archive boundary | Bounded EOCD and central-directory preflight; prefix and trailing-data detection; entry, metadata, expanded-byte, ratio, special-file, CRC, and rollback enforcement |
| Race coverage | Upload, workspace-growth, free-space, transient-snapshot, artifact-publication, reservation, and cleanup races covered |
| Task 4A full suites | Windows 452 passed/107 skipped; Linux 548 passed/1 skipped |
| Task 4B final full suites | Windows 458 passed/135 skipped; Linux 582 passed/1 skipped |
| Static verification | Ruff, focused mypy, `compileall`, and `git diff --check` passed |
| Spec review | Approved; fresh Linux focused run 104 passed and Windows HTTP boundary 3 passed |
| Code-quality review | Ready: Yes after reproducing and closing a disguised trailing-data ZIP bypass |
| Commits | `a92e137`, `21978b7` |

The external legacy profile remains outside the HA quota and ingress middleware paths. Task 4 deliberately exposes a reusable workspace-growth observer rather than duplicating runtime ownership: the one-active/eight-queued gate and turn watchdog are Task 7, event retention is Task 8, and rotated/redacted service logs are Task 20.

## Task 5 — supervised Codex app-server transport

| Evidence | Result |
|----------|--------|
| RED contract | Scripted peer and absent-client tests committed as `9c27c69` |
| Locked protocol | Exact `codex-cli 0.139.0` stable/v2 method, payload, result, full-bundle, and schema digests generated from the installed Codex schemas |
| Focused GREEN | 31 passed, 1 platform skip after runtime version and graceful-shutdown hardening |
| Final 20-run gate | 20/20 runs; each 25 passed/1 POSIX skip; 500 pass executions, zero leaked fake/Codex processes |
| Windows full suite | 489 passed, 136 skipped |
| Linux full suite | 614 passed, 1 skipped in Python 3.13 container; the Windows-only updater module was explicitly excluded because the image has no PowerShell |
| Unfiltered Linux context | 616 passed, 1 skipped; seven pre-existing updater tests failed only because `powershell` is absent |
| Real Codex handshake | Native installed Codex 0.139.0 reached `ready=True`, generation 1, then closed with no live process |
| Wheel inspection | Contract manifest plus stable and v2 runtime schema assets present in the built wheel |
| Static/generation checks | Ruff, focused mypy, `compileall`, generator `--check`, and staged diff check passed |
| Independent review | PASS after fixing EOF-before-TERM shutdown and retaining exact runtime/schema version binding |
| Commit | `19cad27` |

The HA profile constructs and owns exactly one app-server client through FastAPI lifespan. The transport rejects unlocked directions, malformed or oversized JSONL, invalid payload/results, stale generations, response-ID misuse, callback overload, and runtime/schema version mismatches without emitting raw server content. The remaining direct legacy auth, model, and run consumers are intentionally migrated in Tasks 6, 7, and 10; Task 5 does not claim repository-wide single-process ownership yet.

## Task 6 — structured ChatGPT account flow

| Evidence | Result |
|----------|--------|
| RED contracts | Coordinator, HA lifecycle/routes, and shared-client account/limits suites failed at the missing production boundaries |
| Auth protocol | Exact `account/read`, `account/login/start` with `chatgptDeviceCode`, cancel, logout, completion, and account-update handling |
| Race/recovery coverage | Operation revision, app-server generation and `loginId` correlation, cancel-during-start, concurrent polls, sparse updates, restart recovery, close/late-event rejection, and authoritative logout read |
| Privacy boundary | HA account projection retains only safe auth/plan fields; raw errors, email, IDs, tokens, auth files, and private backend calls are excluded |
| Lifecycle/API | One app-server owns the coordinator and safe probes; reverse shutdown is exception-safe; cancel plus typed 409/503 responses are bearer protected |
| Focused GREEN | 148 passed before final reentrant-listener addition; independent final review also passed 95 focused tests |
| Windows full suite | 570 passed, 136 skipped |
| Linux full suite | 695 passed, 1 skipped in Python 3.13 container with the Windows-only updater module excluded |
| Static verification | Ruff, focused mypy, `compileall`, and `git diff --check` passed |
| Independent review | APPROVED after startup retry, sparse-update, logout-authority, restart-poll, concurrent-status, and bounded-close fixes |
| Commit | `0480f38` |

The external profile retains its deprecated CLI parser, auth-file account probe, and token-backed limit probe for the 0.6.x rollback window. HA mode constructs none of those credential owners. The Task 6 auth/run mutual-exclusion acceptance remains open only until Task 7 installs the shared runtime lease; a status-based compatibility check was deliberately not added.

## Task 7 — global runtime gate, app-server turns, approvals, and questions

| Evidence | Result |
|----------|--------|
| Global ownership | One active turn, eight queued prompts, and auth mutation exclusion are enforced by one immutable `RuntimeGate` |
| Structured runtime | HA prompt, steer, interrupt, thread start/resume, turn start, and terminal handling use the locked app-server protocol; HA app composition rejects the legacy `BridgeRunner` owner |
| Prompt ownership | Each chat can own at most one queued prompt; a second submission fails with retryable `thread_prompt_pending`, while the immutable global gate still caps the queue at eight |
| Mode and path policy | Observe is read-only/on-request; Edit is workspace-write/on-request; Full auto is workspace-write/never; all deny network access, require exact nested sandbox/workspace metadata, and reject nonportable, private, or outside-workspace approval paths |
| Interaction boundary | Command/file approvals and `request_user_input` are bearer protected, strictly correlated, redacted, expiring, idempotent, and never expose provider request tokens or private paths |
| Opaque command boundary | Schema-valid command requests without parsed `commandActions` are declined; only contained, parsed actions are surfaced until Task 21 proves target sandbox confinement |
| Callback ordering | Pre-response notifications and server requests drain FIFO; callbacks arriving during replay append behind the active batch, and early `serverRequest/resolved` notifications retain generation/request ownership until the interaction is created and expired |
| Steer uncertainty | A timeout or mismatched steer response persists `steer_outcome_unknown`, rejects replay of the same request ID, aborts the owning app-server generation, and clears queued work |
| Deletion/storage atomicity | Active, queued, interacting, or publishing chats block deletion; project/thread create and metadata mutations serialize with deletion; runtime history is persisted before public metadata removal, and injected checkpoint failures preserve that metadata |
| Failure/recovery | Active and queued work is interrupted on cold restart; pending interactions expire; claimed responses recover as `outcome_unknown`; corrupt v1 checkpoints are quarantined and thread projections repaired |
| Bounded state | Terminal runs/interactions compact to fixed recent windows; request tombstones are capped at 50,000 and fail closed before accepting more work; checkpoint writes are private, validated, atomic, and fsynced on POSIX |
| Race/adversarial coverage | Concurrent duplicate submissions, cancellation, stale generations, randomized callback reordering, blocked/failed response writes, provider resolution, deletion/mutation, and cross-thread decisions are covered |
| Process termination | POSIX test proves a SIGTERM-resistant app-server parent and child process group are force-killed before a clean next generation |
| Focused GREEN | 251 passed, 6 skipped |
| Windows full suite | 723 passed, 139 skipped on Python 3.14.4 |
| Linux full suite | 851 passed, 1 skipped, and 1 Starlette deprecation warning in the Python 3.13 container; the Windows-only updater test was excluded |
| Static verification | Changed-file Ruff, `compileall`, protocol validators, and `git diff --check` passed |
| Independent review | Protocol payload review passed; final broker/storage review found no remaining ordering or cleanup blocker after exact sandbox metadata, opaque-command denial, callback replay ownership, deletion lock ordering, legacy-owner rejection, and seeded reorder fixes |
| Implementation commit | `6583d9f` (`Broker Codex turns and approvals`) |

Task 7 provides in-process notification dedupe and truthful restart interruption;
it does not claim cross-file crash atomicity between runtime JSON and thread JSONL.
Task 8 owns that SQLite outbox contract. Attachment-backed turns deliberately
return `runtime_attachments_not_ready` until Task 9 supplies a checksum-bound
transport that cannot leak private copies into a user workspace or source
control. Real filesystem/network sandbox enforcement remains a protected-HA
acceptance condition, and the VM rollback path is unchanged.

## Task 8 — durable global event journal and replay

| Evidence | Result |
|----------|--------|
| Global replay contract | One SQLite WAL journal assigns monotonic cursors across auth, runtime, and thread scopes; replay and bounded wait support filters, heartbeats, batch ceilings, and snapshot-guided 410 recovery |
| State/event atomicity | A durable outbox prepares complete JSON writes and events, atomically replaces/fsyncs canonical state, then uniquely finalizes journal events; startup and pre-commit reconciliation finish pending operations exactly once |
| Crash and ordering coverage | Injected crashes after prepare, after state replace, and before event append recover once; newer revisions cannot overtake an older pending operation |
| Retention and capacity | Per-scope count/byte retention, global physical journal limits, bounded waiters, bounded operation metadata/tombstones, deterministic 413/429/507 responses, and fail-closed retired idempotency keys |
| Privacy boundary | Device codes/raw auth output, private workspace/stored paths, raw provider `codex.event` payloads, and unsafe legacy failure fields are projected out before hashing, sizing, SQLite persistence, or replay |
| Compatibility | The list-shaped v0 per-thread adapter remains; legacy JSONL imports are idempotent and safely projected; the deprecated external runner now pairs canonical lifecycle state with its events through the same outbox |
| Focused verification | 322 passed, 66 skipped across journal, outbox, API, runtime, runner, auth, storage, and workspace suites |
| Windows full suite | 814 passed, 141 skipped |
| Linux full suite | 944 passed, 1 skipped, and 1 known Starlette TestClient deprecation warning in a Python 3.13 container; the Windows-only updater test was excluded |
| Static verification | Ruff, `compileall`, panel JavaScript syntax, staged diff check, and final diff hygiene passed |
| Independent review | Luna and Terra found no remaining P0/P1 after event projection, capacity-envelope, legacy atomicity, notification-restart, and pending-operation ordering fixes |
| Implementation commit | `c1f2307` (`Add durable Bridge event journal`) |

The journal is private Bridge state, not a Home Assistant entity, event-bus, or
logbook payload. The future Integration may forward only these normalized
records over administrator-authorized HA WebSockets. Task 9 owns binary
transport; Task 10 and later own the Integration consumer and panel resume flow.

## Task 9 — resumable private uploads and ranged artifacts

| Evidence | Result |
|----------|--------|
| Upload protocol | HA-only API v1 supports create/status, fixed 8 MiB ordered chunks, idempotent same-index retry, completion, and cancellation; external v0 alone retains multipart compatibility |
| Integrity and recovery | Per-chunk and final SHA-256, strict manifests, descriptor-rooted atomic writes, exact-inode cleanup, restart resume, durable publishing reconciliation, completed-tombstone cleanup, and recoverable session-owned final assembly |
| Concurrency and capacity | Upload/thread lock ordering covers deletion races; live-part leases protect unlocked request streams; private quota/free-space checks, 64 KiB metadata ingress, strict path/depth limits, bounded sessions/tombstones, and safe orphan reaping prevent byte and inode bypasses |
| Runtime privacy | Completed uploads remain private and available-but-unselected; text-only turns select no attachments, and no unsupported generic app-server input or automatic workspace copy is introduced |
| Artifact downloads | One byte range, 206/416, strong SHA-256 ETag, If-Range, bounded 1 MiB descriptor-pinned iteration, and forced sanitized attachment/octet-stream/nosniff/no-store headers |
| Focused verification | Final Linux upload suite 28 passed; broader Task 9 transport/storage regression passed after the review fixes |
| Windows full suite | 816 passed, 172 skipped on Python 3.14.4 |
| Linux full suite | 977 passed, 1 skipped, and 1 known Starlette TestClient deprecation warning in a Python 3.13 container; the Windows-only updater test was excluded |
| Memory smoke | A 32 MiB four-chunk upload plus final assembly peaked at 2.04 MiB traced Python memory and increased process RSS by 2.00 MiB |
| Static verification | Repository Ruff, `compileall`, staged diff check, and final diff hygiene passed |
| Independent review | Luna spec/security and Terra quality passes found no remaining P0/P1/P2 after recovery, quota, deletion, live-stream reaper, manifest strictness, and platform-construction fixes |
| Implementation commit | `ed9e6f9` (`Add resumable Bridge file transport`) |

The Bridge-only transport does not claim that generic files are representable by
the current locked Codex app-server schema. Tasks 10-17 own truthful capability
discovery, explicit attachment selection, bounded text/image representations,
consented workspace import, Integration forwarding, and the panel experience.

## Task 10 — shared app-server lifecycle and truthful readiness

| Evidence | Result |
|----------|--------|
| Single HA runtime owner | Production HA composition creates one `CodexAppServerClient`; its catalogue, account, limits, auth coordinator, and `RuntimeBroker` all share that identity. External legacy mode alone retains subprocess probes and `BridgeRunner`. |
| Dynamic model catalogue | Shared `config/read` plus paged `model/list` discovery uses one total deadline, a 100-page ceiling, generation-aware caching, configured timeout/TTL values, last-known-good fallback, and stable redacted errors. |
| Direct-chat recovery | A stale fallback direct project is reconciled from the recovered shared catalogue before the first new direct chat, so provisional defaults are never materialized as permanent per-chat overrides. |
| Lifecycle and recovery | Startup, normal shutdown, partial-start failure, generation interruption, active/queued cleanup, rate-limit cache invalidation, and reverse owner close order are covered. Initial known app-server failures keep the bearer-protected fatal diagnostic surface alive. |
| Readiness and admission | HA reports `ready`, `auth_required`, `degraded_catalogue`, or redacted `fatal` reasons. Fatal runtime/version/sandbox state returns 503 for prompts; expired auth returns 409; stale catalogue alone does not block text prompts. |
| Version and diagnostics | The initialized app-server user agent supplies a validated live Codex semver; HA diagnostics compare it with the immutable build version while redacting executable paths, repository metadata, tool versions, and raw run errors. |
| Bounded health reads | Catalogue, account projection, rate limits, and auth reconciliation use explicit five-second request bounds rather than the transport's longer general default. |
| Sandbox honesty | Task 10 accepts only literal `sandbox_ready=True` from a trusted future verifier. The production branch therefore stays fatal until Task 21 installs and proves the real AppArmor/bubblewrap filesystem/network self-test; no environment flag or unsafe bypass fabricates readiness. |
| Focused verification | Final independent focused run: 151 passed, 3 platform skips. |
| Windows verification | Full suite: 832 passed, 175 skipped on Python 3.14.4; the subsequently hardened runtime-broker file passed 98/98. |
| Linux verification | All applicable Python 3.13 tests passed in two auditable shards: 898 passed/1 skipped outside the broker stress file, plus 98/98 broker tests. The two PowerShell-only updater files were excluded; one known Starlette TestClient deprecation warning remains. |
| Timing falsification | Loaded Linux runs exposed two pre-existing overlapping test deadlines. `f6faa5d` separates the cancellation, interaction, queue, and turn timers without changing broker production behavior; ten repeated cancellation runs plus both platform broker suites passed. |
| Static verification | Repository Ruff, `compileall`, and staged/final diff checks passed. |
| Independent review | Luna and Terra found no remaining P0/P1 after the production composition, bounded-read, sticky-startup-failure, and settings-propagation fixes; Terra granted the final seal. |
| Implementation commit | `d4c786d` (`Unify Codex runtime lifecycle`) |

Transport-level process crash/restart and broker-level generation reconciliation
remain separate deterministic tests rather than one timing-sensitive end-to-end
fixture. Together they prove stale requests cannot cross generations and active
or queued work is interrupted before the recovered runtime accepts new work.

## Task 11 — tested Home Assistant protocol client

| Evidence | Result |
|----------|--------|
| Protocol authority | Frozen API, discovery, readiness, and problem records validate only private App origins, strict Supervisor identity, opaque tokens, negotiated v1, and allowlisted recovery metadata. |
| Transport security | Authenticated readiness, `X-Codex-Bridge-Api`, redirect refusal, bounded connect/pool/read/write/total timing, validated path segments, bounded problem bodies, and suppressed upstream exception details. |
| Compatibility | Supervisor-discovered Apps require API v1; legacy v0 is explicit and capability-limited, with buffered file/event adapters unavailable after v1 negotiation. |
| Streaming ownership | Caller-owned response contexts close deterministically on success, failure, and cancellation; bounded reads and iteration map timeout, incomplete-read, and connection failures to redacted typed errors. |
| Focused verification | 59 passed on Linux with the Home Assistant plugin's socket/task/timer/thread cleanup; 59 passed on Windows with equivalent focused plugins. |
| Bridge regression | 996 passed, 1 skipped on Linux in the same pinned environment, excluding the PowerShell-only updater module; one known Starlette deprecation warning remains. |
| Harness isolation | Root pytest is Integration-only. The deprecated external `BridgeRunner` suite uses its own pytest lifecycle because immediate cancellation can leave a daemon worker briefly draining; HA application composition rejects that owner. |
| Static verification | Ruff, `compileall`, staged diff check, and final diff hygiene passed. |
| Independent review | Luna found no remaining security or lifecycle blocker after typed error-body timeouts, incomplete-stream mapping, fail-closed renegotiation, and endpoint traceback suppression. |
| Implementation commit | `c3749bf` (`Add tested Home Assistant Bridge client`) |

Task 11 does not register Supervisor discovery or expose v1 event/file routes
through Home Assistant. Those consumers remain deliberately gated until Tasks
12–14 establish stable entry identity, one event broker, and bounded streaming
views.

## Task 12 — Supervisor discovery and stable Integration identity

| Evidence | Result |
|----------|--------|
| Primary setup | HA 2026.7 `HassioServiceInfo` wrapper identity supplies authoritative slug/UUID; App config supplies only validated private host, port, token, and API range. No endpoint or token field appears in the App flow. |
| Administrator consent | A newly discovered or external-to-App replacement remains pending until explicit administrator confirmation, and authenticated readiness is revalidated at confirmation time. |
| Stable recovery | Supervisor UUID is the config-entry unique ID; changed host/token data is authenticated, replaced atomically, and reloaded only when changed. Old and new token sentinels remain absent from logs. |
| Single-entry invariant | External v0 and Supervisor v1 modes cannot coexist ambiguously. A confirmed App may replace the one external entry; another Supervisor instance and a second active runtime fail closed. |
| Compatibility | Supervisor mode requires negotiated API v1. The explicit **External Bridge (advanced)** path requires negotiated legacy v0 and remains the rollback route. |
| Lifecycle | Runtime close executes on unload and partial setup failure. HTTP views and WebSocket commands register once per HA process; the panel is removed/re-added with active entry lifecycle. |
| Focused verification | 23 passed on Linux with the Home Assistant custom-component plugin. |
| Full Integration verification | 82 passed on Linux with no socket/task/timer/thread cleanup failures. |
| Static verification | Ruff, `compileall`, JSON/localisation equivalence, manifest assertions, staged diff check, and final diff hygiene passed. |
| Independent review | Terra implemented the slice; Luna sealed the final confirmation, single-entry, rollback, revalidation, v1/v0, and registration behavior with no remaining finding. |
| Implementation commit | `e730c36` (`Discover the Codex Bridge App automatically`) |

The local dependency harness exercises real HA flow classes directly because it
does not ship `hass_frontend`; the complete flow-manager/install sequence remains
a target-system acceptance item in Task 22. Task 13 now owns the one-consumer
event lifecycle that will attach to `CodexBridgeRuntime.async_close()`.

## Task 13 — one Integration event broker and HA WebSocket fan-out

| Evidence | Result |
|----------|--------|
| Single consumer | Each API v1 config entry owns one HA-tracked replay/wait task; multiple authenticated panel subscriptions fan out from that task, and unload or partial setup cancels it deterministically. |
| Replay and exactly-once | A persisted global cursor, bounded local history, replay-before-live ordering, monotonic dedupe, and compacted-journal snapshots recover proxy reconnects without silently skipping events. |
| Scope and zero-chat auth | Auth, runtime, and selected-thread filters follow the Bridge contract; auth events stream without a project or chat, while explicit contradictory filters fail before any upstream request. |
| Resource bounds | Event responses are capped at 8 MiB and 256 records; subscriber queues and replay history have count and byte ceilings; slow clients receive a cursor-bearing snapshot rather than an unbounded queue or silent close. |
| Recovery status | Heartbeats, capped exponential reconnect, retryable connection recovery, terminal auth/protocol/upstream states, and a safe `get_event_status` diagnostic expose no raw exception or private Bridge origin. |
| Admin actions | HA-admin-only WebSocket commands cover event replay/subscription, auth cancel, pending approvals/questions, correlated decision/answer responses, and prompt idempotency keys. User-input answers are strictly bounded and match the Bridge `question_id`/`values` contract. |
| Compatibility | External v0 alone retains per-thread polling. The retiring panel receives v1 thread projections, advances on snapshot cursors, refreshes after overflow/compaction, and falls back to polling after a terminal live-stream error. |
| Privacy | No prompt, response, event payload, token, device code, filename, or auth claim is written to HA state, the event bus, logbook, or routine logs; only the cursor is persisted in HA storage. |
| Focused verification | 68 passed on Linux across the API client, event broker, WebSocket surface, and Integration lifecycle. |
| Full Integration verification | 116 passed on Linux with HA socket/task/timer/thread cleanup enabled. |
| Static verification | Ruff, `compileall`, panel `node --check`, staged/final diff checks, and JavaScript cache-version bump passed. |
| Independent review | Luna and Terra clean-sealed replay races, memory bounds, slow-client recovery, compacted-journal behavior, task ownership, native HA subscription cleanup, and legacy/v1 compatibility with no remaining P0–P2 issue. |
| Implementation commit | `60e08fe` (`Stream Bridge events through Home Assistant`) |

The event broker persists only its monotonic cursor. Durable events and snapshots
remain Bridge-owned, and file bytes remain outside the WebSocket path. Task 14
now owns authenticated, resumable, bounded HA HTTP forwarding.

## Task 14 — authenticated resumable HA file streaming

| Evidence | Result |
|----------|--------|
| HA authorization | Every create/status/chunk/complete/cancel/download view requires an authenticated HA administrator before reading a request body; the browser credential is never forwarded and the private Bridge bearer remains client-owned. |
| Resumable uploads | API v1 exposes create, durable status, ordered 8 MiB chunks, idempotent retry, completion, and cancel. Metadata is capped at 64 KiB, upload/file identifiers and checksums are validated before network access, and oversized chunks fail locally with 413. |
| Request streaming | Binary request bodies move from `request.content` to aiohttp in 64 KiB blocks with exact declared-length enforcement, no `NamedTemporaryFile`, and cancellation propagated through the owned upstream request. External v0 multipart is forwarded intact under an explicit 101 MiB compatibility ceiling. |
| Ranged downloads | Full, 206, and 416 responses preserve validated `Range`/`If-Range`, strong ETag and content-range metadata while streaming with backpressure. Exact `Content-Length` is enforced and post-header failures abort the partial connection instead of attempting a second response. |
| Header safety | Only validated end-to-end length/range/ETag metadata survives. Downloads force attachment, `application/octet-stream`, `nosniff`, and private no-store/no-transform; hop-by-hop, cookie, content-type, CRLF, traversal, and malformed filename values are dropped or replaced safely. |
| Compatibility | External v0 multipart and artifact payloads now stream through HA without temp files or whole-artifact buffering. Its artifact list is bounded to 8 MiB before JSON decoding. The browser-side v1 resumable consumer is the explicit Task 15 integration gate before release. |
| Resource smoke | A 100 MiB, thirteen-chunk transfer through the local HA test server stayed under 24 MiB traced Python growth and 64 MiB sampled RSS growth, wrote no temporary file, and preserved the exact byte count. |
| Focused verification | 41 HTTP transport tests passed; the combined HTTP/API client slice passed 71 tests on Linux. |
| Full Integration verification | 157 passed on Linux with HA socket/task/timer/thread cleanup enabled. |
| Bridge contract verification | 66 upload, artifact, ingress-limit, and HA security contracts passed; the sole warning is the existing Starlette TestClient deprecation. |
| Static verification | Ruff, `compileall`, staged/final diff checks, and exact admin-surface AST assertions passed. |
| Independent review | Luna and Terra clean-sealed request/response ownership, cancellation, memory/disk ceilings, range semantics, header injection, token isolation, legacy bounds, and post-header failure behavior with no remaining P0–P2 finding. |
| Implementation commit | `72b7454` (`Stream files through Home Assistant`) |

Task 14 establishes the private binary transport but deliberately does not ship
an intermediate release. Task 15 must make the browser consume the resumable v1
routes before the HA-native application can pass end-to-end acceptance.

## 0.6.6 release preparation

The release being shipped is Integration `0.6.6`, App `0.6.6`, Bridge `0.5.5`,
and Codex `0.144.4`. It includes the Codex-style panel refinement: a clean left
navigation tree, title-first chat rows, one action menu, correct archive
collapse/search and search icon, 44px mobile targets, transcript-adjacent
decisions, and collapsed mobile settings/limits. Publication, signing, and target-Home-Assistant
acceptance are pending. No `0.6.6` image digest or live-acceptance result is
recorded here; the following `0.6.5` evidence is historical only.

## Live Integration 0.6.5 / App 0.6.5 acceptance

| Evidence | Result |
| --- | --- |
| Live root-cause probe | A deliberately invalid, non-secret diagnostic token against the advertised App hostname returned `cannot_connect`, not `invalid_auth`, proving Core could not reach the hostname before credentials were evaluated. |
| App publication | The App uses the Supervisor-assigned private IP from the Bashio helper present in the exact pinned base image, validates it against RFC1918/ULA networks, preserves the Supervisor UUID, and supplies a fresh 32-hex non-secret `publication_id` per start. |
| Integration confinement | Supervisor discovery now requires the same literal private App-IP policy before constructing or authenticating a Bridge client; loopback, link-local, public, documentation, and hostname targets fail closed. |
| Recovery UX | A valid but temporarily unreachable discovery remains on the `hassio_confirm` form with `cannot_connect`; retries revalidate authenticated readiness, and no new/replacement config-entry data is written before success. |
| Panel UX | Transport failures receive an accessible retry/dismiss surface; local validation and sign-in guidance remain dismiss-only; retryable prompt state is visible at desktop and mobile widths. |
| Current release metadata | `app_config:rw` replaces the legacy map name. App `0.6.5`, Integration `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4` projections are synchronized. The signed immutable App `0.6.5` image digest is `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`. |
| Dependency policy | `httpx>=0.28.1` and Docker login-action updates merged independently; App build tools consolidate at supported `build==1.5.0`, `setuptools==83.0.0`, and `wheel==0.47.0`. Yanked `build==1.5.1` is rejected. Duplicate App pip ownership is removed, and pytest `>=9.1.0` is ignored while the pinned HA test plugin requires `pytest==9.0.3`. |
| Full Integration | 170 passed in the pinned Home Assistant 2026.7.2 Linux test environment. |
| Full Bridge | 1092 passed, 188 platform skips in the isolated pytest lifecycle. |
| Frontend | ESLint passed; 142 unit tests and 11 Playwright flows passed; the generated bundle rebuilt byte-identically and passed `node --check`. |
| App/release | 100 passed, 3 platform skips; a second protocol/App security slice passed 78 with 1 platform skip; Ruff, compileall, release projection sync, deterministic hash lock, and hash-locked dry-run installation passed. |
| Production image | The signed immutable amd64 App `0.6.5` image is identified by digest `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`; its runtime reports App `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4`. |
| Independent review | Review found and closed the consumer-side private-IP trust-boundary gap; no remaining correctness, deduplication, Bashio compatibility, retry, or credential/log-safety finding remained. |
| GitHub publication | Integration/App release `0.6.5` is published, not a draft or prerelease. Its CI and signed App release workflow passed. |
| Target Home Assistant | Home Assistant ran exactly one installed/running Codex Bridge App. The live runtime strip reported App `0.6.5`, Integration `0.6.5`, Bridge `0.5.5`, and Codex `0.144.4`; Start on boot, Watchdog, and Auto update were enabled. The retained `0.6.4` backup was available. |
| Account and catalogue | ChatGPT device approval completed and the panel reported a connected Pro account. After live discovery was unavailable, the dynamic bundled catalogue exposed GPT-5.6 Sol/Terra/Luna. Sol exposed low/medium/high/xhigh/max/ultra; Terra exposed the same levels; Luna exposed low/medium/high/xhigh/max. |
| Limits and chat | The live account rendered the disabled five-hour window as `Off` and the weekly window separately. The exact reply `HA bridge 0.6.5 smoke test passed.` completed successfully. No Bridge request failed. |
| Panel and restart recovery | The compact Codex-style sidebar was live. After an explicit App restart, the ChatGPT Pro session survived. A normal restart can briefly show a provisional disconnected state while discovery is republished, then recovers after about 15 seconds or a refresh. |
| Superseded pre-release behavior | Full, live, and poll refreshes deferred only a typed `reservation_conflict` while the authoritative thread was queued, running, or cancelling, retained the previous artifact snapshot, and continued refreshing transcript/status. This narrower behavior was superseded after live acceptance showed that another chat can reserve the same project workspace while the selected chat is idle. |
| Dependency notifications | All supported Dependabot ecosystems now feed one weekly maintenance group. Legacy PRs `#8`–`#14` were closed, grouped PR `#29` merged with green CI, and no dependency PR remains open. Security fixes remain enabled separately. |

## Live release matrix: Integration 0.6.5 / App 0.6.5 / Bridge 0.5.5 / Codex 0.144.4

| Release scope | Status |
| --- | --- |
| Release state | Published and live-accepted within the boundaries recorded above. The signed immutable App image digest is `sha256:d0bb3954f535324f174189f06a0256169dc08464897c64b4f5b5ffd99bfe5f60`. |
| Catalogue recovery | Live app-server discovery remains primary. On failure, a verified fresh catalogue is retained as stale last-known-good; otherwise the installed Codex `debug models --bundled` catalogue is read dynamically; only then is the static fallback used. Stale results retry after 15 seconds. |
| Runtime-derived options | The release has no hardcoded GPT-5.6 or reasoning-level release list. GPT-5.6 and model-specific Max/Ultra were presented from the dynamically read bundled catalogue after live discovery was unavailable. |
| Panel behavior | The release uses a compact Codex-style sidebar while retaining Home Assistant theme and accessibility behavior. A typed transient artifact `reservation_conflict` preserves the prior artifact snapshot and avoids a false connection error even when the selected chat is idle; unrelated artifact failures remain visible. |
| Boundary unchanged | Browser and remote-proxy traffic remains terminated at Home Assistant. Nabu Casa, Cloudflare, VPN, and other HTTPS reverse proxies must not expose or forward a browser directly to the private App or Bridge. |

## 0.7.0 release and bounded target-HA evidence

The published matrix is Integration/App/panel `0.7.0`, Bridge `0.6.0`, and
bundled Codex `0.144.4`. The signed generic image digest is
`sha256:04e0cd5f805e4f0f587ebdfa6c3e6f7516f6650c444850a59d7e5765930d31ea`;
the amd64 child is
`sha256:7d60cb8c7bfe696f6432fb9b744434ca63ca8f8f92724ab580aa1dbf32addfcc`.
Main CI run `29471288344` and publication run `29471288457` succeeded, and the
release carries signature, SBOM, and provenance attestations ([release
page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.0)).
The local checks below remain source/package evidence; the target-HA rows record
bounded live observations rather than blanket acceptance of every capability.

| Evidence | Result |
| --- | --- |
| Full Bridge | 1195 passed, 189 platform skips in 198.32 seconds. |
| Integration capability slice | 107 passed across the Bridge client, protocol, administrator WebSocket surface, and automation scheduler. The full Home Assistant plugin requires Linux because Home Assistant 2026.7 imports `fcntl`; CI remains the full Integration gate. |
| Frontend | ESLint passed; 213 unit tests passed; the generated bundle rebuilt and passed `node --check`; all 12 Playwright flows passed, including device login, mobile navigation, decisions, reconnect, hostile content, and axe checks. |
| Documentation and App package | 94 passed, 2 platform skips across repository documentation, release synchronization, App package, build-context, and Codex-lock contracts. |
| Static checks | Changed-file Ruff format/check, `compileall`, and `git diff --check` passed. |
| Release projections | `sync_app_release.py --check` reports App `0.7.0`, Bridge `0.6.0`, Codex `0.144.4`; the verified Codex lock check passed. |
| Reproducible App context | The amd64 context staged successfully with the locked build/runtime dependencies and Bridge `0.6.0` wheel. |
| Local App image | Docker built the staged Home Assistant base image successfully. Inside that image `codex --version` reported `0.144.4`, and the locked app-server contract generator `--check` passed against that exact binary. |
| Independent release review | Pre-release review found and closed the MCP capability-gate bypass, pre-service MCP activation, masked durable MCP cleanup, unlinked and terminal-before-link automation crash windows, retryable/post-mutation/post-claim scheduler gaps, root-workspace skill path, best-effort DNS screening gaps, mobile management-route trap, stale mutation refresh, desktop-error privacy leak, keyboard gaps, hidden-overlay hit testing, destructive-button contrast, and project-instruction scope/OAuth popup regressions. The final backend re-review was READY after 115 focused tests; the final frontend re-review was READY after 213 unit tests. |
| Target Home Assistant | App and Integration `0.7.0` reported Bridge `0.6.0` and Codex `0.144.4`; ChatGPT Pro remained signed in, dynamic GPT-5.6 was visible, the five-hour window rendered `Off`, and chat/history were preserved. App auto-update and MCP opt-in persistence after restart were observed. |
| Management mutation boundary (historical 0.7.0 observation) | Forms lost unsaved values during a background rerender. The `0.7.1` candidate contained the fix; the retest was open at the time of this `0.7.0` record. |
| Recovery gates (historical 0.7.0 observation) | The first unattended App update was proven. External blocked-network/Nabu Casa/Cloudflare routing, cold restore, and previous-image rollback remained unproven. |

The remaining acceptance work recorded for this `0.7.0` section was the
`0.7.1` management-mutation retest and the external blocked-network,
cold-restore, and previous-image recovery gates. The retest is recorded below;
the recovery gates remain open.

## 0.7.1 published release and bounded target-HA evidence

Integration/App `0.7.1` is published and live-accepted on the target Home
Assistant within the boundaries below. The release uses generic image digest
`sha256:ec4e5f4ea48ba2333d5689879bc98a58912ae15ac9f90a133d30712452403184`
and amd64 child digest
`sha256:cacfb7b4a65a1b0290fe5c7da9dfa33c5ffde78f8ebaa3370fac9366c19681a6`.
Main CI rerun `29483810669` and App publication `29483810926` succeeded
([release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.1)).

| Evidence | Result |
| --- | --- |
| Target runtime | App and Integration `0.7.1` installed/running; Bridge `0.6.0`; Codex `0.144.4`. |
| Account and models | ChatGPT Pro remained signed in; GPT-5.6 was visible from runtime discovery; the five-hour window rendered `Off`; existing chats/history were preserved. |
| Scheduled form | Draft values survived background rerenders. |
| Skills form and mutations | Draft values survived rerenders; create/list/delete passed. |
| MCP form | Draft values survived rerenders; cancellation passed. |
| One-time Observe automation | Claimed exactly at `2026-07-16T09:09:30Z`; completed at `09:09:35Z`; then paused and deleted. |
| Plugins/marketplaces (historical 0.7.1 observation) | Live list returned `capabilities_unavailable` (HTTP 503). No `0.7.1` plugin or marketplace list/mutation acceptance was claimed. |
| Update/recovery | First unattended App auto-update remains proven. This manual update retained the prior-version backup. External blocked-network/Nabu Casa/Cloudflare routing, cold restore, and arbitrary previous-image rollback remain unproven. |

## 0.7.2 published plugin catalogue evidence

App/Integration `0.7.2` with Bridge `0.6.1` was published and signed, but was
not target-HA accepted before `0.7.3` superseded it; target-HA-accepted `0.7.1`
remains the historical live baseline. The `0.7.2` generic image digest is
`sha256:6d2622bfbf2f1ce50611a4b2b0f72b9f682d0ad6e6619ed84c06d3d74fd462bd`
with amd64 child
`sha256:8e70abea7f98037c805d5163601a0d4a3045e3d54a83f27ee36af64072fe56f0`;
main CI `29491849347` and App publication `29491849502` succeeded
([release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.7.2)).
Bundled Codex remains `0.144.4`. In a signed-in Codex run, the native plugin
catalogue measured approximately `4,041,499` bytes, contained `1,916` plugins,
and completed cold in `35.887s`.

| Candidate fix or measurement | Result |
| --- | --- |
| App-server framing/request | Message bounded to `8MiB`; cold catalogue request bounded to `60s`. |
| HA Integration plugin request | Plugin catalogue request gets a `75s` deadline and `8MiB` response cap. |
| Bridge projection | Projects at most `4,096` plugins, covering the measured `1,916`-plugin catalogue. |
| Frontend request shape | Plugins and marketplaces load through one frontend request. |
| Acceptance boundary | These are candidate facts only. The historical `0.7.1` live list returned `capabilities_unavailable` (HTTP 503); no `0.7.1` plugin or marketplace list/mutation acceptance was claimed. |

## Evidence status through 0.7.3

The historical `0.6.5` section remains bounded live-acceptance evidence for that
matrix, and the `0.7.0` section is retained as the prior published baseline.
The `0.7.1` publication, digests, target-HA runtime versions, ChatGPT Pro login
retention, dynamic catalogue, duration-aware limits, chat/history, management
form retention, skill mutations, scheduled Observe run, App auto-update, and
MCP form cancellation are evidenced above. The `0.7.1` plugin/marketplace HTTP
503 remains historical evidence; the `0.7.2` catalogue measurements and fixes
are published evidence without target-HA acceptance. External blocked-network/Nabu
Casa/Cloudflare routing, cold restore, and arbitrary previous-image rollback
remain unproven; the optional external Bridge remains a compatibility/recovery
path until then.

## 0.7.3 candidate: provider-gated web search and images

App/Integration `0.7.3` with Bridge `0.6.2` and bundled Codex `0.144.4` is a
candidate only, pending real Home Assistant acceptance. On the Supervisor path,
native web search defaults to Live for prompts and automation runs only after a
successful provider-capability advertisement. Model-controlled shell networking
remains disabled. Signed-in ChatGPT-account image generation requires both
`imageGeneration` and `namespaceTools`, uses no OpenAI API key, and retains only
private, bounded PNG/JPEG/WebP artifacts. The compact UI and updater
`jsonschema` dependency-installation fix are also candidate changes.

No target-HA acceptance, image digest, CI/publication run ID, or live feature
result is recorded for `0.7.3`. The earlier published-but-not-target-accepted
`0.7.2` catalogue section and the target-HA-accepted `0.7.1` evidence above
remain unchanged historical records.

## 0.7.5 published release and bounded target-HA evidence

App, Integration, and panel `0.7.5`, Bridge `0.6.3`, and Codex `0.144.5` were
installed and running on target Home Assistant `192.168.50.20` on 2026-07-16.
App publication run `29511116947` produced immutable digest
`sha256:6214ab4fa471f3356460c1c392e582981cd1b80ad2fc2173ddb925aaba6336d0`
with attestation `35670902`.

| Evidence | Result |
| --- | --- |
| Account and composer | ChatGPT Pro remained connected. A fresh direct chat defaulted to `gpt-5.6-sol` with `low` thinking. The catalogue exposed Sol, Terra, and Luna with the advertised Low, Medium, High, XHigh, Max, and Ultra levels; the compact composer showed five-hour `Off` and Week `60%`. |
| Native live search | The natural prompt `what is the weather in Malta like today` recorded `Searching the web` run activity and returned current live conditions. |
| Update retention | The App update retained automatic update and kept the prior-version backup. |
| Acceptance boundary | This run does not claim blocked-network/Nabu Casa/Cloudflare routing, cold restore, arbitrary previous-image rollback, image generation, plugins/marketplaces, or MCP acceptance. The historical `0.7.1` plugin/marketplace HTTP 503 result remains bounded historical evidence. |

## 0.8.1 publication and failed bounded target exercise

App, Integration, and panel `0.8.1`, Bridge `0.7.1`, and Codex `0.144.5` were
published and installed on the target Home Assistant. Publication run
`29527193037` produced generic digest
`sha256:2df98ca0452262a8336b82ec4842ba681c49b44c22a28983a7a10b3d9692e8a2`
and amd64 payload digest
`sha256:83074645bb03000884e5b13e05501899929dd41c99ef1aa228fccb636adae537`;
signature, SBOM, and exact-main provenance verification passed. This is not an
accepted release because the required PDF path failed.

| Evidence or acceptance item | Status |
| --- | --- |
| Installation and retained state | App and Integration `0.8.1` installed; Bridge `0.7.1` and Codex `0.144.5` reported. ChatGPT Pro, existing projects/chats, automatic App update, start-on-boot, watchdog, and prior-version backup were retained. |
| Models and usage | Runtime discovery exposed GPT-5.6 Sol/Terra/Luna and the advertised reasoning levels; five-hour usage rendered `Off`. |
| Artifact failure | The selected Test workspace contained only ordinary `.agents/skills` directories and a 622-byte PDF. The aggregate workspace root separately contained stale sandbox-test debris, including root-owned unreadable entries. Every artifact list failed before the selected workspace manifest and `0.8.1` misclassified the operational aggregate scan as HTTP 400 unsafe selected-workspace content. |
| Acceptance result | Failed/pending. PDF indexing, archive, and preview did not pass; no 0.8.1 target-acceptance claim is made. |
| Browser automation | Issue #43 tracks the required secure App-owned browser worker. Per ADR 0006, interactive Chromium remains deferred pending separate sandbox and enforced-egress acceptance. |

## 0.8.2 signed publication and bounded target smoke

App, Integration, and panel `0.8.2`, Bridge `0.7.2`, and Codex `0.144.5` are a
signed release. Publication run `29536061100` completed successfully against
exact main commit `ad65759032e859c309d06fee309a0e436f50dbe6`. The target Home
Assistant then reported App, Integration, and panel `0.8.2`, Bridge `0.7.2`,
and Codex `0.144.5`; ChatGPT/history remained present and the panel loaded
without the prior false global connection banner. This was a bounded smoke
check, not a completed PDF/archive/restore acceptance matrix, so `0.7.5`
remains the latest fully accepted release.

| Evidence or acceptance item | Status |
| --- | --- |
| Aggregate scan contract | Release restores non-capacity `WorkspaceBoundaryError` failures to typed, retryable `reservation_conflict` with resource `filesystem_scan`; capacity violations remain `quota_exceeded`. |
| Ordinary workspace regression | Release regression covers `.agents/skills/.../SKILL.md` plus a regular PDF and proves a retry succeeds after one operational aggregate scan failure. |
| Artifact error containment | Release keeps generic artifact-index and preview failures in the **Files** surface, preserving the authoritative transcript, chat state, and any real global connection/authentication error. |
| Desktop alignment | Release widens the desktop navigation/context rails at large viewports, retains a shared bounded conversation/composer axis, uses theme-derived navigation tint and a full-height structural context plane, moves healthy telemetry to System, and preserves narrow drawers, keyboard/touch actions, and reduced motion. |
| Focus mode | Release exposes standards fullscreen only from an explicit panel control, reflects fullscreen state, uses native Escape, and restores keyboard focus without changing the HA/Integration/App trust boundary. |
| Local verification | Clean Linux/Python 3.14 Bridge run: 1,450 passed and 13 skipped. Frontend: lint passed, 272 unit tests passed, generated bundle rebuilt, and 16/16 Playwright flows passed; release projections, Codex lock, Python compilation, Ruff, and diff checks passed. Independent re-review found no remaining release blocker in the requested scope. |
| Bounded target smoke | Coordinated versions, retained ChatGPT/history, and absence of the false global connection banner passed after the App restart. PDF indexing/archive/preview, cleanup of known acceptance debris, Focus mode, cold restore, and arbitrary previous-image recovery were not re-proved. |

## 0.8.3 signed publication and bounded target-HA acceptance

App, Integration, and panel `0.8.3`, Bridge `0.7.2`, and Codex `0.144.5` were
published from exact main commit `913c08d3393574f799baf0b47e78d31422c12fe1`.
Main CI `29544350904` and the signed App publication `29544351022` passed. The
immutable App digest is
`sha256:bd8c9b1e275e5f832a64d81d8aabb163c8f8d4e755ec317a6eeac530788741fa`;
[provenance attestation 35745773](https://github.com/Herbertmt978/HA_Codex_Bridge/attestations/35745773)
accompanies the
[0.8.3 release](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/0.8.3).

| Evidence or acceptance item | Status |
| --- | --- |
| Shared reading geometry | Header, transcript, safe live action, interactions, and compact composer use one 840-pixel content rail. The 330-360-pixel Activity card floats with the same desktop gutter at wide widths, then becomes an accessible right drawer at 1121-1480 pixels before it can compress the conversation. |
| Continuous transcript | Messages, run activity, approvals, and questions share one scroll surface rather than competing nested regions. |
| Activity information | Outputs, bounded Subagent totals, Background activity, Browser state, and Sources render as compact Codex-style sections. Terminal runs clear stale working counts while retaining completed and needs-attention totals. |
| Failure containment | Artifact-index, archive, and preview errors remain local to **Files** after authoritative thread/status/events succeed. A healthy response and real connection state are preserved; bounded retry remains available. |
| Run-state recovery | Bridge startup clears a busy thread projection that has no surviving private runtime owner, while preserving current nonterminal ownership. The panel treats an idle thread snapshot as authoritative, so orphaned item and message deltas cannot restore **Working**, **Preparing a response**, a Stop control, streaming state, or steer mode. |
| Responsive and accessible behavior | Mobile activity details expand inside the viewport; compact-desktop Activity uses a focus-contained drawer; keyboard tabs, disclosures, touch targets, reduced motion, and one-to-one tab/panel semantics are retained. Exact 880/881/1120/1121 boundaries are covered. |
| Local frontend verification | ESLint passed, 294/294 unit tests passed, the generated bundle rebuilt, and 17/17 Playwright flows passed including hostile-content containment, PDF preview, auth, approvals, retries, compact-width geometry, exact responsive boundaries, mobile bounds, and axe checks. |
| Local Bridge verification | The complete Bridge matrix passed with 1267 tests and 200 platform-gated skips. Focused recovery coverage proves missing-checkpoint cleanup, exact re-projection of a genuinely owned nonterminal run, and startup isolation from an unrelated malformed thread record. Ruff and diff checks also passed. |
| Target versions and retained state | On Home Assistant `192.168.50.20`, App and Integration `0.8.3` reported Bridge `0.7.2` and Codex `0.144.5`; ChatGPT Pro, projects, and chat history remained present. |
| Recovered stale run | The old `0.8.0 PDF acceptance` thread recovered from the false **Working / Preparing a response / Stop / steer** state to a truthful ready/Run completed state. |
| Normal prompt and catalogue | A fresh GPT-5.6-Sol prompt completed. Sol, Terra, and Luna plus advertised Max/Ultra reasoning levels were visible. The compact composer rendered five-hour usage as `Off` and showed the weekly window. |
| Native web search | The Malta weather prompt exposed `Searching the web` and `Using web search` before returning live conditions. Shell networking remained disabled. |
| Connection-state truthfulness | No false global **Connection issue** remained after the successful run, including after the secondary Files failure path. |
| Open acceptance boundaries | The current PDF artifact scan still returns the typed `409` local Files conflict, so PDF/archive/restore acceptance is not claimed. External Nabu Casa/Cloudflare routing, cold restore, arbitrary image rollback, and the secure App-owned browser worker remain unproven. |
| Release workflow follow-up | A manual paired HACS release gap was discovered. The paired-release workflow now waits for signed App publication and is policy-tested; its first live automatic exercise remains the next App release and is not claimed as part of this acceptance. |

## 0.8.4 acceptance-foundations candidate

App/Integration/panel `0.8.4` with Bridge `0.7.3` and Codex `0.144.5` is a
source candidate only. It has no release tag, immutable digest, publication
run, or target-HA acceptance at this checkpoint.

| Evidence or acceptance item | Status |
| --- | --- |
| PDF repair | Selected-workspace artifact listing and archive creation no longer depend on an aggregate workspace-root scan. Linux regressions prove a regular PDF remains available beside unrelated unreadable stale debris, while mutation quota uncertainty still fails closed. Root-side startup cleanup accepts only exact lowercase 32-hex self-test locators and restores ownership/mode after injected partial failures. Real target list/archive/preview/download remains pending. |
| Image generation | Provider capability negotiation, generation/revision authority, revocable publication leases, strict PNG/JPEG/WebP validation, idempotent private persistence, and sign-out race coverage pass. A deterministic blocked-validation test proves revocation wins without an artifact or `artifact.added` event. Real ChatGPT-account prompt -> Files preview -> download remains pending. |
| Remote transport | The Docker harness passes isolated LAN, Nabu-shaped, and Cloudflare-shaped profiles for prompt retry, distinct upload cancellation, exact 8 MiB chunk replay, WebSocket reconnect, trusted forwarded-header normalization, and artifact 206/416 resume. These are post-TLS-termination synthetic shapes; no real Nabu Casa or Cloudflare external capture is claimed. |
| Recovery evidence | Strict offline collectors validate redacted, descriptor-bound, distinct pre/post snapshots and never mutate Home Assistant. Successful output is labelled `evidence_format_validated` / `offline_snapshot_consistency`; destructive cold restore and retained-image recovery remain pending. |
| Browser worker | The high-level contract, correlated at-most-once dynamic-tool broker, policy proxy, redirect checks, bounded private artifacts, cancellation, and pinned worker scaffold are covered. Current HAOS denied the required user/network namespace and native Chromium sandbox path, while the parent App profile can read App-private data and open sockets. No attestation is created and `browser_v1` is not advertised. No extra privilege, host networking, or sandbox bypass is used. |
| Frontend | ESLint passed; 294 unit tests and 17 Playwright flows passed; deterministic generated-asset checks passed. |
| Python and Integration | Ruff, `compileall`, release sync, and Codex lock checks passed. The Linux Integration suite passed 318 tests. The decisive Python 3.14 Docker Bridge run passed 1,596 tests with 13 platform skips; the CI-like non-root focused rerun passed 19 with 1 skip. |
| Repository validation | The pinned hassfest image reports one valid Integration and zero invalid integrations. Its CI mount is now scoped to `custom_components`, preventing ignored local build manifests from being recursively misidentified as integrations. |
| Review | Independent final review reported no actionable correctness, security, or acceptance-claim finding. It confirmed descriptor-bound PDF reads, revocation-aware image publication, absent browser capability without attestation, and offline/redacted remote and recovery collectors. |

The release may publish these completed foundations, but it must keep the real
target PDF/image, authorized external transport, destructive recovery, and
browser isolation gates open until each has separate controlled evidence.

## 0.8.10 signed long-response release

App, Integration, and panel `0.8.10`, Bridge `0.7.5`, and Codex `0.144.5`
were published from exact main commit
`9fdfe53671d4773f3e955abb2720b408d874cd29`. Publication run
`29613120991` completed successfully and created the paired Integration
release. The immutable generic image digest is
`sha256:736250059793d068bec0bb94dceec582c1272b82b18d837158857d2ca946b4c0`;
provenance attestation `35904448`, signature verification, and the SBOM
attestation passed. This publication evidence does not by itself claim a live
5,000-word target-HA result.

## 0.8.11 account-neutral chat candidate

App/Integration/panel `0.8.11` with Bridge `0.7.6` and Codex `0.144.5` is a
source candidate at this checkpoint. It has no release tag, immutable digest,
publication run, or target-HA account-switch acceptance yet.

| Evidence or acceptance item | Status |
| --- | --- |
| Root cause | HA chats persisted a native `codex_thread_id` without recording which ChatGPT account owned it, so a new account could receive an invalid `thread/resume`. |
| Private identity boundary | The authoritative `account/read` email is normalized and HMAC-SHA-256 keyed by the existing private Bridge token. Only the opaque 64-hex marker reaches storage. Email, token, and marker remain absent from the panel, API, events, diagnostics, and logs. |
| Static local state | Account rebinding preserves chat/project IDs, titles, transcript events, attachments, artifacts, workspaces, model/thinking settings, archive state, and scheduled `continue_thread` targets. It clears only provider/runtime projection and adds one safe detach event. |
| Migration and compatibility | The first post-upgrade account observation detaches pre-0.8.11 unowned provider handles once. The same account later retains `thread/resume`; a different account makes the same HA chat use `thread/start`. Local history is visible but is not silently replayed to the new provider conversation. |
| Authoritative/fail-closed behavior | `account/updated` is only a hint and triggers a generation-checked `account/read` under the runtime gate. A newer hint invalidates an account read or active-login poll already in flight, preventing stale binding or a stale ready projection. An identity-less ChatGPT response detaches provider continuity and leaves prompt and automation admission auth-blocked. A notification or App-server generation change received during an active turn marks auth unavailable; reconciliation runs after the turn settles. |
| Queued admission fence | Every newly accepted prompt and every queued prompt promoted to active rechecks authoritative account admission. A prompt queued before an account change terminalizes locally as cancelled without any `thread/start`, `thread/resume`, or `turn/start`; its Home Assistant chat and durable user-message event remain visible. Exact idempotent readback remains available without starting provider work. |
| Active-steer admission fence | An interactive follow-up repeats provider admission after taking the broker lock and before writing idempotency, events, publication state, or `turn/steer`. A phased RED/green callback proves an account update raced after the pre-lock check rejects locally with no provider steer, local message, or request outcome while the existing run remains active. |
| Transient-state admission | Shared readiness and the broker's final callback require exactly `state == ok` and `auth_required == false`. An `account/updated` hint now publishes `checking`/auth-required before its authoritative read. A blocked-read lifecycle test proves direct prompts and scheduled continuations both fail before provider work or local target mutation; `logout_running` is fenced the same way. |
| Blocked-poll auth dedupe | When a turn owns the runtime gate, repeated status/ready reconciliation attempts compare the complete canonical unavailable projection and return the existing revision. Account-update and generation-change regressions prove only the first fail-closed state reaches the listener/durable event path, repeated polls do not grow it, and recovery still performs one fresh account read after the turn releases. |
| Atomic provider continuity | New prompts reserve the runtime prompt lease before their final admission check and provider-thread reload. A deterministic real-gate regression lets account B rebind immediately before reservation and proves the existing HA chat uses `thread/start`, never account A's `thread/resume`; rejection regressions prove both post-reservation auth failure and legacy-data run validation release all capacity without a provider call, local message, or idempotency record. |
| Deletion-safe lock order | The potentially reconciling admission check runs without `RuntimeBroker._lock`, then the broker repeats started, thread-existence, and full idempotency validation before mutation. A cross-thread lock probe is RED on the prior broker-lock callback and GREEN after the repair. The final callback remains account-binding-safe because active or queued prompt ownership excludes every runtime auth lease. |
| Missing historical files | Account rebinding uses the same schema-valid, canonical-filename, metadata-only durable commit boundary as startup runtime recovery. A RED/green cross-platform regression proves it never invokes unrelated local-state validation, and a Python 3.14 Linux-container HA-profile regression passed after removing a chat workspace before account B binds; it proves the provider projection detaches, every other local chat field remains unchanged, and the private binding settles without relaxing corrupt-record checks. |
| Stale device login generation | If the App server restarts during device login, status recovery clears the old generation's login correlation, detaches its auth lease under the coordinator lock, releases the lease outside that lock, and loops into a fresh gated account read. The RED/green regression proves the old lease was held, the current account settles, a late old-generation completion is inert, and prompt capacity is available afterward. |
| Crash recovery | A lifecycle RED test proved that the previous startup order could detach and then restore the old provider ID. Runtime checkpoint recovery now settles before account binding, so a changed account removes any restored ID before request readiness. |
| TDD evidence | Owner-marker/coordinator tests first failed on the missing helper/callback; storage tests first failed on the missing binding method; lifecycle composition first failed because the legacy provider handle remained attached; recovered-checkpoint, in-flight notification, active-login-poll, queued-promotion, missing-workspace, stale-login-generation, active-steer, and blocked-poll tests then failed on stale startup/auth/admission, retained leases, duplicate events, or over-broad validation behavior. Each focused slice passed after its minimal repair. |
| Complete Bridge suite | Final Windows/Python 3.14 plugin-independent matrix after all review-race repairs: `1473 passed, 218 skipped` in 261.97 seconds. Repository-wide Ruff, compileall, and diff hygiene passed. |
| Focused final-hardening matrix | Auth state/coordinator, storage, lifecycle, broker, and automation tests passed `356 passed, 6 skipped` in 86.06 seconds after the startup-order, authoritative-update, in-flight-read, identity-less, queued-promotion, transient-state, blocked-read, generation-drift, unattended-admission, atomic provider-continuity, pre-registration lease-cleanup, deletion-safe lock-order, missing-historical-workspace, stale-login-generation, active-steer, and blocked-poll dedupe repairs. |
| Frontend gates | ESLint passed; all `320` Vitest unit tests passed; the production panel and local PDF worker rebuilt; and all `22` Playwright browser flows passed. |
| Open gates | Protected CI, signed publication, installation, an actual different-account switch, and bounded existing-chat prompt acceptance remain pending. |
