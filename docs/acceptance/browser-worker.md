# App-owned browser worker acceptance

## Availability decision

The `amd64` App supports an explicitly enabled browser worker, conditional on
root-owned startup attestation. `enable_browser` defaults to false. A missing,
invalid or stale proof leaves `browser_v1` unavailable; no parent-network
fallback exists. The external Bridge does not enable this worker.

The earlier prototype could not establish namespaces on its test host. Native
HAOS testing established a separate AppArmor child and a namespace launcher
without adding Docker capabilities. Packaging alone remains insufficient.

## Boundary and threat model

Public pages, page scripts, model-supplied selectors and URLs are untrusted.
The model receives only the typed `ha_browser` operations. The trusted Bridge
correlates calls with the current runtime generation, thread, turn and session.
No public listener, CDP endpoint, raw evaluation, arbitrary headers, credential
injection or file path is exposed by that contract.

The launcher uses a dedicated Bubblewrap executable, a separate AppArmor child,
fresh user, PID, mount, network, IPC and UTS namespaces, no usable capabilities,
no-new-privileges and a locked seccomp filter. System files are read-only; the
profile is in a 128 MiB temporary filesystem. App data, Codex home, workspaces
and Supervisor state are not mounted. LSM rules deny private paths and sensitive
proc aliases. A real private file descriptor held by the parent is a startup
canary: the browser must be unable to open it through procfs.

HAOS masks parts of procfs and refuses a new procfs mount. The launcher keeps
the existing masked mount. Only caller-owned user-namespace mapping files are
writable, so Chromium can create its own nested sandbox. The outer seccomp
filter denies mount changes, namespace entry, cross-process reads, ptrace and
other escape primitives. Chromium retains its renderer namespace and seccomp
sandbox; no `--no-sandbox` or initial-namespace `SYS_ADMIN`/`SYS_PTRACE` is used.
Chromium's nested user namespace resets its bounding capability set; its
effective, permitted, inheritable and ambient capability sets remain empty.

Only loopback exists in the browser network namespace. Its TCP relay reaches
a private Unix socket owned by the trusted policy proxy outside that namespace.
The proxy validates every DNS answer and connects to the checked numeric peer.
Mixed public/private answer sets are rejected. Redirects and subresources
receive the same connection policy. UDP and raw IP sockets are denied by the
kernel; proxy bypass cannot create an external route. No ambient credentials
or resolver configuration are inherited by Chromium.

The root initializer starts the real unprivileged worker and independently
checks descendant kernel records: AppArmor label, UID mapping, capabilities,
PID hierarchy and seccomp filters. The fixed immutable helper supplies bounded
in-namespace negative checks. Renderer IDs are correlated with kernel PID
records, rather than inferred from Chromium's zygote command line. A fixed
offline page must produce valid PNG/PDF output, and all observed processes must
exit before the root writes the boot-local proof. Public egress is checked
separately on the target, so a website outage does not determine App startup.

## Bounds and failure handling

The worker permits one session, 100 actions and five minutes of session time.
Control frames, page text and captures have fixed byte bounds. Kernel rlimits
bound individual process data, output file size, descriptors and CPU time. A
watchdog samples aggregate RSS plus swap and process count, terminating the
browser tree above 1,500 MiB or 64 processes. This is a sampled bound, not a
hard aggregate memory cgroup, and can briefly overshoot between samples.
The relay limits concurrent connections, idle time and transferred bytes.

Cancellation stops a pending operation, closes the proxy and removes the
profile. Parent death and PID-namespace teardown remove browser descendants.
Failed initialization leaves no session. Captures are validated and stored
through the existing private chat artifact pipeline; they do not expose a
remote URL or browser profile. Published artifacts follow chat retention rules.

## Reproducible checks

- Run `bridge_service/tests/test_browser*.py` on Linux for schema, ownership,
  stale callback, artifact, DNS rebinding, redirect, subresource, proxy bypass,
  malformed IPC and failure coverage.
- Build the actual App context. The Dockerfile checks the exact Chromium APK
  checksum; the verified asset lock supplies Bubblewrap, Codex and its code-mode
  host. The host is required for dynamic tool calls through the app server.
- On HAOS-DEV, run root browser attestation with the App's production AppArmor
  policy. Confirm the renderer sandbox and every negative check, not just a
  successful page load.
- Run `scripts/check_browser_worker.py` inside the candidate as `codexbridge`
  after root attestation. It exercises real navigation, a fixed local form,
  typed actions, screenshots/PDFs, cancellation during a pending action,
  resource-limit termination and process/profile cleanup. It also verifies
  private subresource denial and a synthetic HTTP redirect to a private target.
  The form makes no external submission.
- Verify a fresh Codex chat receives the tools, creates private artifacts and
  serves them only through authenticated Home Assistant routes. An existing
  Codex session must keep its previous tool set.
- Retain the signed image, signature, SBOM/provenance and target release
  evidence. Test disabled and failed-proof startup as well as enabled startup.

Local native action and isolation checks passed during development on
20 September 2026. A fresh authenticated HA chat using the packaged candidate
opened Example Domain and created a PNG screenshot and PDF. Both artifacts
were available through authenticated HA requests and rejected unauthenticated
requests. The disposable chat was removed without changing the existing chat.
Signed-image publication and Supervisor installation acceptance must still be
recorded separately before declaring issue #43 complete.
