# Chat context menu frontend checks — 26 September 2026

This is local frontend evidence for an unreleased change. Managed HA-DEV,
provider, release and production acceptance remain separate.

The shared menu owner handles right-click, sidebar ellipsis and the header.
The former separate header/sidebar popup state has been removed. Existing
archive/restore and delete-confirmation methods remain the canonical mutations.
New navigation, project-move, section and fork actions require
`chat_operations_v1`; established actions remain available on older Apps.

The initial focused local checks passed:

- Frontend lint.
- 66 focused unit checks across chat context actions, existing chat controls and
  navigation/onboarding regressions.
- Four real-browser harness scenarios: the established disposable edit/archive/
  delete flow, all context actions at 390 and 1440 pixels, and a 1024-pixel
  coarse-pointer tablet with actual taps.
- The panel source bundle regenerated successfully and the diff has no
  whitespace errors.

The action scenarios cover rename, pin/unpin, unread/read, project moves, section
assignment/create/rename/remove/manage, share, each copy choice, native-fork
command routing, new-window routing, archive/restore and deletion cancellation
and confirmation. They also exercise desktop shortcuts, composer exclusion,
hover submenus, phone Back controls and viewport bounds. Screenshots capture
light/dark root menus, each main submenu, section management, rename, deletion
and the touch tablet. Visual inspection corrected outline icon styling, hidden
desktop Back controls and background tooltips.

Focused units cover the legacy capability gate, stale/uncertain writes, duplicate
submission guards, abandoned action replies, capability loss during a section
save, empty-section management, unchanged active-chat selection, explicit unread
clearing, keyboard traversal, outside dismissal, hostile names and clipboard
privacy. Conversation copies omit raw tools and code-block action labels and
reject an oversized projection before rendering it.

The browser uses synthetic Home Assistant responses and an instrumented
clipboard. It proves control routing and layout, not real provider fork, file
copy, browser clipboard permissions or published native acceptance. Full release
checks run after integration with the backend and conversation navigation work.

The subsequent move review hardening has its own focused evidence: module lint
and 46 chat context unit checks passed. Choosing a destination now performs only
a fresh artifact read and opens a labelled modal. Uploaded/private generated
files copy automatically; ordinary shared workspace files require explicit
unchecked-by-default selection, bounded to 100 visible choices. The payload
sends only selected `workspace_artifact_ids`, including an empty array when none
are chosen. Tests cover safe filenames/paths, malformed lists, cancellation,
capability/destination/workspace/revision/busy changes before save, late reads,
polling preservation, unknown acknowledgement and duplicate-write suppression,
mobile focus trapping and cancellation while loading. The earlier browser
evidence predates this added review. Parent integration subsequently passed
all 604 frontend unit checks and all 102 browser scenarios, including the move
review at phone and desktop widths. Those checks assert unchecked defaults,
exact selected file IDs, modal bounds and confirmation before a move. Screenshots
were inspected and an inaccurate static selection notice was corrected; the
46 action units and both complete menu browser scenarios passed again after
that wording correction. The final panel and PDF worker were regenerated.

The integrated conversation checks also cover empty chats, scrollable large
histories, 24-pixel desktop targets, alignment with the composer/header, and a
compact disclosure when the actual desktop chat area has insufficient space.
Provider, filesystem and managed release acceptance remain separate.
