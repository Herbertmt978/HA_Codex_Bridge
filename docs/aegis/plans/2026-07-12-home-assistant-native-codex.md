# Home Assistant-native Codex Implementation Plan

## Goal

Replace the Windows VM as the canonical Codex runtime with a protected Home Assistant Supervisor App while retaining and improving the HACS Integration/panel as the only user interface. All browser operations must use the current Home Assistant origin over LAN, Nabu Casa, Cloudflare/reverse proxy, or VPN. Authentication must use ChatGPT-managed device authorization, never an OpenAI API key.

## Architecture

One supervised Codex app-server process owns ChatGPT account state, model discovery, rate limits, threads, turns, streaming, approvals, user questions, and cancellation. The Bridge is a typed broker and durable store. The Integration negotiates an API version, authenticates over the private App network, maintains one upstream event consumer, and exposes admin-only HA WebSocket/HTTP surfaces. The panel uses only relative HA URLs. The App packages the Bridge and locked Codex binaries into an immutable signed image.

## Tech Stack

- Python 3.12+, FastAPI, Uvicorn, Pydantic, HTTPX, SQLite, pytest, and pytest-asyncio.
- Home Assistant custom integration APIs, aiohttp, Voluptuous, and `pytest-homeassistant-custom-component` pinned to the target HA release.
- Vanilla JavaScript, esbuild, Vitest/jsdom, Playwright, and axe-core; no runtime CDN or second UI framework.
- Home Assistant App metadata, S6 Overlay, Bashio, AppArmor, Bubblewrap, Docker Buildx, GHCR, Cosign/Sigstore, and GitHub Actions.

## Baseline/Authority Refs

- Approved design: `docs/aegis/specs/2026-07-12-home-assistant-native-codex-design.md`
- Current baseline: `docs/aegis/baseline/2026-07-12-initial-baseline.md`
- Language: `CONTEXT.md`
- Governance: `docs/aegis/BASELINE-GOVERNANCE.md`
- Home Assistant App configuration, communication, repository, security, publishing, and testing docs linked from the design.
- OpenAI Codex app-server/auth/sandbox docs linked from the design.

## Compatibility Boundary

- The HA App/API v1 path is canonical. It exposes relative workspace names, structured ChatGPT auth, approvals, durable events, and resumable files.
- Existing unversioned Bridge endpoints remain as a deprecated external/Windows adapter through 0.6.x. They do not receive new approval/auth guarantees.
- The old Integration can still reach the new Bridge through legacy endpoints; the new Integration can use an old external Bridge in an explicit advanced route.
- Existing VM files, chats, projects, credentials, and scripts are never moved or deleted automatically.
- The owner requested fresh HA project/chat state and a fresh ChatGPT device login.
- Integration, App, Bridge, API, and Codex versions are independent and displayed separately.

## Verification

- Per-slice RED/GREEN pytest, frontend, and container checks.
- Full Python, HA Integration, frontend unit/E2E, proxy, packaging, lint, security, and image suites.
- Real protected HA OS acceptance on the owner's architecture.
- External access acceptance through the configured HA URL from an authorized network where OpenAI is blocked.
- Cold backup/restore and one App update/rollback before unattended updates or VM retirement.

## Plan Basis

### Facts

- Branch `Herb/ha-app` starts from release 0.5.3 and 115 passing Bridge tests.
- The current Integration uses manual URL/token setup; the current Bridge uses `codex exec --json` and CLI-output auth parsing.
- Current uploads spool into HA Core temporary storage and downloads buffer completely.
- The current project path API permits arbitrary absolute Windows paths.
- OpenAI app-server supports ChatGPT device login, account status/rate limits, models, threads/turns, approvals, and user-input requests.

### Assumptions

- The target is Home Assistant OS or Supervised with the App Store/Supervisor available.
- The owner has an eligible ChatGPT plan and can enable/complete device authorization.
- The target can pull signed GHCR images and make outbound HTTPS connections to OpenAI.

### Runtime facts to discover before publishing App metadata

- The owner's Supervisor architecture, obtained from `ha info --raw-json` or Supervisor `/info`.
- The configured external HA access path used for acceptance.
- Whether protected HA OS permits Codex/Bubblewrap filesystem and network namespaces on that architecture.

Failure of the sandbox fact is a stop condition for cutover, not permission to enable unsafe bypass.

## Ripple Signal Triage

| Signal | Expansion |
|--------|-----------|
| Owner | Windows runtime owner retires; Supervisor App becomes canonical. |
| Downstream | Storage, routes, Integration client, WebSocket/HTTP proxy, panel, docs, release automation, and rollback all change. |
| Contract | Add negotiated API v1 while preserving unversioned legacy adapters through 0.6.x. |
| Source of truth | One app-server client replaces separate auth/model/run subprocess owners. |
| Verification | Python-only testing expands to HA, browser, proxy, container, signature, and real HA OS evidence. |

## File Map

### Bridge runtime

- Create `bridge_service/src/codex_bridge_service/{api_contract,workspace,event_store,codex_app_server,runtime_gate,auth_coordinator,runtime_broker,resource_limits,build_info}.py`.
- Create `bridge_service/src/codex_bridge_service/routes/{approvals,runtime_events,uploads,versions}.py`.
- Modify `settings.py`, `models.py`, `app.py`, `main.py`, `storage.py`, `codex_process.py`, `model_catalog.py`, `limits.py`, `account.py`, `diagnostics.py`, and existing routes.
- Add focused tests and `bridge_service/tests/fakes/fake_app_server.py`.

### Home Assistant Integration and frontend

- Create `custom_components/codex_bridge/{protocol,event_broker,http_streaming}.py`.
- Modify config flow, runtime, API client, WebSocket/HTTP views, manifest, strings, translations, and panel registration.
- Create root `requirements-test.txt`, `pytest.ini`, `tests/custom_components/codex_bridge/`, and transport fixtures.
- Create frontend source/test/E2E tooling under `frontend/`; generate the shipped HACS panel asset.

### App, release, and documentation

- Create `repository.yaml`, `codex_bridge_app/`, `scripts/stage_app_context.py`, `scripts/update_codex_lock.py`, and package/security tests.
- Create pinned GitHub workflows, dependency configuration, ownership, and templates.
- Rewrite root/App documentation and add security/support/contribution/licence notices and real screenshots.
- Keep Windows PowerShell files during the 0.6.x rollback window; move active guidance to legacy migration docs.

## Task 1: Establish API v1, independent versions, and typed readiness

**Files:**

- Create `bridge_service/src/codex_bridge_service/api_contract.py`
- Create `bridge_service/src/codex_bridge_service/build_info.py`
- Create `bridge_service/tests/test_api_contract.py`
- Modify `bridge_service/src/codex_bridge_service/models.py`
- Modify `bridge_service/src/codex_bridge_service/diagnostics.py`
- Modify `bridge_service/src/codex_bridge_service/routes/health.py`
- Modify `bridge_service/src/codex_bridge_service/routes/status.py`

