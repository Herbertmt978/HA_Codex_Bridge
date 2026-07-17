# Image-generation acceptance

This runbook closes only the Home Assistant image-generation path. It does not
authorize an API key, a browser-to-provider request, an external image URL, or
an SVG/HTML artifact.

## Automated contract

- `image_generation_v1` is advertised only when the current Codex app-server
  generation verifies both `imageGeneration` and `namespaceTools`.
- The runtime broker records the capability revision that owned the turn. A
  logout, account change, capability loss, or app-server generation change
  invalidates that authority; a later capability probe cannot reactivate an
  older turn's token.
- Only a correlated, completed `imageGeneration` item can reach the artifact
  writer. Duplicate, unsolicited, stale, malformed, active-content, animated,
  over-dimension, over-pixel, and decompression-bomb results fail closed.
- Accepted PNG, JPEG, and WebP bytes remain private and are exposed to the panel
  only through the authenticated Home Assistant artifact route.

## Target Home Assistant procedure

1. Confirm App, Integration, Bridge, and Codex versions and retain the immutable
   App digest in the private acceptance record.
2. Confirm **Sign in with ChatGPT** is complete and readiness advertises
   `image_generation_v1`. Do not add an OpenAI API key.
3. In a fresh chat, ask Codex to generate one innocuous raster image. Record the
   safe activity stage and the terminal generated-artifact event, but not the
   prompt, account identity, or raw base64 result.
4. Open **Files**, preview the result, download it, and verify the downloaded
   MIME type and digest match the private artifact metadata.
5. Capture a browser network trace showing that panel requests stayed on the
   Home Assistant origin. Redact the origin, cookies, tokens, query strings,
   prompts, and response bodies before retaining evidence.
6. Sign out or invalidate the capability during a separate controlled run and
   verify a stale completion cannot add an artifact or reactivate the old turn.

## Pass boundary

Acceptance requires activity -> one private artifact -> preview -> download,
with no direct browser/provider request and no duplicate publication. Until a
real target-HA run records that evidence, image generation remains implemented
but unaccepted.
