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