**Why:** Discovery and independent App/HACS updates require explicit compatibility and safe version diagnostics.

**Impact/Compatibility:** Keep top-level `status: "ok"` for 0.5.x callers. Add API v1 fields without removing legacy status/model fields.

**Verification:** `python -m pytest -q bridge_service/tests/test_api_contract.py bridge_service/tests/test_diagnostics.py bridge_service/tests/test_models.py`

- [ ] Write failing tests asserting `API_CURRENT = API_MINIMUM = API_MAXIMUM = 1`, an unversioned legacy adapter, overlap selection, typed `409 api_incompatible`, and `/ready` output containing safe `api`, `app`, `bridge`, `codex`, `image`, `architecture`, `capabilities`, and readiness-reason fields.
- [ ] Run the verification command and confirm failures are missing modules/fields rather than fixture errors.
- [ ] Implement immutable Pydantic records, `negotiate_api(client_min, client_max)`, build-info environment parsing, and additive `/ready`/diagnostics responses. Ensure `repr` and serialization contain no token, URL, auth path, email, or prompt.
- [ ] Run the focused tests and the existing 115-test suite; confirm all pass.
- [ ] Commit only this slice with message `Add Bridge API and version contract`.

## Task 2: Replace inherited subprocess environments with an allowlist

**Files:**

- Modify `bridge_service/src/codex_bridge_service/codex_process.py`
- Modify `bridge_service/tests/test_codex_process.py`
- Extend `bridge_service/tests/test_diagnostics.py`
- Extend `bridge_service/tests/test_model_catalog.py`

**Why:** A Supervisor App environment contains credentials that must never reach Codex or model-controlled tools.

**Impact/Compatibility:** Preserve PATH/HOME/CODEX_HOME/locale/temp/certificate behavior. Authenticated proxy variables and API/PAT values are deliberately unsupported in the HA profile.

**Verification:** `python -m pytest -q bridge_service/tests/test_codex_process.py bridge_service/tests/test_diagnostics.py bridge_service/tests/test_model_catalog.py`

- [ ] Add parameterized failing tests with `SUPERVISOR_TOKEN`, `HASSIO_TOKEN`, Bridge token, `OPENAI_API_KEY`, `CODEX_ACCESS_TOKEN`, CI secrets, cookies, and authenticated proxy URLs; assert absence while PATH, dedicated HOME/CODEX_HOME, locale, TMPDIR, `SSL_CERT_FILE`, and `SSL_CERT_DIR` remain.
- [ ] Run the focused tests and confirm the existing copy-and-filter behavior leaks the sentinel secrets.
- [ ] Implement `codex_subprocess_environment()` from a literal allowlist, set a dedicated HOME/CODEX_HOME/TMPDIR, accept only filesystem certificate paths, and never copy the parent mapping wholesale.
- [ ] Run focused and full Bridge tests; inspect a captured fake-process environment and confirm no sentinel secret.
- [ ] Commit with message `Isolate Codex subprocess environments`.

## Task 3: Constrain all HA workspaces and file operations

**Files:**

- Create `bridge_service/src/codex_bridge_service/workspace.py`
- Create `bridge_service/tests/test_workspace.py`
- Modify `bridge_service/src/codex_bridge_service/settings.py`
- Modify `bridge_service/src/codex_bridge_service/storage.py`
- Modify `bridge_service/src/codex_bridge_service/models.py`
- Modify `bridge_service/src/codex_bridge_service/routes/projects.py`
- Modify attachment/artifact routes and storage tests

**Why:** Codex must see only `/config/workspaces`, never HA config, App credentials, arbitrary host paths, or symlink escapes.

**Impact/Compatibility:** HA API v1 accepts and returns workspace names/relative paths. Absolute `root_path` remains only for `external_legacy` and is visibly deprecated.

**Verification:** `python -m pytest -q bridge_service/tests/test_workspace.py bridge_service/tests/test_storage.py bridge_service/tests/test_threads_api.py`

- [ ] Write failing tests for absolute paths, `..`, symlink/junction escape, archive traversal, special files, stale artifact paths, upload rename races, and API leakage of `/config` or `/data`; add positive create/browse/read/write/archive cases inside a temporary workspace root.
- [ ] Run the focused tests and confirm current arbitrary-path and symlink behavior fails the security assertions.
- [ ] Implement `WorkspaceBoundary` with lexical plus resolved containment, Linux `dir_fd`/`O_NOFOLLOW` operations where available, regular-file enforcement, and relative public records. Route create/browse/upload/archive/artifact/run paths through it; disable drive enumeration in HA mode.
- [ ] Run focused tests on Windows and a Linux container, then the full Bridge suite. Preserve legacy fixtures under the explicit external profile.
- [ ] Commit with message `Confine Home Assistant workspaces`.

## Task 4: Add resource quotas and safe archive handling

**Files:**

- Create `bridge_service/src/codex_bridge_service/resource_limits.py`
- Create `bridge_service/tests/test_resource_limits.py`
- Extend `bridge_service/tests/test_storage.py`
- Modify `settings.py`, `storage.py`, artifact/attachment routes, and models

**Why:** Codex shares the HA host; unbounded uploads, archives, transcripts, and runs can exhaust HA storage or memory.

**Impact/Compatibility:** Defaults are one active turn, eight queued prompts, four-hour total/ten-minute idle runs, 100 MiB/file, 10 GiB workspace, 2 GiB upload+artifact, 20,000 archive entries, 2 GiB expanded archive, 100:1 expansion, and bounded logs/events.

**Verification:** `python -m pytest -q bridge_service/tests/test_resource_limits.py bridge_service/tests/test_storage.py`

- [x] Write failing tests for reservation races, insufficient free space, per-file/workspace/artifact ceilings, zip bombs, entry counts, compression ratio, special entries, partial failures, crash recovery, and quota release.
- [x] Run the tests and confirm current storage accepts the over-limit fixtures.
- [x] Implement immutable `ResourceLimits`, atomic `QuotaManager` reservations, streaming byte counters, free-space margins, safe archive iteration, and typed `413 quota_exceeded`/`409 reservation_conflict` responses.
- [x] Run focused/full tests and verify temporary files/reservations are removed after cancellation and exceptions.
- [x] Commit the atomic limit primitive and HA storage integration as `a92e137` and `21978b7`.

Runtime queue/turn enforcement consumes these immutable limits in Task 7; event compaction and rotated service logging remain owned by Tasks 8 and 20 respectively.

## Task 5: Build the supervised Codex app-server transport

**Files:**

- Create `bridge_service/src/codex_bridge_service/codex_app_server.py`
- Create `bridge_service/tests/fakes/fake_app_server.py`
- Create `bridge_service/tests/test_codex_app_server.py`
- Modify `bridge_service/src/codex_bridge_service/app.py`
- Modify `bridge_service/src/codex_bridge_service/main.py`

