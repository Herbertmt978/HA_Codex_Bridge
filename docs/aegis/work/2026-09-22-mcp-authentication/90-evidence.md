# MCP authentication implementation evidence

Development qualification on 22 September 2026. This is not published-release
acceptance. Only synthetic credentials were used.

## Automated checks

- Frontend lint and build passed; all 390 unit tests passed.
- All 41 Chromium scenarios passed. Credential cases cover desktop/mobile
  layouts, keyboard use, axe accessibility, clearing after failed submission,
  replacement/removal and no secrets in browser storage or WebSocket requests.
  Visual inspection confirmed the mobile form remains readable.
- Full Linux Home Assistant Integration suite: 351 passed.
- Full Linux Bridge suite: 1,967 passed, 27 platform-specific skips.
- Root restore checks: eight passed.
- Ruff, release-version synchronisation and the pinned Codex lock check passed.
- The final amd64 App image built from the hermetic staging script.

New cases include malformed/oversized input, administrator and older-App
rejection, reserved/duplicate/injected headers, public/private DNS pinning,
redirects, TLS hostname checks, split literal/JSON credential echoes, restricted
storage, legacy registry migration, failed-write revocation and cancellation.
The exact HTTP-surface inventory now asserts admin protection for the new route.

## Native behaviour

Disposable containers on HAOS-DEV used the candidate image, fresh private volumes
and the existing AppArmor profile. Native Codex discovered a fixture tool and
called it with bearer authentication and with an API-key header. Both modes
rejected incorrect credentials, accepted replacement/rotation, survived restart,
and stopped making upstream requests after credential removal and another
restart. Bootstrap made no requests before validation. Saved native configuration
and public status contained no synthetic secret.

Complete App startup attested the sandbox and advertised `mcp_credentials_v1`.
Authenticated readiness/status routes passed; anonymous access was rejected.
The running App served create, replace and remove credential requests with
write-only response bodies and no-store headers. The final native run produced
no cancellation traceback.

All task containers and volumes were removed from DEV, along with both temporary
images and the transfer archive. DEV retained installed App/Integration 1.1.2,
three backups and 6.3 GiB free. DEV103 and CT105 were restored stopped. Local
Docker was already running and remains running; its task test containers and
transfer server were removed/stopped. Source, logs and the local candidate image
remain available for the later release workflow.

## Review and limits

Independent design review led to disabling core dumps and clarifying that
cancellation cannot undo an upstream action or revoke a provider token.
Implementation review suggestions were checked against the code and tests:

- Retain fail-closed in-memory revocation before disk writes. A failed write
  returns an error; successful persistence is required for a durable change.
  Moving revocation after the write would leave active requests using the old
  credential during a failed operation. Provider revocation remains separate.
- Header values total at most 8 KiB, not 32 KiB. Regex compilation uses Python's
  cache; each stream has its own pending-prefix buffer. Sharing that buffer
  across headers and body would be incorrect.
- GET/DELETE administrator routes do not parse bodies. The bounded POST/PUT
  middleware covers every credential-bearing request; relay protocol requests
  already have their own body limit for all allowed methods.
- Literal and JSON-escaped reflection is tested across every split position.
  This cannot prevent a malicious trusted server from transforming a secret.

No production installation or real provider credential was changed. The native
Integration installation was not replaced; HA view/client behaviour was verified
by the full Linux Integration suite and browser fixtures. Public authenticated
transport used controlled transport/DNS/TLS tests, not a third-party account.
App backups include the plaintext private registry, as disclosed in the UI and
guide. Older backups may retain removed credentials.

The Aegis helper reports pre-existing historical filename/index format drift;
no historical records were rewritten to satisfy its newer schema.

## Release candidate 1.3.0

The owner subsequently requested publication. App/Integration/panel 1.3.0 and
Bridge 0.10.0 contain the same qualified credential implementation; Codex remains
0.155.1. Version projections, notes and current guides were updated together.
The paired candidate reran all 390 frontend, 41 browser, 351 Integration and
1,967 Bridge tests successfully, plus eight root restore and ten Windows updater
checks. HACS, hassfest, reverse-proxy fixtures and the rebuilt image passed.
That exact candidate passed native credential lifecycle and complete App startup
checks on HAOS-DEV. No production deployment was performed.

Publication requires final PR CI, signed image/provenance verification and a
published-image native check. Those results are recorded by the release workflow
and the task's external evidence, not inferred from these pre-publication tests.
