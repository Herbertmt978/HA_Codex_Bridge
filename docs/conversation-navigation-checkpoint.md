# Conversation navigation and image previews

The panel projects retained chat events into a slim vertical rail on desktop and
touch screens. The former disclosure, turn buttons and summary cards have been
removed. Existing run associations group each prompt and response, with
chronological fallback for older events. Marker lengths reflect the original
content within a 6–26 px range. Long histories scroll inside a bounded sticky
track. Visible transcript position owns the current marker.

Hover and keyboard focus expand a marker and show a prompt/response preview.
Delayed dismissal allows stable pointer movement into the card. Desktop click
jumps; a first touch tap opens the preview, whose explicit action jumps. Arrow
keys, Home, End and Escape provide navigation with visible focus and restoration.
Reduced motion removes animation. Cards stay inside the visual viewport and
conversation pane, clear of the composer and Home Assistant header.

Bookmarks persist in browser local storage, scoped by the existing HA user
preference key and selected chat. Only bounded numeric turn anchors are stored;
prompts, responses, provider IDs and credentials are not. Bookmarks do not
synchronise between devices.

The existing 25,000-event retention limit and 12,500-turn projection cap remain.
Cached projections avoid work for unchanged histories and irrelevant streaming
events. Animation frames coalesce scroll work; cached anchor positions locate
the current marker. Keyed controls and preview actions preserve focus across
relevant streaming updates without moving the transcript scroll position.

Uploaded, generated and workspace PNG, JPEG, WebP and GIF images have decoded
inline thumbnails and enlarged previews. Right-click, Shift+F10 and a visible
action button expose Open, Copy image and Download. Pending uploads preview
locally; ordinary files retain their existing cards. Menus use design tokens and
visual viewport bounds.

Downloads remain administrator-authenticated through HA. The private Bridge
checks thread ownership and reads confined, quota-bound snapshots. Older Apps
without the new capability fail closed. Raster byte/dimension/decoder checks,
concurrency and decoded-pixel cache bounds limit browser resources. Switching
chats cancels pending work and disposes object URLs. No external image URL is
loaded from chat text. Copy image needs a secure context and browser permission;
unavailable or rejected access reports a download alternative truthfully.

Focused verification completed:

- `npx vitest run frontend/test/conversation-timeline.test.js frontend/test/conversation-navigation.test.js frontend/test/codex-parity-layout.test.js --environment jsdom`
- `npx eslint frontend/src/codex-bridge-panel.js frontend/src/conversation-timeline.js frontend/test/conversation-timeline.test.js frontend/test/conversation-navigation.test.js frontend/e2e/panel.spec.js`
- `npx playwright test frontend/e2e/panel.spec.js --grep "conversation timeline previews"`
- `npm run build`

Current browser checks exercise short, empty, 40-turn and 500-turn histories,
scroll synchronisation, bookmark reload, stable hover/focus, streaming projection
reuse, real touch tap/jump, light/dark themes, reduced motion and axe checks.
Image checks exercise real decoding, clipboard write/read, downloaded PNG bytes,
permission denial, corrupt input, pending uploads and menu stability on refresh.
Harness events are synthetic; native HA acceptance and physical phone tests must
be reported separately.

Earlier results in PR #153 belong to the previous navigation implementation.
Release notes and private receipts record fresh results for this replacement.