**Why:** One structured process must replace separate auth/model/run subprocess owners and support bidirectional approvals without blocking stdout.

**Impact/Compatibility:** Legacy probes remain injectable for tests/external mode; HA mode starts exactly one app-server through FastAPI lifespan.

**Verification:** `python -m pytest -q bridge_service/tests/test_codex_app_server.py`

- [x] Write a scripted JSONL peer and failing tests for initialize/initialized ordering, concurrent request IDs, notifications, server-initiated requests, synchronized responses, malformed lines, timeout, overload, stderr redaction, crash generation, pending-future failure, restart backoff, and process-group shutdown.
- [x] Run the tests and confirm the client module is absent.
- [x] Implement `CodexAppServerClient.start()`, `request()`, `respond()`, handler registration, `close()`, one reader thread, synchronized writes, response futures, bounded callback dispatch, and generation-aware restart. Generate/lock method schemas from the bundled Codex binary during CI rather than accepting arbitrary payloads.
- [x] Run the focused test 20 times with `1..20 | ForEach-Object { python -m pytest -q bridge_service/tests/test_codex_app_server.py; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`, then run the full Bridge suite and confirm no reader threads/processes survive.
- [x] Commit as `19cad27` with message `Add supervised Codex app server client`.

## Task 6: Replace CLI login parsing with ChatGPT-only auth coordination

**Files:**

- Create `bridge_service/src/codex_bridge_service/auth_coordinator.py`
- Create `bridge_service/tests/test_auth_coordinator.py`
- Modify `bridge_service/src/codex_bridge_service/codex_auth.py` into a legacy adapter
- Modify `bridge_service/src/codex_bridge_service/routes/codex_auth.py`
- Modify `bridge_service/src/codex_bridge_service/models.py`
- Modify `bridge_service/src/codex_bridge_service/account.py`
- Modify `bridge_service/src/codex_bridge_service/limits.py`

**Why:** Device login/logout must stream safely in HA, survive restarts, avoid races, and use ChatGPT account entitlements instead of API keys.

**Impact/Compatibility:** Keep `force_logout` accepted but ignored for old clients. Add cancel. API v1 supports only `authMode: chatgpt`; API key/PAT modes block runs.

**Verification:** `python -m pytest -q bridge_service/tests/test_auth_coordinator.py bridge_service/tests/test_codex_auth.py bridge_service/tests/test_account.py`

- [ ] Write failing tests for startup `account/read`, `chatgptDeviceCode` login, live code event, matching generation+`loginId`, stale completion rejection, cancel, explicit logout+final read, restart persistence, terminal code clearing, repeated identical expiry revision, zero-chat auth events, wrong auth modes, missing device authorization, and auth/run conflicts.
- [x] Run focused tests and capture failures from the stdout parser/race behavior.
- [x] Implement a lock-protected coordinator with monotonic revision, operation generation, `loginId`, normalized safe states, `busy`, `account/read`, `account/login/start`, `account/login/cancel`, and `account/logout`. Replace direct `auth.json` token decoding/rate-limit HTTP with `account/read` and `account/rateLimits/read` for HA mode.
- [x] Run focused/full tests. Assert no raw app-server error, reusable token, email, or auth file contents enters events/logs.
- [x] Commit as `0480f38` with message `Add structured ChatGPT account flow`.

All Task 6 cases except the shared auth/run exclusion are complete. That remaining test is intentionally carried into Task 7, which owns the global runtime lease and app-server turn broker; duplicating a storage-status check here would introduce a TOCTOU path.

## Task 7: Add the global runtime gate, app-server turns, approvals, and questions

**Files:**

- Create `bridge_service/src/codex_bridge_service/runtime_gate.py`
- Create `bridge_service/src/codex_bridge_service/runtime_broker.py`
- Create `bridge_service/src/codex_bridge_service/routes/approvals.py`
- Create `bridge_service/tests/test_runtime_gate.py`
- Create `bridge_service/tests/test_runtime_broker.py`
- Create `bridge_service/tests/test_approvals_api.py`
- Modify `runner.py` into a deprecated external adapter
- Modify prompt routes, models, storage, and app composition

**Why:** Every command approval and Codex question must be visible/answerable through HA; one active global turn protects the host and auth state.

**Impact/Compatibility:** API v1 uses app-server threads/turns. Fresh HA state does not resume old `codex_session_id`. Legacy external mode retains `BridgeRunner` through 0.6.x.

**Verification:** `python -m pytest -q bridge_service/tests/test_runtime_gate.py bridge_service/tests/test_runtime_broker.py bridge_service/tests/test_approvals_api.py bridge_service/tests/test_runner.py`

- [x] Write failing tests for one global lease, eight queued prompts, one queued prompt per chat, stable `client_request_id` idempotency, thread start/resume, turn start/steer/interrupt, mode policies, exact sandbox/path rejection, callback FIFO and early provider resolution, restart interruption, queue cleanup, deletion/storage races, command/patch approvals, `request_user_input`, stale/duplicate/cross-thread decisions, steer outcome uncertainty, and auth mutation conflicts.
- [x] Run focused tests and confirm the `codex exec` runner cannot satisfy server requests or global concurrency.
- [x] Implement broker methods `submit_prompt`, `cancel_run`, `decide_approval`, `answer_user_input`, and `close`. Correlate thread/turn/item/event IDs; never block the app-server reader. Automatically deny network/private-host/out-of-workspace permission escalation. Map Observe=`read-only/on-request`, Edit=`workspace-write/on-request`, Full auto=`workspace-write/never`.
- [x] Run focused/full tests, including randomized duplicate/reorder sequences. Confirm no process waits on stdin and late decisions return typed 409/410 errors.
- [x] Commit as `6583d9f` with message `Broker Codex turns and approvals`.

Settled Task 7 verification is 251 passed/6 skipped focused; 723 passed/139
skipped on Windows Python 3.14.4; and 851 passed/1 skipped with one
Starlette deprecation warning in the Linux Python 3.13 container, excluding the
Windows-only updater test. The accepted behavior rejects mismatched sandbox and
workspace-path echoes, preserves callback and early-resolution FIFO, permits at
most one queued prompt per chat, serializes storage mutation/deletion, and marks
uncertain steer outcomes non-replayable before aborting the owning generation.

Task 7 intentionally fails attachment-backed turns closed with
`runtime_attachments_not_ready`. Task 9 owns the checksum-bound upload/runtime
transport; placing private attachment copies in a user workspace was rejected
because it could leak them into source control and leave crash orphans. Durable
cross-file state/event exactly-once delivery remains a Task 8 dependency, and
real sandbox confinement remains a target-system acceptance gate. Schema-valid
command approvals that omit `commandActions` are automatically declined: Task 7
only surfaces parsed actions whose paths pass workspace containment checks. This
deliberately narrows command approval until Task 21 proves real sandbox
confinement on the target Home Assistant system.

