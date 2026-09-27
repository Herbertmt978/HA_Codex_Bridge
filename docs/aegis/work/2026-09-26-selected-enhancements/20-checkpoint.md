# Selected enhancements checkpoint

26 September 2026. Checkout: `D:/CodexWork/ha-bridge-selected-enhancements-20260926`; branch: `Herb/selected-enhancements-20260926`.

All five authorised items have implementations and targeted regressions: safe assistant Markdown; bounded retained transcript search and compacted-message retrieval; durable Queue/Steer editing and removal; capability-gated native Plan and deliberate implementation; scoped lazy Git review. The source has incorporated the merged 1.9.2 release. The candidate pairs App, Host companion, Integration and panel 1.10.0 with Bridge 0.17.0 and unchanged Codex 0.157.1.

Fresh candidate evidence: frontend lint, all 763 unit tests across 50 files, build and all 124 browser tests pass. Ruff, compilation, generated JavaScript syntax, release projections, runtime lock and the native contract generator check against the signed pinned Codex binary pass. Release, packaging and native schema tests pass 37 tests with two platform skips; all ten transcript search tests pass. Full Linux qualification remains required; no full-suite pass is claimed.

Windows Bridge qualification reached 651 passing tests and 129 skips before the Linux-only browser worker dependency blocked collection. The earlier run lacked timezone data and ended without a valid summary; tzdata is now installed in the isolated environment. Focused queue and Plan checks pass seven tests, covering durable identity, restart deduplication, denied command/file approval, blocking clarification, deliberate execution restoring saved permissions, browser and Host restrictions, steering and capability gating.

Independent parent probes confirm the Plan permission boundary and the Git reference-order, state-drift, large-patch and repository-helper defences. The merge-commit list and patch now consistently compare the selected commit with its first parent; root commits compare with the empty tree. Flat and nested reference ordering regressions pass. The final Git module passes 13 tests with one POSIX-only skip on Windows. The private index now preserves its source timestamp, correcting same-size edit detection; timestamp drift is rejected. Git metadata enumeration and copying are bounded and ordered; no-follow alternates checks refuse unsupported shared object stores. The App now includes and verifies the Git executable required by this feature.

Parent owns final independent review, full Linux Bridge and Integration suites, root restore check, App image and native DEV acceptance, publication and rollout. The prior release chat has released CT105 and HAOS-DEV. Parent started initially stopped CT105 and owns restoring it to stopped; HAOS-DEV was initially running and remains running. This delivery chat has not used either host or changed resource power state.

Parent Linux Integration qualification reported 478 passes and five omitted-mode forwarding failures. Runtime and fallback calls now omit absent new mode fields; the corrected candidate passed all 483 Linux Integration tests. The repeated binary-path failure is corrected at the index snapshot owner, with deterministic coverage and three passing original-case repetitions.

No publication or external deployment performed. Temporary logs and qualification evidence are in `D:/CodexTemp/bridge-enhancements-20260926`. Local delivery is complete and committed for parent qualification; no release-ready claim is made before those gates.

The clean Linux lifecycle failure was traced to the new queue preservation shutdown branch: an activated worker could skip native start on a closed broker and wait on its preserved queued completion event. Close now removes/signals that in-memory event while retaining durable queue state. A deterministic regression verifies exit and later dispatch once; all eight relevant Windows tests pass with no runtime workers after any teardown. Full Ruff and compilation pass. Legacy lifecycle waits/assertions and gate ownership are unchanged; parent qualifies the final corrected Bridge and image inputs.

## Native qualification corrections

PR #236 and paired 1.10.0 are released after full local and GitHub checks, signed
image, provenance and SBOM verification. Exact paired DEV installation passed.
Native Markdown, transcript search and scoped Git patches passed, but two defects
prevented complete acceptance: Plan capability was checked before constructing
the runner, and Git freshness errors lost their safe message at the Integration
protocol boundary.

Branch `Herb/fix-plan-capability` prepares paired 1.10.1 with Bridge 0.17.1.
Capability negotiation now follows runner construction and retains all native
support gates. The Integration recognises the three existing bounded Git error
codes. Regressions exercise app construction, readiness, Plan submission and
unsupported native contracts, plus actual HTTP error parsing through the HA
websocket handler. The focused 117-test run, Ruff and diff checks pass; full
patch release qualification and resumed native Plan/queue acceptance remain open.

