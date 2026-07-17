# Account-agnostic Home Assistant chats implementation plan

## Goal

Keep Home Assistant chats, projects, transcripts, artifacts, workspace settings,
and automation targets independent of the currently signed-in ChatGPT account.
When the authenticated ChatGPT identity changes, detach only the private Codex
app-server thread handle so the next prompt starts a provider conversation for
the current account instead of attempting to resume one owned by the previous
account.

## Architecture

`CodexAuthCoordinator` remains the canonical owner of authoritative
`account/read` transitions. `BridgeStorage` remains the canonical owner of the
persisted `codex_thread_id`. The coordinator derives an HMAC-SHA-256 owner
marker from the normalized private account email and the stable Bridge bearer
secret, then sends only that opaque marker to storage. Storage compares it with
a private binding file, detaches stale provider handles under the thread
mutation lock, and writes the new binding only after every detach succeeds.
`account/updated` is treated only as a hint and reconciled with a fresh
generation-checked `account/read` under the runtime gate. If a signed-in
response has no stable identity, a private unverified sentinel detaches
provider continuity and auth-blocks UI and automation admission. Runtime crash
recovery settles before this binding step so it cannot restore an old provider
ID after a changed-account detach.
The public auth model, thread API, event payloads, diagnostics, and logs never
contain the email, Bridge secret, or marker.

## Tech stack

Python 3.14, FastAPI, Pydantic, the existing JSON/outbox storage layer, the
Codex app-server v2 protocol, pytest, Home Assistant Supervisor App packaging,
and the synchronized App/Integration release workflow.

## Baseline/authority refs

- `AGENTS.md`: App owns Codex runtime coordination; browser remains HA-only.
- `CONTEXT.md`: device login is App-owned and local chat/project state is
  private Bridge state.
- `docs/aegis/baseline/2026-07-14-ha-native-implementation-baseline.md`:
  `/data` is the canonical private App state boundary.
- `docs/aegis/adr/0003-private-state-and-device-auth.md`: ChatGPT credentials
  remain App-private.
- User-approved contract on 2026-07-17: chats remain static and work through
  whichever Codex account is currently signed in.

## Compatibility boundary

- Preserve every local thread ID, title, project, transcript event, attachment,
  artifact, workspace, model/thinking override, archive state, and automation
  target.
- Preserve `thread/resume` for an unchanged observed account after the one-time
  migration.
- Use `thread/start` after a confirmed account-owner change.
- Keep sign-out non-destructive for all local state while detaching private
  provider continuity; signing in again continues in the same visible HA chat
  through a fresh provider conversation.
- On the first post-upgrade observation, detach legacy handles once because
  releases through 0.8.10 have no trustworthy owner marker.
- Do not expose a new API field or frontend account partition.

## Ripple Signal Triage

This is a persisted cross-module state transition. Verification therefore
covers the producer (`account/read`), canonical state owner (thread storage),
and runtime consumer (`thread/start` versus `thread/resume`), plus public-state
redaction and automation-target preservation.

## Repair track

Root cause: a local chat persists a Codex app-server `codex_thread_id`, but no
persisted identity says which ChatGPT account owns it. After an account switch,
the runtime blindly calls `thread/resume` with the old account's identifier.
The repair adds one private owner binding and invalidates only the stale remote
handle at the storage owner.

## Retirement track

Retire the implicit invariant that every persisted `codex_thread_id` belongs to
whatever account happens to be signed in. No fallback or duplicate chat owner
is added. The legacy unowned-handle migration remains only as the absence-of-
binding case; after it writes schema version 1, normal same/different-owner
comparison is the sole path.

## Task 1: RED — private owner-marker and auth-transition contract

**Files**

- Modify `bridge_service/tests/test_auth_coordinator.py`
- Add `bridge_service/tests/test_auth_state.py`
- Later modify `bridge_service/src/codex_bridge_service/account.py`
- Later modify `bridge_service/src/codex_bridge_service/auth_coordinator.py`

**Why**

Prove that normalized variants of one email map to one opaque marker, distinct
accounts differ, signed-out/identity-less responses produce no identity, and
the coordinator invokes the binding callback before publishing the ready
state.

**Impact/compatibility**

Existing constructor call sites continue to work because the private marker
secret/listener are optional. `CodexAuthStatusRecord` remains byte-for-byte the
public shape.

**Verification**

`python -m pytest -q bridge_service/tests/test_auth_state.py bridge_service/tests/test_auth_coordinator.py`

- [x] Add tests equivalent to:
  `assert account_owner_marker(_chatgpt_account(email=" Person@Example.Test "), "bridge-secret") == account_owner_marker(_chatgpt_account(email="person@example.test"), "bridge-secret")`, distinct-account inequality, signed-out `None`, and a projection scan which rejects the raw email, secret, and marker.
- [x] Run the focused command and record the expected missing-helper/callback RED failure.
- [x] Add the minimal HMAC helper and optional coordinator binding callback; invoke it only for a validated signed-in account response while the runtime auth lease is still held.
- [x] Rerun the focused command and require green output with no warnings.
- [x] Add authoritative account-update reconciliation and identity-less
  fail-closed coverage without changing the public auth model.
- [ ] Commit only this coherent producer slice after reviewing its diff.

## Task 2: RED — storage detaches provider handles without touching chats

**Files**

- Modify `bridge_service/tests/test_storage.py`
- Later modify `bridge_service/src/codex_bridge_service/storage.py`

**Why**