## Task 8: Add a durable global event journal and replay contract

**Files:**

- Create `bridge_service/src/codex_bridge_service/event_store.py`
- Create `bridge_service/src/codex_bridge_service/routes/runtime_events.py`
- Create `bridge_service/tests/test_event_store.py`
- Create `bridge_service/tests/test_events_api.py`
- Modify `storage.py`, existing event routes, auth coordinator, and runtime broker

**Why:** Remote proxy/WebSocket interruptions must resume exactly once; auth events must stream before any chat exists.

**Impact/Compatibility:** API v1 returns `EventBatchRecord`; `/threads/{id}/events/replay` remains a list-shaped v0 adapter. Legacy JSONL imports idempotently. Existing JSON state remains canonical during 0.6.x and is paired with a durable SQLite outbox/reconciler rather than pretending JSON and SQLite share a transaction.

**Verification:** `python -m pytest -q bridge_service/tests/test_event_store.py bridge_service/tests/test_events_api.py`

- [x] Write failing tests for global monotonic cursors, auth/runtime/thread scopes, replay-before-live locking, wait heartbeat, bounded batch, dedupe, concurrent writers, SQLite restart, retention/compaction, expired cursor 410 with minimum/snapshot guidance, and idempotent legacy import. Add injected crashes after outbox commit, after atomic JSON replace, and before event append; startup reconciliation must yield one state revision and exactly one event.
- [x] Run focused tests and confirm current per-thread snapshots lack a global cursor/wait behavior.
- [x] Implement SQLite WAL-backed `BridgeEventStore.append/replay/wait/compact`, typed batches, condition signalling, payload size validation, and adapters. Add a `DurableOutbox`: commit an operation ID plus complete intended JSON/event payload to SQLite, atomically replace/fsync the JSON record with that operation ID/revision, then append the uniquely keyed event and mark applied in one SQLite transaction. Before readiness, reconcile every pending row by applying or recognizing the JSON revision and idempotently appending the event.
- [x] Run focused/full tests with concurrent writers and restart loops; inspect database/event sizes against configured limits.
- [x] Commit with message `Add durable Bridge event journal`.

## Task 9: Add resumable uploads and ranged artifact downloads

**Files:**

- Create `bridge_service/src/codex_bridge_service/routes/uploads.py`
- Create `bridge_service/tests/test_uploads_api.py`
- Extend `bridge_service/tests/test_storage.py`
- Modify attachment/artifact routes, storage, models, and resource limits

**Why:** Provider-neutral remote access needs bounded requests, resume after proxy disconnects, and no whole-file HA buffering.

**Impact/Compatibility:** Keep the old multipart endpoint for external v0. API v1 adds create/status/chunk/complete/cancel upload sessions and Range downloads.

**Verification:** `python -m pytest -q bridge_service/tests/test_uploads_api.py bridge_service/tests/test_storage.py`

- [x] Write failing tests for 8 MiB chunk negotiation, idempotent same-index retry, offset/order errors, per-chunk/final SHA-256, resume after restart, cancel cleanup, quota conflict, client disconnect, malicious names, 206/416 ranges, ETag/If-Range, and attachment-safe headers.
- [x] Run tests and confirm the existing multipart/download path cannot resume and lacks typed range behavior.
- [x] Implement upload-session manifests under private Bridge state, atomic chunk writes, streaming final assembly into the confined private attachment store, checksums, crash recovery, cancellation, and ranged streaming. Force sanitized `attachment`, `application/octet-stream`, and `nosniff` headers.
- [x] Run focused/full tests and a memory-bounded large-file smoke test; verify HA Core is not involved in this Bridge-only stage.
- [x] Commit as `ed9e6f9` with message `Add resumable Bridge file transport`.

Task 9 deliberately keeps completed uploads private and available-but-unselected.
Text-only prompts therefore continue without implicitly exposing stored files. The
locked app-server `UserInput` schema has no generic-file variant, so explicit
attachment selection, bounded supported representations, capability negotiation,
and consented workspace import remain owned by Tasks 10-17 rather than inventing
an unsupported runtime field or copying private files into source-controlled workspaces.

## Task 10: Share app-server models, account data, and fatal readiness

**Files:**

- Modify `model_catalog.py`, `limits.py`, `account.py`, `app.py`, `main.py`, diagnostics, status/readiness routes, and tests
- Create `bridge_service/tests/test_runtime_lifecycle.py`

**Why:** The App must own one runtime, recover model defaults correctly, and fail closed when its sandbox is unavailable.

**Impact/Compatibility:** Preserve fallback/cached catalogue semantics and direct-chat recovery. HA readiness distinguishes ready, auth-required, degraded catalogue, and fatal sandbox/runtime.

**Verification:** `python -m pytest -q bridge_service/tests/test_runtime_lifecycle.py bridge_service/tests/test_model_catalog.py bridge_service/tests/test_threads_api.py bridge_service/tests/test_diagnostics.py`

- [ ] Write failing tests proving one shared app-server client serves `model/list`, `account/read`, rate limits, and turns; add lifecycle shutdown, crash/reconcile, stale catalogue recovery before direct chat creation, build-version mismatch, and fatal sandbox cases.
- [ ] Run focused tests and confirm current probes spawn independently and readiness always says ok.
- [ ] Inject the shared client into probes/coordinators/broker through FastAPI lifespan, reconcile on restart, implement sandbox self-test state, preserve provisional catalogue rules, and close resources in reverse ownership order.
- [ ] Run focused/full suites and assert one fake app-server process per application instance and no leaked worker/thread.
- [ ] Commit with message `Unify Codex runtime lifecycle`.

## Task 11: Add the Home Assistant Integration test foundation and protocol client

**Files:**

- Create `requirements-test.txt`
- Create root `pytest.ini`
- Create `tests/conftest.py`
- Create `tests/custom_components/codex_bridge/{conftest,test_protocol,test_bridge_api}.py`
- Create `tests/fixtures/ready_{legacy_v0,v1,future_incompatible}.json`
- Create `custom_components/codex_bridge/protocol.py`
- Modify `custom_components/codex_bridge/const.py`
- Modify `custom_components/codex_bridge/bridge_api.py`

**Why:** Core-side behavior currently has no automated test authority, redirect defense, timeout policy, or typed v1 errors.

**Impact/Compatibility:** Use API v1 for discovered Apps and an explicit capability-limited v0 client for external Bridges.

**Verification:** `python -m pytest -q tests/custom_components/codex_bridge/test_protocol.py tests/custom_components/codex_bridge/test_bridge_api.py`

