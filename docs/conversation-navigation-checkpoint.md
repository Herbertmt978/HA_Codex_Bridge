# Conversation timeline checkpoint

The panel now projects retained chat message events into a compact turn timeline. A turn groups its user prompt and assistant replies by Bridge `run_id`, with chronological fallback for older events. Desktop markers form a centred, sticky vertical stack; hover and keyboard focus expand a horizontal tick and show a preview outside the scrollable marker track. Large histories scroll inside the bounded track. Touch and narrow layouts start with a 44 px “Jump to message” disclosure; opening it shows a horizontal turn list and preview, while choosing a turn jumps and closes the list. Escape also closes it and restores focus to the disclosure.

The timeline uses the existing conversation event history and its 25,000-event retention limit, with a 12,500-turn projection cap. It groups RuntimeBroker `run.queued` with the matching turn and clears that state on `run.started`; legacy `run.dequeued` is also supported. Repeated steer prompts, responses and accessible labels are bounded to 120 characters. It adds no persistence or bookmark state. Preview text is inserted as text, and provider/account data and internal run identifiers are not rendered.

Focused verification completed:

- `npx vitest run frontend/test/conversation-timeline.test.js frontend/test/conversation-navigation.test.js frontend/test/codex-parity-layout.test.js --environment jsdom`
- `npx eslint frontend/src/codex-bridge-panel.js frontend/src/conversation-timeline.js frontend/test/conversation-timeline.test.js frontend/test/conversation-navigation.test.js frontend/e2e/panel.spec.js`
- `npx playwright test frontend/e2e/panel.spec.js --grep "conversation timeline previews"`
- `npm run build`

The Playwright check exercised desktop hover/focus/jump, the initially closed mobile disclosure, mobile preview/jump/close and Escape focus restoration, dark theme, reduced motion and axe checks. Screenshots are retained with the private local verification receipt.

Integrated verification passed all 604 frontend units and 102 browser scenarios.
Follow-up regressions cover terminal failed/cancelled/interrupted turns, cleared
queues, malformed historical records, empty chats and long scrollable tracks.
The rail uses the existing reading-area gutter without reducing message width;
when the actual conversation area lacks room, it uses the compact disclosure.
Header, message and composer alignment, target spacing and the narrow desktop
fallback are asserted in real Chromium layout checks. These local browser
checks use synthetic events and do not establish native provider acceptance.

The final PR #153 review build passed all 610 frontend units and 102 Chromium
scenarios. The review corrections preserve unknown native outcomes across
the Bridge, Home Assistant and panel and distinguish definitive rejection.