Parent owns publication and DEV qualification. Production stays at 1.9.2 with
automatic App updates paused until separate rollout approval. Existing owned DEV
fixtures and the stale Git token are retained for patch verification. VM 103
remains running as found; CT105 has been restored stopped. Disposable test output
and live receipts remain under `D:/CodexTemp/bridge-enhancements-20260926`.

## Combined Assist and native corrections — 27 September 2026

The combined local candidate pairs App, Integration and panel 1.11.0 with
Bridge 0.18.0 and unchanged Codex 0.157.1. Assist retains one configurable
conversation entity, independently selected native or compatible custom MCP
servers, a private read-only profile and deny-all initial native tool grants.
Question notifications remain opt-in and cannot grant execution approval.

Native evidence showed that a loaded provider thread ignores resume settings.
The broker now confirms stable unsubscribe and explicit unload before resuming
the same history with accepted settings. Strict identity, model, permission,
generation and deadline checks remain. Fifteen focused provider-peer tests pass;
independent read-only review found no actionable defect in this correction.

The panel accepts an omitted default pending status only on the authoritative
pending-list route. Native completed Plan items project into safe Markdown and
rail excerpts; matching unfinished Plans stream without becoming acknowledged
answers. Queue edit/remove references resolve a unique run-owned draft across
global event and local message sequence namespaces. Rebuilds preserve reading
position and cancelling a draft preserves a different live turn's ownership.
The current Windows checks pass 815 units and all 131 browser cases. Desktop
and phone screenshots verify completed Plan and edited queue presentation.

The App restart regression proves the original shutdown cancels its queued
record before recovery. Closing auth before runner teardown can wake a queued
worker into the admission-false path. A shared locked worker predicate now
preserves only an explicit, undispatched queued prompt when the broker is closed.
Deterministic barrier tests preserve that prompt after closed admission and
closed start errors while genuine open-broker account changes still cancel.
Private account ownership and the original recovery budget remain required.
Fresh Linux focused qualification passes 90 tests and full Ruff, including the
real App lifespan's post-shutdown queue assertion and one dispatch/completion
after restart. The failing diagnostics remain in the combined task evidence.

Independent final-candidate review also identified an already-admitted queued
lease retained by cancellation during close. The deterministic pre-start
barrier reproduced the reserved slot after shutdown. Close now releases that
slot only for a proven undispatched explicit queue; dispatch-marked ownership
remains retained. Both focused barrier cases pass. The first frozen full Bridge
run passes 2,436 tests with 27 existing skips; final qualification will cover
this scoped correction separately against the corrected committed source.

Final qualification requires a clean local commit, a canonical Git archive
verified against every committed file, fresh applicable Linux gates and parent
native acceptance. No combined publication or deployment has occurred.
Production remains excluded; original fixtures and failed histories are retained.

## PR #238 lifecycle review corrections — 27 September 2026

A valid native HA MCP grant now survives a temporary unload or setup of one
enabled server entry. Status reports unavailable and refresh does not rotate
credentials until it is loaded again. Token and owner validation still precede
that temporary status. Missing, disabled or failed entries, missing capabilities
and invalid authority retain definitive cleanup. Restoration during running HA
does not leave a listener waiting for a startup event that has already fired.

Permanent Bridge entry removal now uses the question coordinator's own saved
ledger contract after unload, on both Supervisor and external connections.
It clears managed persistent and verified Companion notices without starting
the runtime. Corrupt storage, failed service delivery, cancellation and failed
ledger writes retain cleanup evidence. A changed recipient identity is never
targeted. Normal reload still preserves claims and does not deliver twice.

The focused Linux run passes 100 cases. The public Home Assistant entry manager
drives setup, reload, disable and removal in the regressions; only optional MCP
integration callbacks and external network/UI boundaries are mocked. No test
dependency versions change. Independent scoped source review found no blocker.
The corrected candidate requires fresh full Integration, static and validator
checks. Unaffected Bridge, frontend and App gates may be carried only after
exact canonical input equivalence is recorded. Parent owns publication and
managed HA-DEV acceptance; no native or phone actions occurred in this follow-up.
