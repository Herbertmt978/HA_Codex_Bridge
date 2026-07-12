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

## Task 2 — isolated Codex subprocess environments

| Evidence | Result |
|----------|--------|
| Initial RED | 29 failed and 18 passed; inherited parent values crossed the subprocess boundary |
| Hardening RED | 21 credential/PATH failures, then 13 provider/locale failures, then 7 Bridge/HA alias failures |
| POSIX compatibility RED | `relative:/usr/bin` incorrectly discarded the valid absolute entry |
| Independent focused GREEN | 95 passed |
| Independent full Bridge GREEN | 330 passed in 17.09s |
| Real Windows environment probe | 43 absolute PATH entries, zero empty entries, nine allowlisted keys, dedicated HOME/CODEX_HOME present |
| Spec review | Approved after credential-carrier and Bridge/HA alias fixes |
| Code-quality review | Ready: Yes; final confirmation found no findings after the POSIX compatibility fix |
| Diff hygiene | `git diff --check` passed; worktree clean |
| Commits | `61ad49a`, `649af01`, `6982cd7`, `c37042a` |

Codex subprocesses no longer copy the parent environment. The builder retains only validated executable paths, dedicated home/Codex home, safe temporary paths, structured locales, platform essentials, and existing certificate paths. Supervisor, HA, Bridge, OpenAI, GitHub, CI, cookie, authorization, proxy, and unrelated values are excluded. Realistic carrier forms are rejected even when embedded in an otherwise allowlisted value. Legacy fake-runner controls are injected only inside their tests.

## Task 3A — descriptor-anchored workspace boundary

| Evidence | Result |
|----------|--------|
| Initial RED | New test module failed collection because `codex_bridge_service.workspace` did not exist |
| Initial platform GREEN | Windows 40 passed/5 unavailable; Linux 45/45 |
| Review challenge | Initial implementation rejected for Windows TOCTOU fallback, path-based list/walk races, FIFO blocking, traceback causes, and portable-name gaps |
| Hardened Windows GREEN | 54 passed, 15 protected-I/O capability skips |
| Hardened Linux GREEN | 69 passed, zero skips, including descriptor/root-ancestor swaps, symlinks, FIFO, and special files |
| Full Bridge GREEN | 384 passed, 15 Windows capability skips in 18.75s |
| Spec review | Approved with no remaining findings after Unicode/device-alias and uniform link-error fixes |
| Code-quality review | Ready: Yes after reproducing and fixing the root-ancestor escape; final Minor regression suggestion also fixed |
| Build/diff hygiene | `compileall` and `git diff --check` passed; worktree clean |
| Commits | `ccfbb20`, `ee38aed`, `13baaeb`, `f2072b0`, `6f3ffb6` |

The accepted boundary holds a trusted root descriptor and duplicates it for every protected operation. POSIX `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`, exclusive creation, nonblocking special-file checks, descriptor-based listing/walking, and inode verification prevent lexical, symlink, ancestor-swap, and final-entry races. Unsupported platforms retain validation-only behavior and reject protected I/O; the Windows external legacy profile remains separate. Public names and errors are relative and redacted, including formatted exception chains.

## Evidence status

This is draft evidence for continuation. It does not prove the HA App, sandbox, proxy, release, or cutover.
