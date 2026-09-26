# Changelog

## 1.8.4

- Keeps the optional Host Access App version aligned with Codex Bridge `1.8.4`.
  Host-access permissions and the separate grant are unchanged.

## 1.8.3

- Keeps the optional Host Access App version aligned with Codex Bridge `1.8.3`.
  Host-access permissions and the separate grant are unchanged.

## 1.8.2

- Keeps the optional Host Access App version aligned with Codex Bridge `1.8.2`.
  It does not change host-access permissions or the separate grant.

## 1.8.1

- Keeps the optional Host Access App version aligned with Codex Bridge `1.8.1`.
  It does not change host-access permissions or the separate grant.

## 1.8.0

- Keeps the optional Host Access App version aligned with Codex Bridge `1.8.0`.
  It does not change host-access permissions or the separate grant.

## 1.7.3

- Keeps the optional Host Access App version aligned with Codex Bridge `1.7.3`.
  It does not change host-access permissions or the separate grant.

## 1.7.2

- Keeps the optional Host Access App version aligned with Codex Bridge `1.7.2`.
  It does not change host-access permissions or the separate grant.

## 1.7.1

- Bundles the Sigstore-verified Codex runtime `0.157.0`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.13.0` without changing its Integration API compatibility.

## 1.7.0

- Keeps the optional Host Access App version aligned with Codex Bridge `1.7.0`.
  It does not change the separate host-access grant or permissions.

## 1.6.4

- Keeps the optional Host Access App version aligned with Codex Bridge `1.6.4`.
  It does not change the separate host-access grant or permissions.

## 1.6.3

- Keeps the optional Host Access App aligned with Codex Bridge `1.6.3`.
  Host access still requires separate installation and consent.

## 1.6.2

- Keeps the optional Host Access App aligned with Codex Bridge `1.6.2`.
  Host access still requires separate installation and consent.

## 1.6.1

- Keeps the optional Host Access App aligned with Codex Bridge `1.6.1`.
  Host access still requires separate installation and consent.

## 1.6.0

- Keeps the optional Host Access App aligned with Codex Bridge `1.6.0` and
  Bridge `0.13.0`. Host access still requires separate installation and consent;
  this release does not enable it automatically.

## 1.5.1

- Bundles the Sigstore-verified Codex runtime `0.156.1`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.12.0` without changing its Integration API compatibility.

## 1.5.0

- Keeps the optional Host Access App version aligned with Codex Bridge 1.5.0.
- Host permissions and the explicit administrator acknowledgement are unchanged.

## 1.4.0

- Keeps the optional Host Access App version aligned with Codex Bridge 1.4.0.
- Host Access behaviour and amd64-only support are unchanged.

## 1.3.1

- Keeps the optional Host Access App version aligned with Codex Bridge 1.3.1.
- Host Access behaviour and amd64-only support are unchanged.

## 1.3.0

- Keeps the optional Host Access App version aligned with Codex Bridge 1.3.0.
  Host access permissions and behaviour are unchanged. MCP token authentication
  does not require this extra App.

## 1.2.0

- Uses the shared image paired with App and Integration 1.2.0. Host access
  permissions and acknowledgement requirements are unchanged. Local MCP is
  configured separately in Codex Bridge and does not require this companion.

## 1.1.2

- Uses the shared image paired with Integration and panel 1.1.2. Host access
  permissions and acknowledgement are unchanged.
- Bundles the Sigstore-verified Codex runtime `0.155.1`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.8.0` without changing its Integration API compatibility.

## 1.1.1

- Uses the updated shared App image with Uvicorn `0.53.0` and repaired build
  dependencies. Host access permissions and acknowledgement are unchanged.
- Bundles the Sigstore-verified Codex runtime `0.155.1`.
- Keeps model and reasoning-level choices dynamically discovered from that runtime.
- Bundles Bridge `0.8.0` without changing its Integration API compatibility.

## 1.1.0

- First experimental release for amd64 Home Assistant OS.
- Runs bounded root commands through the privately paired Codex Bridge.
- Requires Protection mode to be disabled and a separate acknowledgement in
  the Bridge UI. Installation alone never enables access for Codex tasks.
- Supports cancellation, revocation and explicit selection for scheduled work.
- Uses the same immutable image as Codex Bridge App 1.1.0, with a separate
  startup role. Ordinary Codex sessions do not run inside this privileged App.
