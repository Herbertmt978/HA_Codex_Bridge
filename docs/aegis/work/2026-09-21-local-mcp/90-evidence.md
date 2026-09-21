# Local MCP release qualification

Candidate: App, Integration and panel 1.2.0; Bridge 0.9.0; Codex 0.155.1.
These are pre-publication results from 21 September 2026. Published images and
signatures are checked separately by the release workflow.

## Automated checks

- Frontend lint and build passed; 384 unit tests passed.
- All 39 Chromium browser scenarios passed, including the updated MCP forms at
  desktop and mobile widths, acknowledgement reset and accessibility checks.
- Linux Integration suite: 342 passed.
- Linux Bridge suite: 1,918 passed, 27 platform-specific skips.
- Root restore checks: eight passed.
- Ruff, Python compilation, release-version synchronisation, the pinned Codex
  lock, HACS validation and hassfest passed.
- LAN, Nabu Casa-shaped and Cloudflare-shaped proxy transport checks passed.
- The amd64 App image built locally before any publishing action.

Commands used include `npm run lint`, `npm run test:unit`, `npm run build`,
`python -m ruff check bridge_service custom_components scripts tests`, the
repository's Linux Integration and Bridge pytest suites,
`python scripts/sync_app_release.py --check` and
`python scripts/update_codex_lock.py --check codex_bridge_app/codex-release.json`.

## Native HAOS checks

Disposable containers on HAOS-DEV used the built candidate with the existing
AppArmor profile and fresh private volumes. No production credentials were copied.
The complete App startup attested the tool sandbox, advertised `mcp_local_v1`,
served authenticated routes and rejected missing credentials. With no account
installed it correctly reported that ChatGPT sign-in was required.

A disposable streamable-HTTP server on the App network exposed one test tool.
Native Codex discovered it and completed a tool call. A new App-server generation
restored the approved local binding after restart; a counter on the test server
confirmed that bootstrap sent no MCP requests before validation. Turning local
access off removed the native binding. With MCP disabled, stale local HTTP and
stdio entries neither connected nor executed and were removed from saved config.

The candidate Integration was also installed on HAOS-DEV. Core configuration
validation and restart passed; Home Assistant served the exact candidate panel
and no Integration errors appeared in the restart log. Its existing 1.1.2 files
were retained for restoration after the test.

Real-socket relay tests also cover trusted/untrusted TLS, hostname verification,
SSE, expired sessions, redirects, mixed DNS answers, changed destinations,
revocation, size limits and incomplete-stream termination.

## Findings and limits

Native testing exposed two assumptions that mocks had missed: effective MCP
config includes default fields, and an empty command-line table does not remove
saved entries. Recognition now accepts only observed safe defaults; startup
explicitly disables saved entries and verifies the effective configuration before
accepting requests. The regression fixtures model recursive merging.

The initial restart attempt was blocked until those corrections passed. Later
image iterations exhausted development disk space; removing this task's image
archives restored enough space, and native startup and restart checks passed again.

Independent consultation was checked against the pinned runtime and native
results. The protocol requires `initialized` before config requests; application
requests remain closed until validation completes. Strict boolean checks remain,
and native results confirmed their types. Existing restrictions on disabled or
unsupported imported configurations were retained.

A fresh public OAuth-provider login and an HAOS cold-backup restore were not
performed for this feature. Existing HTTPS/OAuth compatibility is covered by the
manager tests; root restore tests do not constitute an HAOS cold restore. No
production MCP, browser or Host Access permission was changed.
