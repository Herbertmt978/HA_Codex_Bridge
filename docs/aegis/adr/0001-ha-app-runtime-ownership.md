# ADR 0001: Home Assistant App runtime ownership

**Status:** Accepted for the repository implementation snapshot on 2026-07-14.

## Decision

The Supervisor-managed Codex Bridge App is the canonical owner of the API v1
runtime. In the Home Assistant profile, one app-server client owns account,
model, runtime, approval, and turn coordination. The Integration is the
Home Assistant-facing client and browser boundary; it is not a second Codex
runtime owner.

The separately operated Bridge remains an explicit external-legacy adapter,
not a parallel primary runtime. Its `BridgeRunner` and legacy process paths
must not be selected by Home Assistant profile composition.

## Evidence and consequences

`bridge_service/src/codex_bridge_service/app.py` constructs the app-server
client for the Home Assistant profile and rejects `BridgeRunner` there.
`tests/custom_components/codex_bridge/test_config_flow.py` covers the primary
Supervisor flow and its explicit external alternative. The state of this
decision and its remaining operational gates are recorded in the
[implementation baseline](../baseline/2026-07-14-ha-native-implementation-baseline.md).

This is an ownership decision, not a claim that a target Home Assistant
installation has completed release acceptance.
