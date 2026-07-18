# Home Assistant-native Codex — Todo Checkpoint

## TodoCheckpointDraft

- **State:** `0.8.11` account-neutral chat candidate under local verification.
- **Current todo:** Complete repository gates, protected review, immutable signed
  publication, coordinated target-HA installation, and a bounded existing-chat
  prompt under the currently signed-in ChatGPT account.
- **Active slice:** `Herb/0.8.11-account-rebind` starts from signed `0.8.10`.
  It privately associates native Codex thread handles with one ChatGPT owner,
  detaches stale handles after an account change, and deliberately leaves every
  Home Assistant chat/project/transcript/file/workspace/automation record static.
- **Completed:** Producer, storage-owner, runtime-consumer, lifecycle, privacy,
  queued-state, archived-chat, scheduled `continue_thread`, authoritative
  account-update, in-flight read/login invalidation, identity-less fail-closed,
  promoted-queue admission, atomic prompt-lease continuity capture,
  deletion-safe auth lock ordering, missing-historical-workspace recovery,
  stale-login generation recovery, active-steer admission, blocked-poll auth
  deduplication, owner-aware provider catalogues, atomic automation target
  admission/replay, browser-safe thread and interaction projections,
  historical-event read-time redaction, and recovered-checkpoint coverage.
  The fresh complete Bridge matrix passed with
  `1496 passed, 218 skipped` in 299.39 seconds; the final focused
  account-neutral/runtime/privacy slice passed `295 passed, 3 skipped`, and
  the complete Runtime Broker suite passed `195 passed, 3 skipped`. The exact
  Linux/Python 3.14 Home Assistant Integration matrix
  passed `319 passed` against Home Assistant 2026.7.2.
  Frontend lint, `320` unit tests, the production build, and `22` browser tests
  also passed.
- **Evidence refs:** `90-evidence.md` records signed `0.8.10` publication and
  bounded `0.8.11` local evidence. The implementation plan is
  `../../plans/2026-07-17-account-agnostic-chats.md`.
- **Release state:** `0.8.10` is the latest signed release: main commit
  `9fdfe53671d4773f3e955abb2720b408d874cd29`, publication `29613120991`,
  digest
  `sha256:736250059793d068bec0bb94dceec582c1272b82b18d837158857d2ca946b4c0`,
  and provenance attestation `35904448`.
- **Open boundaries:** `0.8.11` CI/publication/install/account-switch acceptance;
  real Nabu Casa/Cloudflare captures; destructive cold restore and arbitrary
  retained-image rollback; and browser-worker isolation/attestation.
- **Next step:** finish all local gates, push the reviewed branch, merge only
  after required checks pass, then install Integration first and App second.

## Workflow state

- **Package manager:** npm via root `package-lock.json`.
- **Frontend:** framework-free JavaScript Web Component bundled with esbuild.
- **Runtime:** App/Integration/panel `0.8.11`, Bridge `0.7.6`, locked Codex
  `0.144.5`.
- **Verification:** frontend lint/unit/build; Ruff; full Bridge pytest; release
  projection and Codex-lock checks; protected Linux/HA/browser/App-build CI;
  signed digest/provenance/SBOM; target-HA smoke.

## ResumeStateHint

- **Repository:** repository root.
- **Worktree:** `.worktrees/0811-account-rebind`.
- **Branch:** `Herb/0.8.11-account-rebind`.
- **Integrated main head:** `9fdfe53671d4773f3e955abb2720b408d874cd29`.
- **Original checkout:** contains unrelated user changes and must remain
  untouched.
- **Required readback:** `AGENTS.md`, `CONTEXT.md`, the account-neutral plan,
  current Git/GitHub release state, and the final `90-evidence.md` section.

## DriftCheckDraft

- **Intent alignment:** browser traffic remains on Home Assistant; only the
  private App/Bridge contacts Codex/OpenAI.
- **Canonical ownership:** the auth coordinator owns authoritative
  `account/read`; Bridge storage owns the persisted provider-thread handle.
- **Compatibility:** the public auth/thread models and Integration API do not
  gain an account identifier or account-specific chat partition. Public thread
  responses now explicitly omit private provider/runtime continuity fields.
- **Privacy:** email is used only transiently to derive a keyed opaque marker;
  neither value nor the Bridge secret enters browser APIs, events, diagnostics,
  logs, or release artifacts.
- **Failure behavior:** a binding failure keeps auth unavailable. The private
  binding file is written last, so interruption repeats an idempotent detach
  instead of blessing a stale handle.
- **Concurrency behavior:** a newer account hint invalidates an account check
  already in flight. Queued prompts recheck authoritative admission when
  promoted and stop locally before any provider request if ownership changed.
- **Admission linearization:** a new prompt reserves runtime ownership before
  its final auth check and storage reload. An account rebind that wins first is
  observed by the prompt; one that loses cannot detach provider continuity
  until the prompt lease is released.
- **Lock ordering:** potentially reconciling auth admission runs without the
  broker lock. The broker then repeats started, deletion, and idempotency checks
  before mutation; its final check is safe because the prompt lease already
  excludes every account-binding path.
- **Runtime ordering:** crash recovery settles before authoritative account
  binding. A changed account therefore removes any provider ID restored from a
  nonterminal checkpoint before Home Assistant begins serving requests.
- **Migration:** first `0.8.11` observation detaches legacy unowned handles once;
  same-account native continuity is retained afterward, while changed accounts
  start a fresh provider conversation in the same local chat.
- **Identity-less behavior:** the locked app-server email field is nullable.
  When it is absent, the Bridge uses only a private unverified sentinel to
  detach provider continuity and keeps UI/automation admission auth-blocked;
  no credential-derived identity is guessed or exposed.
- **Release discipline:** `0.8.11` remains a source candidate until protected
  CI, immutable publication, and target acceptance all complete.