Make the private owner binding and detach operation crash-safe, idempotent, and
bounded to provider runtime projections.

**Impact/compatibility**

The first valid binding observation migrates unowned 0.8.10-and-earlier thread
handles. A same-marker observation is a no-op. A different marker clears
`codex_thread_id`, `active_turn_id`, `active_run_id`, pending runtime prompts,
runtime error/status projection, and nothing else. The binding file is private
and is written last so a crash retries safely.

**Verification**

`python -m pytest -q bridge_service/tests/test_storage.py -k codex_account_binding`

- [x] Add a fixture chat containing transcript events, attachment/artifact metadata, project/workspace/model settings, archive state, an old `codex_thread_id`, and a stale runtime error; assert first binding detaches only runtime state, the local fields/events remain equal, and the emitted event contains no marker.
- [x] Run the focused command and record the expected missing-method RED failure.
- [x] Implement `BridgeStorage.bind_codex_account(marker)` with strict 64-hex validation, private schema-v1 binding load/write, thread/automation lock ordering, idempotent detach events, and binding-last persistence.
- [x] Rerun the focused command and require green output.
- [ ] Commit only the storage-owner slice after scanning the binding file and events for private data.

## Task 3: RED — end-to-end resume/start and scheduled-target behavior

**Files**

- Modify `bridge_service/tests/test_runtime_broker.py`
- Modify `bridge_service/tests/test_automations.py` if the existing continuation
  fixture does not already exercise the same storage path
- Modify `bridge_service/src/codex_bridge_service/app.py`

**Why**

Prove the user-visible contract rather than only the two units: the same local
chat resumes for the same account, rebinding preserves its local state, and the
next prompt/continuation starts a new provider thread for a different account.

**Impact/compatibility**

The app wires the stable existing `auth_token` only as the HMAC key. No new
credential, API capability, Integration change, or account-specific UI state
is introduced.

**Verification**

`python -m pytest -q bridge_service/tests/test_runtime_broker.py bridge_service/tests/test_automations.py -k "account_binding or account_rebind or continue_thread"`

- [x] Add a broker acceptance test that records `thread/start`, then verifies
  same-owner `thread/resume`, changes the owner marker, and verifies the same
  HA thread next sends `thread/start`; assert transcript/artifacts and the
  local thread ID are unchanged. Cover an automation `continue_thread` target
  through the same detached local record.
- [x] Run the focused command and record the expected wiring/behavior RED failure.
- [x] Wire the coordinator marker callback to `storage.bind_codex_account` in
  `create_app`, keeping all public auth and thread models unchanged.
- [x] Recover runtime checkpoints before account binding and prove a restored
  account-A provider ID is detached before account-B request readiness.
- [x] Block unattended automation dispatch while account ownership is
  unavailable or unverified.
- [x] Rerun the focused command and require green output.
- [ ] Commit the complete repair after producer/owner/consumer diff review.

## Task 4: Release 0.8.11 and target acceptance

**Files**

- Update release-owned version/changelog files with the owning release script,
  then verify the already-versioned candidate with
  `python scripts/sync_app_release.py --check`
- Update `CONTEXT.md`, `README.md`, `docs/aegis/INDEX.md`, and the release
  evidence/checkpoint required by the repository workflow

**Why**

Ship the fix as one synchronized immutable App/Integration release and verify
the exact account-switch path on the user's test Home Assistant.

**Impact/compatibility**

Expected matrix: App/Integration/panel 0.8.11, Bridge version synchronized by
the release script, Codex lock unchanged unless the owning script reports an
already-approved update. The prior signed 0.8.10 image remains the rollback
point.

**Verification**

Run `npm run lint`, `npm run test:unit`, `npm run build`, Ruff, the full Bridge
pytest suite, release sync/lock checks, protected GitHub CI, signed image/SBOM/
provenance checks, then install Integration first/restart and install App on
`192.168.50.20`. In the existing `Test` chat, send the bounded prompt
`Reply with exactly: 0.8.11 account-switch acceptance complete.` and require a
successful response under the currently connected account.

- [x] Add release notes stating the one-time legacy provider-handle detach and
  explicit local-data preservation boundary.
- [ ] Run the complete local release gates and inspect the clean intended diff.
- [ ] Push the `Herb/0.8.11-account-rebind` branch, open/review/merge the PR, and
  wait for all required protected checks.
- [ ] Verify immutable publication evidence and install the coordinated release
  on the test HA with a backup.
- [ ] Verify the existing-chat acceptance, live version matrix, connected plan/
  usage state, local history/artifacts, and no private identity in panel/API/
  logs before recording final evidence.

## Risks and rollback

- Email is the only usable identity field in the locked app-server
  `account/read` contract and is nullable. Identity-less signed-in responses
  therefore detach provider continuity and keep turn admission blocked rather
  than guessing from raw credentials. A future stable opaque account ID could
  remove that conservative continuity loss.
- The first 0.8.11 observation intentionally loses native provider-thread
  continuity once, but retains the complete Home Assistant transcript and
  workspace. Rollback to 0.8.10 retains local data but reintroduces blind
  cross-account resume behavior.
- A failure before the binding file is written repeats an idempotent detach on
  restart; writing the binding last prevents a stale handle from being blessed.

## Self-review

The plan covers the user contract, migration, same-account compatibility,
privacy, scheduled continuations, producer/owner/consumer tests, release gates,
target acceptance, and retirement of the blind-resume invariant. It introduces
no frontend owner, provider-specific chat copy, or unbounded fallback.
