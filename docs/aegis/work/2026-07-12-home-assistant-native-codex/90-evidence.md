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

## Task 3B — Home Assistant-owned filesystem integration

| Evidence | Result |
|----------|--------|
| Runtime/profile integration | HA and external profiles retain distinct storage contracts; public HA paths remain relative |
| Project/thread integration | HA-owned project and thread directories are descriptor-anchored and portable-name validated |
| Runner integration | Codex receives the selected HA workspace without broad upload-directory exposure |
| Attachment security | Selected files are copied into sealed Linux memory descriptors, reopened read-only, and passed individually through `/proc/self/fd`; private paths and sibling files are not exposed |
| Artifact security | Download metadata is source-qualified and relative; responses stream an already-open immutable snapshot with safe headers and generic failures |
| Archive security | Sources are strict-walked and snapshot one at a time; private ZIPs publish only after successful close, fsync, identity validation, metadata save, and event append |
| Concurrency and cleanup | Upload, stale-writer, artifact-dedup, archive-builder, cancellation, and thread-deletion races are covered; partial private files are removed on failure |
| Windows full suite | 403 passed, 107 skipped |
| Linux full suite | 499 passed, 1 skipped |
| Artifact/archive focused suite | 26 passed |
| Spec reviews | Approved for attachments, artifacts, and archives |
| Code-quality reviews | Ready: Yes for each accepted slice |
| Build/diff hygiene | `compileall` and `git diff --check` passed; worktree clean |
| Commits | `51e1fc9`, `12d648a`, `1e5b1f3`, `b175578`, `e07d212`, `e3a7e0a`, `e3b7c24` |

The HA profile now owns every private path involved in a run. Attachment, artifact, and archive payloads cross trust boundaries through verified descriptors and immutable snapshots, while serialized records expose only validated relative locators. Append-preserving saves and canonical locks prevent stale writers from erasing concurrent state. The external profile remains compatible with its existing path-based behavior.

The remaining Task 3 runtime fact is target-system evidence: a real HA App acceptance run must confirm inherited file-descriptor behavior under the final container and sandbox configuration. That is an explicit release gate, not evidence claimed by the host test suites.

## Evidence status

This is draft evidence for continuation. It does not yet prove resource ceilings, the HA App image, target sandbox, proxy deployment, release, or cutover.
