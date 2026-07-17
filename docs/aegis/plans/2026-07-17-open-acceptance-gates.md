# Open acceptance gates implementation plan

## Goal

Close the five explicitly open Home Assistant-native Codex gates without
weakening the browser -> Home Assistant -> private App trust boundary:

1. make selected-workspace PDF Files discovery and preview independent of
   unrelated aggregate workspace debris;
2. prove ChatGPT-account image generation from prompt through private artifact
   publication and Home Assistant preview/download;
3. prove the same provider-neutral panel/Integration transport through Nabu
   Casa and a Cloudflare-compatible reverse proxy;
4. prove cold backup/restore and retained previous-image recovery on the target
   Home Assistant; and
5. add the secure App-owned browser worker specified by ADR 0006.

The current signed `0.8.3` release remains the rollback point. No release or
target-HA acceptance claim expands until its corresponding real-system check
passes.

## Working boundary

- Branch: `Herb/0.8.4-acceptance-foundations`
- Worktree: `.worktrees/090-open-acceptance`
- Starting commit: `670ef649eeafaa4c5840206c22db1d9b6b1a2c7d`
- The original main worktree and its unrelated modified/untracked files remain
  untouched.
- Browser requests remain same-origin Home Assistant requests. App/Bridge,
  Codex, browser-worker, MCP, and OpenAI endpoints never become browser-visible.
- ChatGPT device authentication remains the only OpenAI credential path.
- A failed browser sandbox, egress policy, restore verification, or capability
  negotiation is a stop condition, not permission for an unsafe fallback.

## Baseline

- Current-main Linux/Python 3.14 focused artifact/workspace/upload baseline:
  `65 passed`.
- Current-main Windows run is intentionally platform-gated for secure
  descriptor operations; the Home Assistant pytest plugin cannot import on
  Windows because it requires `fcntl`.
- Frontend and complete Python baselines are rerun after the first RED/GREEN
  slice and before any release.

## Task 1: Repair the typed PDF Files 409 at the owning boundaries

**Files**

- Modify `bridge_service/src/codex_bridge_service/storage.py`
- Modify `bridge_service/tests/test_storage_resources.py`
- Modify `bridge_service/tests/test_artifact_workspace.py`
- Modify `codex_bridge_app/rootfs/usr/local/bin/sandbox-self-test`
- Modify `bridge_service/tests/test_app_sandbox_contract.py`
- Update `docs/aegis/work/2026-07-12-home-assistant-native-codex/90-evidence.md`

**Root-cause contract**

Artifact listing and archive creation read one selected, already-existing
workspace and publish only private metadata/output. They must enforce that
workspace's entry/byte/type boundary, but must not become permanently
unavailable because a different workspace or a stale self-test locator cannot
be opened. Aggregate quota uncertainty must continue to fail closed for actual
workspace mutations. The root-owned sandbox self-test must also remove only
its exact stale nonce-shaped debris on the next root-side initialization.

**RED**

- Reproduce a regular PDF in the selected workspace plus an unreadable or
  unopenable peer/self-test entry beneath the aggregate root.
- Assert list and archive use the selected bounded manifest and succeed.
- Assert workspace mutation reservation still fails closed while aggregate
  usage cannot be measured.
- Assert stale cleanup accepts only exact `.sandbox-self-test-<32 hex>` and
  `.sandbox-sibling-<32 hex>` locators, rejects symlinks/renames/foreign names,
  and is idempotent.

**GREEN and retirement**

- Remove the read-only artifact/archive dependency on the aggregate quota scan;
  keep aggregate enforcement at mutation reservations/growth observation.
- Add root-side exact-identity stale self-test cleanup before creating a new
  probe. Retire the temporary perpetual `filesystem_scan` retry behavior for
  this known debris case while retaining typed transient errors for genuine
  selected-workspace races.
- Run the focused Linux suite, App sandbox/startup tests, Integration error
  mapping tests, frontend Files/PDF tests, then a real target-HA PDF
  list/archive/preview/download acceptance.

## Task 2: Complete image-generation acceptance

**Files**

- Modify `bridge_service/src/codex_bridge_service/codex_app_server.py`
- Modify `bridge_service/src/codex_bridge_service/runtime_broker.py`
- Modify `bridge_service/src/codex_bridge_service/storage.py`
- Modify `bridge_service/tests/test_codex_app_server.py`
- Modify `bridge_service/tests/test_runtime_broker.py`
- Modify `bridge_service/tests/test_generated_images.py`
- Modify `frontend/src/codex-bridge-panel.js`
- Modify `frontend/src/desktop-features.js`
- Modify `frontend/test/artifact-preview.test.js`
- Modify `frontend/test/desktop-features.test.js`
- Add `tests/acceptance/image-generation.md`

