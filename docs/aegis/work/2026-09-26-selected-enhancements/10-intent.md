# Selected enhancements delivery

Scope: CB-001, CB-005, CB-008, CB-009 and CB-015 from the canonical 26 September 2026 audit. Parent chat owns publishing, independent review and release. No production installation is authorised by this handoff.

Baseline: freshly fetched origin/main, paired App/Integration/panel 1.9.1, Bridge 0.16.0, pinned Codex 0.157.1. Primary checkout unrelated changes preserved. Read AGENTS.md, CONTEXT.md, audited rows, personal home/release/credential/tool instructions and canonical architecture map. Managed worktree unavailable for the Code project; authorised D: isolated Git worktree used.

Ownership: Sol root owns panel wiring, combined tests, docs and handoff. Luna search_diff owns backend search/diff and corresponding Integration endpoints; Luna queue_plan owns runtime queue/plan and corresponding Integration endpoints; Luna markdown owns separate Markdown helper/tests. Shared Integration edits coordinated between agents. Root owns generated frontend assets and version changes.

Compatibility: browser -> authenticated HA Integration -> private Bridge only. New features gated; old Apps keep existing behaviour. Plan mode never grants execution permission. Search uses visible transcript only. Diffs come from confined Git state.

Checks required: frontend lint/unit/build/browser; Ruff/compile; Bridge tests; full Linux Integration suite, root cold-restore, App image, transport, release authority checks. Parent coordinates shared Linux/native resources. No external publication from delivery chat.

Status: candidate source implemented and rebased onto the merged 1.9.2 release; local candidate checks complete, including merge/root comparisons and nested references. Independent parent review, Linux/image/native acceptance and release remain pending.
