# ADR 0002: Home Assistant-origin transport and trust

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

The browser communicates only with authenticated Home Assistant WebSocket and
HTTP surfaces. The Integration owns the private App bearer credential and
validates the discovered App endpoint, Supervisor identity, authenticated
readiness, and negotiated API v1 before it forwards a request. Browser
credentials and private App tokens do not cross that boundary.

The manual **External Bridge (advanced)** path is a separate, explicit v0
compatibility mode. It is not discovered as an App and cannot silently become
the v1 transport.

## Evidence and consequences

The protocol constants and client validation live in
`custom_components/codex_bridge/const.py` and `bridge_api.py`; discovery and
v0/v1 selection are covered by `test_config_flow.py` and `test_protocol.py`.
The [implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md)
records the boundary and its non-claims.

Local transport coverage does not prove operation from an external
OpenAI-blocked network; that remains a release-acceptance gate.
