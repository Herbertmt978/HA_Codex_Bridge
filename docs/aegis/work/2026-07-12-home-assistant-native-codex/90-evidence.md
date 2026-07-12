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

## Evidence status

This is draft evidence for continuation. It does not prove the HA App, sandbox, proxy, release, or cutover.