- [ ] Pin Home Assistant/pytest integration dependencies to the current target minor; write failing tests for range overlap/mismatch, authenticated `/ready` verification, discovery/ready mismatch, malformed/public endpoints, redirect refusal, connect/read/total timeouts, 409/410/413/416 mapping, streaming response ownership, and secret-free repr/log errors.
- [ ] Install test requirements and run focused tests; confirm missing protocol and current client behavior fail.
- [ ] Implement immutable discovery/ready records, API negotiation, explicit `aiohttp.ClientTimeout`, `allow_redirects=False`, typed problems, `X-Codex-Bridge-Api: 1`, streaming context managers, and redacted error types.
- [ ] Run Integration and Bridge suites together; confirm no global event loop/session leak.
- [ ] Commit with message `Add tested Home Assistant Bridge client`.

## Task 12: Implement Supervisor discovery, stable identity, and token rotation

**Files:**

- Create `tests/custom_components/codex_bridge/{test_config_flow,test_init}.py`
- Modify `custom_components/codex_bridge/config_flow.py`
- Modify `custom_components/codex_bridge/__init__.py`
- Modify `custom_components/codex_bridge/runtime.py`
- Modify `custom_components/codex_bridge/strings.json`
- Modify `custom_components/codex_bridge/translations/en.json`
- Modify `custom_components/codex_bridge/manifest.json`

**Why:** The primary setup must require no URL/token copying and must recover safely when the App rotates its token or address.

**Impact/Compatibility:** `async_step_hassio` is primary. Manual URL/token fields move under **External Bridge (advanced)** and remain v0-only through 0.6.x.

**Verification:** `python -m pytest -q tests/custom_components/codex_bridge/test_config_flow.py tests/custom_components/codex_bridge/test_init.py`

- [ ] Write failing tests for Integration-first/App-first order, exact service/slug/instance ID, private host/port, token validation, authenticated contract confirmation, duplicate discovery, rediscovery update/reload, rotation without token logs, incompatible API, auth rejection, user install/start guidance, advanced external flow, unload/reload, and process-lifetime command/view registration.
- [ ] Run focused tests and confirm only manual setup exists.
- [ ] Implement `async_step_hassio`, stable unique ID, automatic entry updates, primary waiting/retry form, explicit external step, runtime cancellation/restart, and redacted repair errors. Token rotation returns status only; the new token arrives by rediscovery.
- [ ] Run focused/full Integration suites and inspect `caplog` for old/new token sentinels.
- [ ] Commit with message `Discover the Codex Bridge App automatically`.

## Task 13: Replace per-panel polling with one Integration event broker

**Files:**

- Create `custom_components/codex_bridge/event_broker.py`
- Create `tests/custom_components/codex_bridge/{test_event_broker,test_websocket_api}.py`
- Modify `custom_components/codex_bridge/runtime.py`
- Modify `custom_components/codex_bridge/__init__.py`
- Modify `custom_components/codex_bridge/bridge_api.py`
- Modify `custom_components/codex_bridge/websocket_api.py`

**Why:** Auth must stream with zero chats and multiple remote panels must not create duplicate upstream polling or lose events after proxy reconnects.

**Impact/Compatibility:** API v1 uses one runtime consumer and HA subscriptions. Legacy v0 retains current per-thread polling only in advanced external mode.

**Verification:** `python -m pytest -q tests/custom_components/codex_bridge/test_event_broker.py tests/custom_components/codex_bridge/test_websocket_api.py`

- [ ] Write failing tests proving two subscribers create one upstream consumer; cover auth/runtime/thread scopes, cursor persistence, replay-before-live, exactly-once dedupe, heartbeat, gap snapshot, bounded slow subscriber, exponential reconnect, token change, unsubscribe, unload, and upstream-error state.
- [ ] Run tests and confirm the current per-browser polling behavior fails the shared-consumer assertions.
- [ ] Implement a config-entry-owned broker with one cancellable long-poll loop, scoped callback subscriptions, bounded queues, cursor-gap recovery, and connection status. Register admin-only `subscribe_events`, auth actions, approvals, user-input, prompt idempotency, and safe diagnostics commands.
- [ ] Run focused/full tests with task-leak detection; verify no data is placed on the HA event bus/logbook.
- [ ] Commit with message `Stream Bridge events through Home Assistant`.

## Task 14: Stream resumable files through authenticated HA HTTP views

**Files:**

- Create `custom_components/codex_bridge/http_streaming.py`
- Create `tests/custom_components/codex_bridge/test_http.py`
- Modify `custom_components/codex_bridge/http.py`
- Modify `custom_components/codex_bridge/bridge_api.py`

**Why:** The remote browser must transfer files through HA without HA Core temp files, whole-memory buffers, unsafe headers, or proxy-specific URLs.

**Impact/Compatibility:** Keep old multipart/download views for external v0; API v1 adds upload-session/chunk/complete/cancel and ranged download views.

**Verification:** `python -m pytest -q tests/custom_components/codex_bridge/test_http.py`

- [ ] Write failing tests for admin authorization, create/status, repeated chunk, checksum mismatch, request streaming without temp files, completion/cancel, client cancellation, upstream timeout, 413, Range/If-Range, 206/416, bounded response iteration, malicious filename/header injection, hop-by-hop stripping, attachment/octet-stream/nosniff, and redirect refusal.
- [ ] Run tests and confirm current `NamedTemporaryFile`/`response.read()` behavior fails bounded-stream assertions.
- [ ] Implement chunk/range views that pass `request.content` upstream and write upstream chunks to `aiohttp.web.StreamResponse`; preserve only safe range/cache metadata and own/close every response deterministically.
- [ ] Run focused/full Integration suites plus a 100 MiB memory smoke test through a local HA test server.
- [ ] Commit with message `Stream files through Home Assistant`.

## Task 15: Establish a reproducible frontend build and hostile-content tests

**Files:**

- Create `package.json`, `package-lock.json`, and `eslint.config.js`
- Create `frontend/src/{codex-bridge-panel,protocol,event-stream,safe-dom,uploads}.js`
- Create `frontend/test/{helpers,protocol,event-stream,xss}.test.js`
- Create `frontend/e2e/panel.spec.js`
- Move `output/playwright/panel-harness.html` to `frontend/e2e/panel-harness.html`
- Generate `custom_components/codex_bridge/frontend/codex-bridge-panel.js`

**Why:** The 4,000-line generated panel has no build/test authority and renders attacker-controlled Codex content on the HA admin origin.

**Impact/Compatibility:** Keep the existing custom element and panel URL. Use vanilla modules bundled locally; no Lit/React/CDN/runtime dependency.

**Verification:**

```powershell
npm ci
npm run lint
npm run test:unit
npm run build
git diff --exit-code -- custom_components/codex_bridge/frontend/codex-bridge-panel.js
```

