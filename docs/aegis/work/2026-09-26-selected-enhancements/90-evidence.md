# Delivery evidence

26 September 2026. Candidate: App, Host companion, Integration and panel 1.10.0; Bridge 0.17.0; unchanged Codex 0.157.1. Authorised scope: CB-001, CB-005, CB-008, CB-009 and CB-015. The complete source includes the merged 1.9.2 baseline.

## Completed local checks

| Check | Fresh result | Log under the task temporary directory |
| --- | --- | --- |
| Frontend lint | Pass | frontend-lint-candidate.log |
| Frontend units | 763 passed across 50 files | frontend-unit-candidate.log |
| Generated frontend build | Pass | frontend-build-candidate.log |
| Desktop and mobile browser checks | 124 passed | frontend-e2e-candidate.log |
| Release, packaging and native schema regression tests | 37 passed, 2 platform skips | candidate-packaging-corrected.log |
| Retained transcript search | 10 passed | candidate-search-tests.log |
| Git review, merge/root comparisons, nested references and index timing | 13 passed, 1 POSIX-only skip | candidate3-git-tests.log |
| Ruff across Bridge, Integration, scripts and tests | Pass after final correction | candidate3-ruff.log |
| Native schema regeneration against signed pinned runtime | Pass | native-schema-candidate.log |
| Release projections and Codex runtime lock | Pass | Machine-readable receipt |
| Python compilation and generated JavaScript syntax | Pass | Machine-readable receipt |

Temporary evidence directory: `D:/CodexTemp/bridge-enhancements-20260926`. Browser fixtures cover safe Markdown, retained search jumps, Queue/Steer controls, queue edits/removal, Plan review/implementation and Git review at desktop and phone widths. Focused runtime checks passed seven queue/Plan tests. Parent probes independently verified approval denial, clarification and restored execution policy, plus Git state drift, unsafe helpers, configuration mutation and large-patch labelling.

## Qualification limits

Windows does not qualify Linux-only execution. The broad Bridge run reached 651 passed and 129 skipped before a Linux-only browser worker import blocked collection. The earlier timezone-data failure was corrected in the isolated environment; that incomplete earlier run supplies no completion result. Queue owner recorded 300 passed and 24 skipped in a broad four-module run before correcting targeted sandbox expectations; remaining Windows/MCP lifecycle failures require the parent Linux run. No full Bridge or Integration pass is claimed here.

Parent owns the final independent review, full Linux suites, root restore gate, App image with an actual Git executable and repository review, native DEV acceptance, all publication and rollout. Source fixtures and synthetic probes do not prove native Codex acceptance on HAOS. The private Git snapshot intentionally refuses linked metadata/common directories/alternates; last-turn review is unavailable because no reliable per-turn baseline is recorded. Search retention is bounded and discloses incomplete coverage.

## Completion and follow-up

Keep the final source and generated panel committed together. Record exact verification identifiers only in the machine-readable handoff receipt. No publishing or host power changes were performed by this delivery chat. Parent owns returning initially stopped CT105 to stopped; HAOS-DEV remains in its initially running state.

## Qualification corrections

Parent Linux qualification exposed an Integration omission-compatibility failure: absent follow-up and collaboration modes were forwarded as `None`. Both runtime and fallback prompt paths now pass these fields only when explicitly supplied. Existing legacy assertions remain intact. Ruff and compilation pass; the Integration suite needs the parent Linux rerun because the Windows environment cannot collect its Home Assistant dependencies. The earlier Linux result was 478 passed and five failed before this correction, not a full pass.

A repeated Windows probe confirmed the intermittent binary-path failure was a product defect. The private snapshot copied index bytes with a newer filesystem timestamp, suppressing Git's racy-stat content check for same-size changes. Independent counterfactuals kept every input constant except the private index timestamp: preserving the source timestamp detected the edit; advancing it hid the edit. Both snapshot copy owners now preserve the captured source index timestamp. Matching metadata fingerprints include it, so timestamp drift during copying is rejected. Source metadata/configuration is untouched.

The final Git module passes 13 tests with one POSIX-only skip. New deterministic coverage verifies source timestamp preservation, drift rejection and consecutive list/per-file detection with matching cached stat fields. Three independent repetitions of the original binary/large/state-drift case pass, without delays or relaxed assertions. Parent must qualify corrected App inputs and rerun Linux checks on this committed source.
