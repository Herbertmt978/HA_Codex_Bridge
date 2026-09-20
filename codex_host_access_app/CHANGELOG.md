# Changelog

## 1.1.0

- First experimental release for amd64 Home Assistant OS.
- Runs bounded root commands through the privately paired Codex Bridge.
- Requires Protection mode to be disabled and a separate acknowledgement in
  the Bridge UI. Installation alone never enables access for Codex tasks.
- Supports cancellation, revocation and explicit selection for scheduled work.
- Uses the same immutable image as Codex Bridge App 1.1.0, with a separate
  startup role. Ordinary Codex sessions do not run inside this privileged App.
