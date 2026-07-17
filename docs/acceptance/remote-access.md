# Remote-path acceptance evidence

This runbook validates the browser-to-Home-Assistant boundary through three
route shapes without adding provider-specific Integration behavior. The
repository collector is offline: it has no Home Assistant or network client,
does not send requests, and may write only one atomic local result manifest.

Synthetic coverage is not real external acceptance. At the time of writing,
the provider-neutral Docker harness runs three isolated upstreams through LAN,
post-TLS-termination Nabu-shaped, and post-TLS-termination Cloudflare-shaped
proxy profiles. Each profile passes prompt retry, separate upload cancellation,
8 MiB chunk replay, Range resume/416, WebSocket reconnect, and trusted forwarded
header normalization. The offline three-profile schema tests also pass, but
authorized captures from an actual external network remain pending.

## Evidence boundary

Create one redacted JSON bundle containing exactly these profiles:

- `lan`, observed from `home-network` and classified `lan`;
- `nabu-shaped`, observed from `external-network` and classified `external`;
- `cloudflare-shaped`, observed from `external-network` and classified
  `external`.

The shaped labels describe the route presented to Home Assistant. They do not
select Nabu Casa, Cloudflare, or any other runtime branch. Every capture must
declare `runtime_route: "provider-neutral"`.

Never save a HAR, browser storage dump, raw origin, URL, prompt, response body,
token, cookie, authorization header, App/Bridge address, upstream address, or
OpenAI endpoint as evidence. Record only relative Home Assistant paths, status
categories, bounded counters, timestamps, UUIDs, and SHA-256 fingerprints.
Collector errors are intentionally generic and do not echo rejected values.

## What a capture must prove

For each profile, use one fresh browser session against the same visible Home
Assistant origin and verify:

1. Authenticated `GET /api/` stays on that origin and returns `200`.
2. `/api/websocket` authenticates, reconnects at least once, resumes with
   strictly increasing event sequences, and emits no duplicate replay.
3. The v1 upload flow uses relative
   `/api/codex_bridge/threads/...` paths for create, status, an exact 8 MiB
   chunk, complete, and cancellation of a separate upload.
4. The first chunk is committed before its response is lost. Retrying the same
   chunk produces exactly two attempts, one commit, one response loss, and one
   idempotent retry.
5. Artifact download returns `206` with `If-Range`, disconnects after at least
   one byte, resumes at that exact byte offset with another `206`, and returns
   `416` for an unsatisfied range.
6. Every request has zero redirects and zero cross-origin requests. Browser,
   flow, and capture origin fingerprints all match.

For a real run, the three captures require distinct canonical UUIDs, distinct
timezone-aware RFC 3339 timestamps, and distinct origin fingerprints. Generate
and hash these locally. Do not send private material to this repository or any
external model. A synthetic capture uses the same shape with
`evidence_kind: "synthetic"`; its result always leaves external acceptance
`pending`.

## Strict input format

The top-level object has schema version `1` and exactly three captures. The
excerpt below shows one complete capture but is not by itself an accepted
bundle; place three such objects in `captures`, using the other two
profile/network values and fresh capture metadata:

```json
{
  "schema_version": 1,
  "captures": [
    {
      "schema_version": 1,
      "capture": {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "captured_at": "2026-07-17T10:00:00Z",
        "evidence_kind": "synthetic",
        "origin_fingerprint": "<64-lowercase-hex>"
      },
      "route_profile": "lan",
      "network": {
        "classification": "lan",
        "observed_from": "home-network"
      },
      "runtime_route": "provider-neutral",
      "browser": {
        "origin_fingerprint": "<same-64-lowercase-hex>",
        "redirects": 0,
        "cross_origin_requests": 0
      },
      "flows": {
        "api": {
          "method": "GET",
          "path": "/api/",
          "status": 200,
          "origin_fingerprint": "<same-64-lowercase-hex>",
          "redirects": 0,
          "cross_origin_requests": 0
        },
        "websocket": {
          "path": "/api/websocket",
          "origin_fingerprint": "<same-64-lowercase-hex>",
          "auth": "passed",
          "reconnects": 1,
          "event_sequences": [7, 8],
          "duplicate_events": 0,
          "redirects": 0,
          "cross_origin_requests": 0
        },
        "upload": {
          "create": {"method": "POST", "path": "/api/codex_bridge/threads/thr_acceptance/uploads", "status": 201, "origin_fingerprint": "<same-64-lowercase-hex>", "redirects": 0, "cross_origin_requests": 0},
          "status": {"method": "GET", "path": "/api/codex_bridge/threads/thr_acceptance/uploads/upl_acceptance", "status": 200, "origin_fingerprint": "<same-64-lowercase-hex>", "redirects": 0, "cross_origin_requests": 0},
          "chunk": {"method": "PUT", "path": "/api/codex_bridge/threads/thr_acceptance/uploads/upl_acceptance/chunks/0", "status": 200, "origin_fingerprint": "<same-64-lowercase-hex>", "redirects": 0, "cross_origin_requests": 0, "chunk_bytes": 8388608, "attempts": 2, "commits": 1, "response_losses": 1, "idempotent_retries": 1},
          "complete": {"method": "POST", "path": "/api/codex_bridge/threads/thr_acceptance/uploads/upl_acceptance/complete", "status": 201, "origin_fingerprint": "<same-64-lowercase-hex>", "redirects": 0, "cross_origin_requests": 0},
          "cancel": {"method": "DELETE", "path": "/api/codex_bridge/threads/thr_acceptance/uploads/upl_cancel", "status": 200, "origin_fingerprint": "<same-64-lowercase-hex>", "redirects": 0, "cross_origin_requests": 0}
        },
        "artifact": {
          "path": "/api/codex_bridge/threads/thr_acceptance/artifacts/art_acceptance",
          "origin_fingerprint": "<same-64-lowercase-hex>",
          "redirects": 0,
          "cross_origin_requests": 0,
          "initial_status": 206,
          "if_range": true,
          "interrupted_after_bytes": 16,
          "resume_range_start": 16,
          "resume_status": 206,
          "unsatisfied_status": 416
        }
      }
    }
  ]
}
```

Unknown or missing fields fail closed. Input must be a regular, non-symlink,
UTF-8 JSON file no larger than 256 KiB; duplicate keys, non-finite numbers,
absolute/cross-origin paths, redirects, provider-specific runtime routes,
duplicate commits/events, and malformed or oversized values are rejected.

## Validate locally

Run the isolated three-profile transport harness:

```text
docker compose -f tests/transport/compose.yaml up --build --abort-on-container-exit --exit-code-from e2e
docker compose -f tests/transport/compose.yaml down --volumes --remove-orphans
```

The Nabu and Cloudflare profiles begin at the trusted side of synthetic TLS
termination and verify the forwarded scheme/host boundary. They do not replace
an external HTTPS browser run through the actual provider.

Run the collector only after manually redacting and reviewing the bundle:

```text
python scripts/acceptance/collect_remote_acceptance.py --input remote-captures.json --output remote-result.json
```

The output contains only route/network categories, capture timestamps, origin
fingerprints, and SHA-256 fingerprints of capture UUIDs. A failed validation
does not create or replace the result. Every successful offline result says
`"external_acceptance":"pending"`; even three distinct `real`-shaped captures
cannot self-certify their provenance. Only the controlled external runner and
reviewed target evidence may close the external acceptance gate.

Do not change the repository's acceptance claim merely because a hand-written
or synthetic bundle validates. Preserve the actual result under the project's
controlled evidence process and review it before updating release evidence.