**Contract**

- Advertise `image_generation_v1` only when the signed-in ChatGPT runtime
  reports both `imageGeneration` and `namespaceTools`.
- Inject the generation-aware capability authority into the runtime broker and
  refuse private publication of unsolicited/stale `imageGeneration` items when
  that verified gate was not true for the owning run. Do not claim that Codex
  can prevent provider-side invocation unless the locked app-server exposes a
  real tool-disable control.
- Accept only the typed app-server `imageGeneration` item, bounded base64/data
  URL content, PNG/JPEG/WebP magic, bounded dimensions/decoded pixels, and a
  permitted non-animated frame structure. Publish through the private artifact
  boundary with no API key, external image URL, SVG, HTML, decompression bomb,
  or browser-to-provider request.
- Stream safe progress, terminal failure, and the final artifact exactly once;
  preview via the existing authenticated Home Assistant artifact route.

**Verification**

- Extend contract tests for capability loss, account/logout generation change,
  malformed/duplicate/oversized or over-pixel images, crash replay, event
  ordering, and redaction.
- Extend frontend tests for progress, failure, auto-selection, local Blob URL
  lifetime, download, keyboard/mobile behavior, and hostile metadata.
- On target HA, generate one image using the connected ChatGPT account and
  prove activity -> private artifact -> preview -> download, with browser
  network traffic limited to the Home Assistant origin.

## Task 3: Add provider-neutral remote-path acceptance

**Files**

- Modify `tests/transport/compose.yaml`
- Modify `tests/transport/proxy.conf`
- Modify `tests/transport/e2e.spec.js`
- Add `tests/transport/remote-path.spec.js`
- Add `scripts/acceptance/collect_remote_acceptance.py`
- Add `tests/acceptance/test_remote_path_contract.py`
- Add `docs/acceptance/remote-access.md`
- Update `docs/remote-access.md`
- Update `README.md`

**Contract**

One parameterized harness exercises LAN, Nabu-Casa-shaped, and
Cloudflare/reverse-proxy-shaped origins without provider branches in the
Integration. It covers Home Assistant authentication, WebSocket subscribe and
reconnect/replay, streaming prompt output, 8 MiB resumable chunks, range
download, timeout/body-limit behavior, and cancellation. Browser evidence may
contain origins and status categories but never tokens, cookies, private URLs,
prompts, or response bodies.

**Verification**

- Unit/contract tests reject absolute App/Bridge URLs, redirects, unsafe
  forwarded headers, cross-origin fetches, and duplicate replay.
- Container proxy tests exercise connection drops, chunk retry, 206/416, and
  same-origin-only browser requests.
- Real acceptance uses the configured Nabu Casa and/or Cloudflare external HA
  URLs from an authorized external network; App-side evidence separately shows
  OpenAI egress originating from HA. Private URLs are recorded only as redacted
  evidence.

## Task 4: Prove cold restore and retained-image rollback

**Files**

- Add `scripts/acceptance/collect_recovery_acceptance.py`
- Add `tests/acceptance/test_ha_recovery_acceptance.py`
- Add `docs/acceptance/cold-restore.md`
- Update `docs/backup-restore.md`
- Update `codex_bridge_app/DOCS.md`
- Update `codex_bridge_app/CHANGELOG.md`
- Update `docs/aegis/work/2026-07-12-home-assistant-native-codex/90-evidence.md`

**Contract**

- The repository helper is read-only: it captures non-secret pre/post identity
  and validates a redacted evidence manifest; it never creates, restores,
  deletes, exports, upgrades, downgrades, or restarts anything.
- Capture non-secret preflight identity: App/Integration/Bridge/Codex versions,
  Supervisor UUID, account state category, workspace/chat sentinels, current
  immutable digest, retained previous version, and backup identifier.
- The administrator runbook creates a cold backup, performs a controlled
  reversible mutation, restores the backup, and then uses the collector to
  verify all sentinels plus readiness/sandbox/auth state.
- The administrator runbook exercises one update to a newer immutable image and
  recovery to the retained previous image only through Home Assistant's
  supported UI/API path. The App does not gain broad Supervisor rollback
  permission and the evidence keeps cold restore distinct from arbitrary image
  selection.
- Abort before mutation when no backup, retained image, healthy rollback target,
  or watchdog/reconnect path is available.

**Verification**

