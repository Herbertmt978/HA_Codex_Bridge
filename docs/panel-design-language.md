# Panel design language

Compare each new control with its adjacent established controls in the rendered
panel. The reference implementations are in `frontend/src/codex-bridge-panel.js`:

The default composer follows the Codex Windows proportions: a clear writing
area and one compact bottom toolbar. Secondary Follow-up, Collaboration,
elapsed-time limit and Web search controls/help open in Turn options. The plus
surface groups Files/workspace context and Previous chat context. Model/thinking
and allowance remain reachable together towards the right; microphone and
send/stop keep their existing meaning. Repository status/review is on demand.
Never add a permanent full-width option or help row for a new feature.

Goal and Find in chat open from the header overflow. Closing them removes their
layout height, not just their contents. Active context and non-default sending
choices retain concise summaries; approvals, pending questions and blocking
validation stay visible. Permission labels describe actual Bridge grants.
Message actions use one labelled ⋯ trigger per message, with copy/quote actions in its menu and no separate action row. Capture validated passage selection before menu focus moves; show unavailable-selection guidance visibly.
Keep exact copy/quote behaviour, including passage selection on touch/keyboard.
Opening or closing any surface must preserve drafts, choices and selected context
and must never submit, grant access or start work. Compare full header-to-composer
views on desktop and narrow screens, including actual regained conversation
height, every submenu, focus return, Escape and viewport placement.

| Use | Existing implementation |
| --- | --- |
| Composer setting | `_composerUtility`, Model and Thinking selectors |
| Compact selector | `composer-utility` label, `composer-select` wrapper, `compact-select` native select and trusted `icons.chevronDown` |
| Compact text action | `composer-limits-button`, such as Limits and Review changes |
| Workspace tabs | `bottom-panel-header`, File preview and Terminal buttons |
| Preview action toolbar | `pdf-preview-toolbar`, including disabled navigation |

Use the panel's `--font-ui`, body/control/caption font sizes, `--text-color`,
`--muted-color`, surface, border, accent and focus tokens. Keep the existing
neutral surfaces and compact spacing. Do not add a second theme palette or
browser-default typography. Global input/button rules and focus rules belong
inside the panel's Shadow DOM; page styles cannot repair missing panel styles.

Composer selectors and text actions are 32px high on desktop and have a 44px
minimum at narrow widths. Labels remain visible, rows wrap, and long values stay
within the panel. Follow the existing 4px compact-control radius and 8px input
radius. Native semantics, accessible names and trusted icon handling remain
intact. Decorative chevrons must not intercept pointer input.

Loading, empty and unavailable messages use readable panel text and consistent
spacing. Keep a reachable retry action. Disabled controls remain legible; focus
must use the existing visible keyboard ring. Do not remove outlines to match a
reference screenshot. Keep informational text separate from actionable controls.

For changes, inspect actual rendered source and then the generated served bundle:
light and dark themes, desktop and 390px narrow viewports, keyboard operation and
visible focus, touch targets, reduced motion, loading/empty/error/retry states.
Compare font, size, surface and geometry with the neighbouring canonical control;
check clipping and horizontal overflow. Capture focused before/after evidence.
Use behavioural/computed-style browser assertions for defects rather than source
string assertions or an unexplained screenshot baseline replacement. Regenerate
assets with the normal build; never edit the generated bundle by hand.

The bottom File preview/Terminal pane uses a thin top-edge drag separator. Keep
its idle height small, support touch and keyboard resizing, preserve visit height
across tabs/open-close, and clamp against both visual viewport edges. Verify real
terminal host dimensions and fitted rows, preview scroll/zoom, and cleanup after
pointer cancellation, close and disconnect. Do not add a permanent resize toolbar.

Specialised message copy actions, including ordered maths sources, belong inside
the same per-message overflow menu. Preserve exact source identity and do not
reintroduce persistent copy-action rows.