- [ ] Add failing Vitest/jsdom tests for protocol/state utilities and an XSS corpus containing closing tags, quote-breaking IDs, SVG/script/srcdoc, event handlers, `javascript:`/`data:` links, hostile models/errors/diffs/filenames, and remote embeds. Add a build-integrity test that imports the shipped custom element.
- [ ] Run unit/build commands and confirm the missing toolchain/source modules fail.
- [ ] Move canonical source under `frontend/src`, configure esbuild to emit one deterministic HACS asset, construct untrusted nodes with `textContent`, restrict links to explicit safe schemes, disable HTML/SVG/PDF iframe preview, and allow safe text/raster Blob previews only.
- [ ] Run lint/unit/build; inspect the generated asset for remote imports and run Playwright to assert no script/handler execution or non-HA-origin request.
- [ ] Commit with message `Add secure frontend build and tests`.

## Task 16: Build cohesive onboarding and ChatGPT account management

**Files:**

- Create `frontend/src/views/{onboarding,auth,runtime-strip}.js`
- Create `frontend/test/{onboarding,auth,runtime-strip}.test.js`
- Modify panel source, generated asset, WebSocket commands, and strings

**Why:** The user needs a self-explanatory HA-first setup, live device code, cancel/logout, safe plan status, and no VM/API-key concepts.

**Impact/Compatibility:** The panel appears only after discovery confirmation. External legacy mode shows a deprecation notice and capability-limited UI.

**Verification:**

```powershell
npm run test:unit -- frontend/test/onboarding.test.js frontend/test/auth.test.js frontend/test/runtime-strip.test.js
npm run build
python -m pytest -q tests/custom_components/codex_bridge/test_websocket_api.py
```

- [ ] Write failing tests for App-connected completed state, App disconnect/retry, live **Sign in with ChatGPT**, code copy/open, phone guidance, cancel, explicit confirmed sign-out, safe plan type, terminal code clearing, API-key/PAT rejection, workspace creation, first chat, and zero-thread auth events.
- [ ] Run tests and capture current `force_logout=true`, missing cancel/logout, and VM wording failures.
- [ ] Implement the four-stage checklist, auth subscription before chats, idempotent login without implicit logout, cancel/sign-out controls, relative workspace picker/importer, runtime/version strip, and precise provider-neutral remote guidance. Remove active URL/token/Windows/VM/API-key copy.
- [ ] Run frontend/Python tests and manually exercise narrow/mobile plus light/dark HA themes in the harness.
- [ ] Commit with message `Create Home Assistant first run experience`.

## Task 17: Add inline approvals, questions, resilient runs, and accessibility

**Files:**

- Create `frontend/src/views/{approval,user-input}.js`
- Create `frontend/test/{approval,user-input,composer,accessibility}.test.js`
- Extend `frontend/e2e/panel.spec.js`
- Modify panel source/styles, generated asset, and Integration commands
- Create `tests/transport/{compose.yaml,proxy.conf,e2e.spec.js}`

**Why:** All Codex decisions must happen in HA and survive WebSocket/proxy drops without duplicated turns.

**Impact/Compatibility:** Mode semantics become explicit. Network/private-path elevation remains policy-denied even if displayed.

**Verification:**

```powershell
npm run test:unit
npm run test:e2e
docker compose -f tests/transport/compose.yaml up --build --abort-on-container-exit --exit-code-from e2e
```

- [ ] Write failing tests for escaped command/patch scope, expiry, accept/decline/cancel, questions/answers, immediate control disable, stale/duplicate/cross-thread rejection, stable `client_request_id`, transcript/cursor preservation, reconnect/reconcile states, exactly-once resume, keyboard dialogs, focus return, live regions, reduced motion, mobile layout, and axe-core violations.
- [ ] Run unit/E2E/proxy tests and confirm current subscription failure and duplicate-submit risks.
- [ ] Implement inline decision cards, question forms, descriptive Observe/Edit/Full auto boundaries, mutation lock during reconciliation, cursor snapshot/resume, accessible dialogs/focus/live regions, and provider-neutral relative HTTP/WebSocket paths.
- [ ] Run full frontend/Integration/proxy suites; capture redacted desktop/mobile screenshots from the real built panel, not a generated mockup.
- [ ] Commit with message `Add resilient Codex controls to the HA panel`.

## Task 18: Create the least-privilege Home Assistant App repository

**Files:**

- Create `repository.yaml`
- Create `codex_bridge_app/{config.yaml,README.md,DOCS.md,CHANGELOG.md,apparmor.txt,icon.png,logo.png}`
- Create `codex_bridge_app/translations/en.yaml`
- Create `bridge_service/tests/test_app_package.py`

**Why:** Supervisor must own the runtime with no host port, Ingress, privileged role, or broad filesystem mapping.

**Impact/Compatibility:** First metadata advertises only the owner's proven architecture. Docker/build logic may support both architectures without publishing support claims.

**Verification:** `python -m pytest -q bridge_service/tests/test_app_package.py`

- [ ] Obtain the target architecture with `ha info --raw-json`; write failing metadata tests for required repository/App fields and explicit absence of ports, Ingress, host network, Docker API, devices, full access, broad Supervisor roles, `/share`, `homeassistant_config`, `all_addon_configs`, and obsolete `build.yaml`. Assert only `addon_config:rw`, cold backup, experimental stage, discovery, a generic multi-architecture immutable image, and the proven architecture.
- [ ] Run focused tests and confirm metadata is missing.
- [ ] Add App repository/metadata/translations/branding with `slug: codex_bridge`, App version `0.6.0`, `startup: application`, `boot: auto`, `init: false`, `stage: experimental`, `backup: cold`, and `ghcr.io/herbertmt978/ha-codex-bridge-app`.
- [ ] Run tests plus the current Home Assistant App/repository linter; render App Store metadata and inspect name/icon/descriptions.
- [ ] Commit with message `Add Codex Bridge Home Assistant App`.

## Task 19: Lock and verify upstream Codex releases

**Files:**

- Create `codex_bridge_app/codex-release.json`
- Create `scripts/update_codex_lock.py`
- Create offline fixtures under `bridge_service/tests/fixtures/codex_releases/`
- Create `bridge_service/tests/test_codex_release_lock.py`

**Why:** Automatic Codex updates require independent identity verification, monotonic versions, bounded archives, and exact architecture assets.

**Impact/Compatibility:** Seed the lock from the then-current verified stable `openai/codex` release; do not hard-code the planning-time version. The updater never installs at runtime and never accepts drafts/prereleases or an identity mismatch.

**Verification:**

```powershell
python -m pytest -q bridge_service/tests/test_codex_release_lock.py
python scripts/update_codex_lock.py --check codex_bridge_app/codex-release.json
```

- [ ] Write failing fixture tests for stable/prerelease/draft, monotonic tag, missing/duplicate assets, archive/bundle digest, decompression size, Sigstore issuer/identity/repository/workflow/tag/transparency log, both musl architectures, and malicious GitHub metadata.
- [ ] Run tests and confirm updater/lock are absent.
- [ ] Implement offline-verifiable lock parsing and online update/check modes. Seed the current verified stable musl Codex and Bubblewrap assets/digests from official release metadata, including issuer `https://token.actions.githubusercontent.com` and tagged `openai/codex` release-workflow identity. Record version, compressed/decompressed size/digest, and bundle identity in the lock.
- [ ] Run fixtures, fetch current official metadata, independently verify every recorded digest/Sigstore bundle, stage the target asset, and confirm `codex --version` exactly matches the lock.
- [ ] Commit with message `Lock verified Codex release assets`.

