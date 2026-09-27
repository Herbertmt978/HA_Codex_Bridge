# Attention inbox

The sidebar Attention inbox shows current requests, failed runs and completed
turns across projects. Filter by status or project and select a row to open its
chat deliberately, including archived and HA Assistant chats. Opening a chat
changes its read state; it does not answer a question or resolve operational
attention. Archived chats retain their normal restrictions.

Pending requests come from the existing runtime interaction owner. They leave
the inbox only when that owner resolves or expires them. Failed and ready to
review rows describe each chat's latest run outcome. A later run replaces that
outcome; there is no separate acknowledgement or dismissal state.

Refresh attention checks current state immediately. Ordinary panel refreshes
check at most once every 30 seconds. Filters apply to the bounded returned view,
not an exhaustive server search. A notice identifies truncation; project choices
include projects represented in that view. Filters stay in this panel visit and
are reset when the HA user or connection changes. Cached rows and outstanding
replies are also invalidated on those changes.

The capability-gated, administrator-authenticated request follows the existing
browser → Home Assistant → private Bridge boundary. Rows contain human labels,
navigation identities and lifecycle timestamps, without question bodies,
commands, tool output or authentication URLs.

The durable latest-run projection survives event compaction and restarts and is
removed when its chat is deleted. On upgrade, it can seed missing rows only from
the retained event journal; run events compacted before this projection existed
cannot be reconstructed. New lifecycle events establish authoritative rows.
Unresolved interactions retain the runtime owner's existing restart and expiry
semantics; the inbox introduces no additional interaction persistence.
