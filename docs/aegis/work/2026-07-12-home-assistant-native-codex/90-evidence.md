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

## Evidence status

This is draft evidence for continuation. It now proves host/container resource
ceilings, safe archives, bounded app-server transport, structured ChatGPT auth,
the single HA app-server owner plus durable global outbox, private resumable
binary transport, dynamic model recovery, and fail-closed runtime readiness, but
not yet the HA App image, target sandbox, proxy deployment, runtime attachment
representation, release, or cutover.