## Task 20: Build the reproducible non-root App runtime and discovery bootstrap

**Files:**

- Create `codex_bridge_app/Dockerfile`
- Create `codex_bridge_app/rootfs/etc/s6-overlay/s6-rc.d/` services for initialization, Bridge, and discovery
- Create `scripts/stage_app_context.py`
- Create `bridge_service/tests/{test_app_build_context,test_app_startup}.py`
- Modify `.gitignore`

**Why:** The image must be immutable, independently reproducible from repository source, and keep Supervisor credentials out of the long-lived runtime.

**Impact/Compatibility:** CI stages a temporary `.build/app-context/`; no wheel/binary/duplicate Bridge source is committed. The App runs one non-root Bridge worker and consumes only Task 19-verified assets.

**Verification:**

```powershell
$arch = (ha info --raw-json | ConvertFrom-Json).arch
python -m pytest -q bridge_service/tests/test_app_build_context.py bridge_service/tests/test_app_startup.py
python scripts/stage_app_context.py --arch $arch
docker buildx build --load -t codex-bridge:test .build/app-context
```

- [ ] Write failing tests for deterministic staged manifests, wheel build, exact Task 19 lock/digests, no parent-directory Docker COPY, token generation/mode/atomicity, `/data` and `/config/workspaces` ownership, `CODEX_HOME` file-store config, sanitized long-lived environment, one Uvicorn worker, readiness wait, exact discovery payload, Supervisor-token removal, signal shutdown, and log redaction.
- [ ] Run focused tests and confirm runtime files are absent.
- [ ] Implement the context stager, explicit pinned base image, non-root user, S6 one-shots/longrun, token-file auth, config initialization, authenticated readiness, Bashio/Supervisor discovery, and `exec` with a sanitized environment. Stage only lock-verified Codex/Bubblewrap assets and add `.build/` to ignore rules.
- [ ] Run pytest, ShellCheck, Hadolint, image build, locked `codex --version`, container health, and shutdown tests. Inspect the Bridge process environment inside the test container for sentinel secrets.
- [ ] Commit with message `Build the immutable Codex Bridge App image`.

## Task 21: Enforce AppArmor and the fatal filesystem/network sandbox gate

**Files:**

- Create `codex_bridge_app/rootfs/usr/local/bin/sandbox-self-test`
- Finalize `codex_bridge_app/apparmor.txt`
- Create `bridge_service/tests/test_app_sandbox_contract.py`
- Modify readiness/build-info models and App docs

**Why:** Model-controlled tools share a network with Supervisor/Core/Apps and a container with ChatGPT credentials; failure must be fatal.

**Impact/Compatibility:** AppArmor is the outer container boundary; the exact locked Codex/Bubblewrap pair from Task 19 isolates tool subprocesses. The dangerous bypass flag is rejected in HA profile and absent from options/docs.

**Verification:** `python -m pytest -q bridge_service/tests/test_app_sandbox_contract.py` plus the real protected-HA acceptance script.

- [ ] Write failing static/runtime tests against the built Task 20 image for forbidden bypass/full-access flags, narrow AppArmor paths, non-root runtime, filesystem sentinels, parent `/proc/*/environ`, `auth.json`, Bridge token/state, outside workspace, and network attempts to Supervisor, Core, sibling App, LAN, internet, and OpenAI.
- [ ] Run local/container tests and confirm failures are explicit rather than skipped as success.
- [ ] Implement a self-test that proves the exact tool sandbox used by app-server; expose fatal/degraded details without paths/secrets. Permit only trusted Codex parent egress to OpenAI; decline all model-tool network elevation in API v1.
- [ ] Run container tests, then install on protected target HA OS and record pass/fail evidence for the advertised architecture. If any isolation assertion fails, keep readiness fatal and do not proceed to cutover.
- [ ] Commit passing code/evidence metadata with message `Fail closed when the Codex sandbox is unavailable`.

## Task 22: Add pinned CI, signed image publishing, and safe automatic updates

**Files:**

- Create `.github/workflows/{ci,build-app,codex-update,release}.yml`
- Create `.github/dependabot.yml`
- Create `.github/CODEOWNERS`
- Extend updater/package tests

**Why:** Codex should update automatically through reviewed immutable images, not self-modifying runtime installs.

**Impact/Compatibility:** Codex-only changes bump App version/changelog/lock, not Integration version. Auto-merge stays disabled until one real target-HA update/rollback passes.

**Verification:** `actionlint`, `zizmor .github/workflows`, local test/build commands, GH Actions dry runs, Cosign verification, and immutable-tag checks.

- [ ] Write failing policy tests that parse workflows and require full commit-SHA action pins, least permissions, target architecture build, lock verification, expected updater file allowlist, `CODEX_UPDATE_PAUSED` kill switch, concurrency, artifact attestations/SBOM, keyless signing, published digest verification, immutable tags, and no automatic merge before canary evidence.
- [ ] Run tests plus actionlint/zizmor and confirm workflows are absent.
- [ ] Implement PR CI for Python/HA/frontend/proxy/App lint/build/security; main image publication; Integration release; and daily stable Codex watcher that opens a narrowly scoped PR. Use official Home Assistant builder actions only where their pinned interface fits the staged context; otherwise use pinned Docker Buildx/Cosign actions.
- [ ] Run local policy/lint/build suites, push the branch, inspect every workflow permission/resolved action SHA, and run a non-release `workflow_dispatch` build. Verify GHCR digest/signature/SBOM without overwriting a version.
- [ ] Commit with message `Automate verified Codex App releases`.

## Task 23: Deliver A-grade documentation, branding, and repository hygiene

**Files:**

