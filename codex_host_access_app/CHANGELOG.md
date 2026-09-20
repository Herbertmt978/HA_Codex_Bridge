# Changelog

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
