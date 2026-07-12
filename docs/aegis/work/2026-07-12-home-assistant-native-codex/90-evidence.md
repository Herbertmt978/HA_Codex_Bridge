# Home Assistant-native Codex — Evidence Bundle Draft

## Baseline evidence

| Date | Scope | Command | Result |
|------|-------|---------|--------|
| 2026-07-12 | Existing Bridge suite in isolated worktree | `python -m pytest -q` from `bridge_service` | 115 passed in 25.03s |

## Review evidence

- Design spec independently challenged for HA Community fit, auth streaming, container/release, security, and UX.
- Implementation plan independently reviewed; release-lock ordering and JSON/SQLite crash consistency issues were fixed; reviewer returned Approved.

## Task 1A — API/build contract

| Evidence | Result |
|----------|--------|
| Initial RED | Missing `api_contract` module during collection |
| Credential-hardening RED | 43 failures, then 55 failures after realistic token/identifier cases |
| Focused GREEN | 134 passed |
| Full Bridge GREEN | 249 passed |
| Spec review | Approved |
| Code-quality review | Approved after two metadata grammar fixes |
| Commits | `0739475`, `45597a5`, `afadc02` |

Build metadata now accepts only bounded SemVer, supported architectures, exact Git/OCI image hashes, and exact SHA-256 lock digests. Realistic GitHub/OpenAI/JWT/Bearer values are rejected and absent from serialization.

## Task 1B — authenticated readiness and additive diagnostics

| Evidence | Result |
|----------|--------|
| Initial RED | 4 readiness failures; combined slice 6 failed and 5 passed |
| Implementer focused GREEN | 153 passed |
| Implementer full Bridge GREEN | 255 passed |
| Independent focused GREEN | 153 passed in 1.04s |
| Independent full Bridge GREEN | 255 passed in 17.98s |
| Diff hygiene | `git diff --check` passed |
| Spec review | Approved with no findings; reviewer-focused run 182 passed |
| Code-quality review | Ready: Yes; no Critical, Important, or Minor findings |
| Commit | `e690cdd` |

`/ready` is still bearer-token protected and now returns frozen typed API, component-version, image, capability, architecture, and readiness records. `create_app` captures validated build metadata once; `/status` retains its existing shape and gains safe version/build diagnostics. Readiness intentionally remains statically `ready` until Task 10 wires runtime and sandbox health.

## Evidence status

This is draft evidence for continuation. It does not prove the HA App, sandbox, proxy, release, or cutover.