- Rewrite `README.md`
- Create `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, `THIRD_PARTY_NOTICES.md`
- Create `docs/{installation,remote-access,backup-restore,migration-from-windows,development}.md`
- Create `.github/ISSUE_TEMPLATE/{bug,feature,config}.yml`
- Create `.github/PULL_REQUEST_TEMPLATE.md`
- Add redacted real images under `docs/images/`
- Update App docs/changelog, HACS metadata, and licence links

**Why:** Installation, remote access, security, recovery, and public presentation are release criteria.

**Impact/Compatibility:** Windows becomes a deprecated rollback guide. No badge/topic implies official or Community endorsement.

**Verification:** Markdown/link/spelling/secret checks, rendered light/dark/mobile review, and fresh-install command walkthrough.

- [ ] Write failing repository tests for required docs/sections, valid internal/external links, restrained real badges, HACS/App install links, ChatGPT-not-API-key wording, provider-neutral remote contract, backup/rollback/removal, third-party Apache notice, no private HA URLs/credentials/personal paths, no active VM-first Quick start, and no endorsement language.
- [ ] Run checks and confirm current Windows-first README/repository files fail.
- [ ] Rewrite the README in outcome-first order; add exact two-surface installation, first run, Cloudflare/Nabu/VPN/LAN guidance, WebSocket/chunk requirements, updates, recovery, security, troubleshooting, development, removal, support/governance, and verified screenshots. Reuse/refine existing brand assets; do not invent unsupported badges.
- [ ] Render every document and App Store/HACS surface; execute every command/link on a fresh test setup; run secret/spelling/link checks and visual review in HA light/dark/mobile.
- [ ] Commit with message `Document the Home Assistant-native experience`.

## Task 24: Complete compatibility, migration, ADR, and architecture retirement records

**Files:**

- Extend compatibility fixtures/tests in Bridge and Integration suites
- Create `docs/aegis/adr/` records for runtime ownership, trust/transport, storage, auth, distribution, and legacy retirement
- Create a post-implementation baseline snapshot and update `docs/aegis/INDEX.md`
- Modify Windows scripts/docs only to mark deprecation and preserve rollback
- Remove tracked generated `bridge_service/dist/` artifacts; generate release assets in CI

**Why:** Durable decisions and the old owner need explicit, tested retirement rather than an indefinite second architecture.

**Impact/Compatibility:** Windows scripts remain functional through 0.6.x. Removal occurs only in the next breaking release after the user explicitly removes the VM fallback.

**Verification:** Old Integration→new App, new Integration→old external Bridge, fresh HA state, Windows updater tests, Aegis index/link checks, and generated-artifact cleanliness.

- [ ] Write failing compatibility tests for v0/v1 negotiation, additive legacy shapes, direct-chat model recovery, no old session resume, fresh state, deprecated external capability set, and unchanged Windows workspace files.
- [ ] Run focused compatibility tests and record current contract gaps.
- [ ] Implement the narrow v0 adapters/deprecation notices, remove checked-in build outputs, preserve PowerShell rollback behavior, write ADRs from proven outcomes, and write a new baseline ownership/contract snapshot. Include retirement triggers and the sandbox falsification result.
- [ ] Run Python/HA/frontend/Windows compatibility suites and Aegis/link checks; verify no generated output or old VM instruction remains in primary install paths.
- [ ] Commit with message `Record HA runtime ownership and legacy retirement`.

## Task 25: Full verification, target-HA cutover, release, and merge

**Files/External state:**

- No speculative code changes; fix only evidence-backed failures in their owning slices.
- GitHub branch/PR/checks/GHCR/release/topics/tags.
- Target Home Assistant test installation, backup, App/Integration configuration, and rollback evidence.

**Why:** Local green tests do not prove protected HA sandboxing, proxy behavior, auto-update, backup, or safe retirement.

**Impact/Compatibility:** Stop—but do not delete—the VM only after every cutover gate. Keep rollback files/image/backup.

**Verification commands:**

```powershell
Set-Location 'C:\Users\Ashby\Dropbox\PC (3)\Documents\Code\ha-codex-bridge'
python -m pytest -q bridge_service/tests tests/custom_components/codex_bridge
npm ci
npm run lint
npm run test:unit
npm run build
git diff --exit-code -- custom_components/codex_bridge/frontend/codex-bridge-panel.js
npm run test:e2e
docker compose -f tests/transport/compose.yaml up --build --abort-on-container-exit --exit-code-from e2e
python scripts/update_codex_lock.py --check codex_bridge_app/codex-release.json
git diff --check
git status --short
```

- [ ] Run the complete local matrix from a clean checkout, then App lint, ShellCheck, Hadolint, vulnerability scan, SBOM/signature, and target-architecture container tests. Fix failures test-first in the owning task and rerun the complete matrix.
- [ ] Install HACS Integration then App on the target HA; verify discovery without URL/token, ChatGPT device login/logout/restart, models, first chat, approvals/questions/cancel, exact-once reconnect, resumable upload/range download, quotas, redaction, and fatal sandbox/filesystem/network sentinels.
- [ ] From the configured external HA URL on an authorized OpenAI-blocked network, capture evidence that browser requests stay on the HA/proxy origin while App egress reaches OpenAI. Test proxy drop/replay, body limit/chunk resume, cold backup/restore, one App update, and previous-image rollback.
- [ ] Open the PR with factual design/security/verification/rollback notes; request automated review, address findings test-first, require all protected checks, publish signed immutable App image and Integration 0.6.0 release, update accurate GitHub description/topics/tags, and merge without bypassing protection.
- [ ] Stop the Windows Bridge and observe HA operation through the rollback window. Do not delete the VM, workspaces, credentials, scripts, old image, or backup. Mark the goal complete only after post-merge HA smoke checks and repository/main/tag/release state agree.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| App-server schema drift | Generate/check method schemas from the locked binary; typed adapters; fail incompatible updates. |
| Approval reader deadlock | Never block JSONL reader; bounded dispatch; correlation/replay tests. |
| Workspace TOCTOU/symlink escape | `dir_fd`/`O_NOFOLLOW` on Linux, resolved containment, hostile/race tests. |
| HA storage exhaustion | Reservations, quotas, streaming, archive limits, event compaction, log rotation. |
| Tool access to HA/LAN/secrets | Non-root runtime, AppArmor, Bubblewrap filesystem/network namespace, real HA fatal self-test. |
| HACS/App version skew | Independent versions, explicit API range, bidirectional compatibility fixtures. |
| Proxy disconnect/body limit | Heartbeats, durable cursors, 8 MiB idempotent chunks, ranged downloads. |
| Upstream supply-chain compromise | Sigstore identity/transparency verification, digests, monotonic stable releases, immutable signed image. |
| Bad App update | Cold backup, retained tags, post-update readiness/repair, manual restore; no broad rollback privilege. |

## Retirement

- Canonical owner after acceptance: protected Supervisor App.
- Compatibility carrier through 0.6.x: external unversioned Bridge adapter plus Windows scripts/docs.
- Keep reason: immediate rollback while real HA operation soaks.
- Retirement trigger: accepted HA sandbox, remote flow, backup/restore, and update/rollback plus explicit user removal of the VM fallback.
- Deletion boundary: no automatic VM stop/delete, workspace move, auth copy, or history conversion.

## ADR and Baseline Completion Questions

- Did the real HA kernel/AppArmor/Bubblewrap result confirm the proposed tool boundary?
- Which target architecture and external access path were proven?
- Did API v1 remain the sole canonical contract with v0 only as a bounded adapter?
- Did one app-server process remain the owner of auth/models/turns/approvals after implementation?
- Are App/Integration/Codex update and rollback responsibilities observable and non-duplicated?
- Has the Windows owner been stopped, and is its deletion trigger still accurate?