- Offline tests cover pre/post manifest validation, wrong target, moved
  version/digest, missing backup evidence, secret redaction, and fail-closed
  collection.
- The destructive target-HA phase runs only after explicit preflight output
  identifies the test HA and verified rollback point. Evidence records outcomes,
  not credentials or private endpoints.

## Task 5: Implement the secure App-owned browser worker

**Files**

- Update `docs/aegis/adr/0006-preview-and-browser-boundary.md`
- Add `bridge_service/src/codex_bridge_service/browser_contract.py`
- Add `bridge_service/src/codex_bridge_service/browser_broker.py`
- Add `bridge_service/src/codex_bridge_service/routes/browser.py`
- Add `bridge_service/tests/test_browser_contract.py`
- Add `bridge_service/tests/test_browser_broker.py`
- Add `codex_bridge_app/rootfs/usr/local/libexec/codex-bridge/browser_worker.py`
- Add `codex_bridge_app/rootfs/usr/local/libexec/codex-bridge/browser_policy.py`
- Modify `codex_bridge_app/Dockerfile`
- Modify `codex_bridge_app/apparmor.txt`
- Modify `codex_bridge_app/rootfs/usr/local/bin/sandbox-self-test`
- Modify `bridge_service/tests/test_app_sandbox_contract.py`
- Modify `bridge_service/tests/test_app_build_context.py`
- Modify `bridge_service/src/codex_bridge_service/feature_capabilities.py`
- Modify `bridge_service/src/codex_bridge_service/app.py`
- Modify `custom_components/codex_bridge/bridge_api.py`
- Modify `custom_components/codex_bridge/websocket_api.py`
- Modify `tests/custom_components/codex_bridge/test_bridge_api.py`
- Modify `tests/custom_components/codex_bridge/test_websocket_api.py`
- Modify `frontend/src/codex-bridge-panel.js`
- Add `frontend/src/browser-view.js`
- Add `frontend/test/browser-view.test.js`
- Extend `frontend/e2e/panel.spec.js`

**Contract**

- The App owns one fixed worker executable and a narrow typed protocol. The
  model can request only high-level navigation, bounded DOM/text inspection,
  screenshot, PDF render, click, type, select, wait, and close operations.
- No arbitrary JavaScript/eval, CDP/WebDriver socket, shell, headers, cookies,
  downloads outside the private artifact pipeline, persistent profile, or
  browser-facing worker endpoint exists.
- Every session uses a new private profile and bounded lifetime/resources.
  Navigation rejects credentials, redirects to disallowed schemes, loopback,
  link-local, RFC1918/ULA, HA/Supervisor/App-private names, and DNS rebinding.
  Connection-time destination enforcement and Chromium sandbox/AppArmor proof
  are mandatory. The capability remains absent/fatal when unavailable.
- Browser screenshots and PDFs enter the existing private artifact path and are
  rendered in the panel only through authenticated Home Assistant routes.

**Phases**

1. Freeze the typed contract and threat-model tests; no Chromium package yet.
2. Implement a fake-worker broker and end-to-end Integration/panel flow.
3. Add the pinned Chromium runtime, AppArmor/namespace/seccomp/egress policy,
   self-test, resource limits, and ephemeral cleanup.
4. Run hostile redirect/DNS/private-network/file/download/CDP/eval tests in the
   built image and on target HA. Do not advertise the capability before these
   pass.

## Task 6: Review, release, and evidence

**Files**

- Modify release-owned version/changelog files through
  `scripts/sync_app_release.py`
- Update `CONTEXT.md`, `README.md`, `docs/aegis/INDEX.md`, the checkpoint, and
  `90-evidence.md`

**Verification sequence**

1. Per-task RED/GREEN tests and fresh implementation/spec/code-quality reviews.
2. `npm ci`, lint, full unit suite, deterministic build, and Playwright.
3. Ruff, Python compilation, full Bridge and Linux Integration suites.
4. App package/startup/sandbox/build, transport container, release sync/lock,
   secret scan, and diff hygiene.
5. Pull request and protected CI; signed immutable image, SBOM, provenance, and
   paired HACS release verification.
6. Target-HA install and only the acceptance claims that actually pass.

## Stop conditions

- Selected-workspace safety, quota mutation enforcement, or sandbox proof
  regresses.
- Image generation requires an API key or browser/provider direct request.
- A remote transport flow exposes the App/Bridge address or credential.
- Recovery lacks a verified cold backup and retained rollback target.
- Chromium cannot be both sandboxed and connection-time denied from HA,
  Supervisor, App-private, LAN, and other private destinations.
