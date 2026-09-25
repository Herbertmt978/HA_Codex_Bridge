# ADR 0009: Isolated stdio MCP workers

**Status:** Accepted for the MCP-03 implementation, 25 September 2026. The
security review and native HAOS-DEV isolation, discovery and tool-call checks
support this bounded first release. The App option remains off by default and
new connections require package review and tool selection.

## Context

The released Bridge accepts public HTTPS MCP servers and separately approved
LAN/App HTTP endpoints. The latter use a private, authenticated relay. Native
Codex stdio configuration would instead launch a command in the Codex process
environment, where App-private state and credentials exist. Package hashes and
process cleanup alone cannot make that command safe. The
[MCP-03 plan](../../planned-releases/mcp-03-stdio.md) requires an independently
reviewed execution and transport boundary.

The App already demonstrates a separate, attested Bubblewrap/AppArmor browser
worker on amd64 HAOS without added container capabilities. Its profile and proof
are browser-specific and must not be reused as evidence for an MCP worker.
HAOS-DEV currently has 2 vCPUs and 2 GiB RAM; Node is not packaged in the App.

## Decision

### Transport and ownership

- Codex sees only a generated, authenticated loopback Streamable-HTTP binding.
  It never receives a stdio `command`, executable path, package path, worker
  environment or upstream credential. A trusted Bridge adapter owns the stdio
  process and translates bounded MCP JSON-RPC messages between HTTP sessions
  and newline-delimited stdin/stdout. The adapter strips its capability header,
  cookies, authentication and HTTP session headers; none reaches the worker.
- A separate stdio registry records the approved package revision, fixed
  entrypoint, permissions, tool selection and pause state. It must not reuse the
  credential-bearing network relay registry as a package store. The manager
  recognises only an exact generated loopback binding and refuses native
  `command`/`args`/`env` entries, including entries from other config layers.
- Each HTTP MCP session owns one worker and one negotiated protocol lifecycle.
  Session identifiers are unguessable, bound to that worker and invalidated on
  stop, crash, reload, restart or removal. New sessions never inherit another
  session's stdio state. HTTP disconnect alone is not a cancellation; explicit
  MCP cancellation, session DELETE and administrator stop follow the protocol
  and terminate the owned worker when its session ends. Unsupported
  server-to-client requests fail closed without waiting indefinitely.
- The adapter, not the worker, owns the loopback listener, request admission,
  connection limits and bounded diagnostics. No App, Bridge or worker listener
  becomes browser-facing. The Home Assistant Integration retains administrator
  authentication and version/capability negotiation.

### Worker isolation and grants

- MCP-03 needs its own AppArmor child profile, Bubblewrap executable, immutable
  launcher and boot-local root attestation. A worker starts with fresh user,
  PID, mount, network, IPC and UTS namespaces; no usable capabilities; no new
  privileges; and a locked seccomp filter. Attestation checks actual descendant
  labels, namespaces, capabilities, seccomp, process hierarchy, denied private
  file descriptors and denied network sockets. Failure leaves stdio unavailable
  and cannot fall back to the Bridge or Codex user.
- The child sees a minimal read-only Python runtime and verified package tree,
  private bounded scratch space, and only its declared file grants. It does not
  mount App data, Codex home, Supervisor state, the Home Assistant configuration,
  host paths or the workspace root. Its environment is constructed from a fixed
  allowlist. It inherits neither Supervisor variables nor Codex sign-in state,
  arbitrary host variables or unrelated file descriptors.
- The first release supports **Python 3.14 on amd64 only**, **no network
  access**, and **no workspace file grant**. Immutable package files and
  disposable scratch space are its only file grants. This avoids granting a
  workspace to MCP calls from another chat: today's global native MCP binding
  does not identify the chat at the adapter. Workspace-relative grants require
  a separately proven per-chat/session ownership boundary. Node, npm,
  user-entered commands, arbitrary host paths, provider secrets and network
  exceptions are outside the first release.
- New connections start paused with an empty tool allow-list. Administrators
  review the server's claimed tool descriptions and select permitted tools.
  Discovery uses an exclusive no-active-work lease and restores the paused,
  empty policy before work admission; an interrupted probe leaves a durable
  fail-closed marker. Tool annotations are untrusted claims. Existing MCP-05
  pause/configuration leases and MCP-06 native tool filtering must apply to
  both attended and scheduled work. An App upgrade alone activates no worker.

### Packages, updates and resource limits

- Ship a small catalogue of approved pure-Python packages in the signed,
  immutable App image. A root-owned manifest records each package's source,
  licence, exact version, Python compatibility, every file digest, fixed
  entrypoint/arguments, environment names and grants. The build pins and
  verifies every input. Runtime verification compares the manifest and package
  bytes before activation. No runtime `pip install`, package-manager network
  access, upload-as-executable or user-provided command is accepted.
- The approval screen shows the package source and version, fixed command,
  digest summary, actual file/environment/network permissions and selected
  tools before activation. A package change creates a new paused revision.
  Update waits for active and queued work, verifies the staged revision, then
  atomically switches the binding; a failure preserves or restores the last
  usable paused revision. The packaged previous revision remains available for
  rollback. Missing, altered or unreviewed packages cannot start.
- Bound per-message bytes, stdout framing, stderr capture, runtime, idle time,
  file size, descriptors, CPU time, process count and aggregate worker memory.
  Admit at most one first-release worker on the 2 GiB DEV class, with a budget
  coordinated with the browser worker. Refuse activation when the budget or
  isolation proof is unavailable. Stop and remove the full process tree on
  cancellation, crash, timeout, restart, pause and removal. Diagnostics expose
  only fixed states and bounded, sanitised error summaries, never worker
  environment, paths, private protocol headers or raw stderr.

## Alternatives and limitations

Direct native Codex `command` configuration and an arbitrary-command text box
are rejected: they would launch code in the wrong trust boundary. A separate
Supervisor App/container could provide a harder aggregate resource boundary,
but adds an App pairing, distribution and state-transfer model not yet
qualified. A single App-owned namespace worker is the first design, conditional
on native isolation and resource evidence.

No workspace grant means the first release can run useful stateless Python MCP
servers but cannot run filesystem servers. No network means it cannot run
servers that call external APIs. Adding either permission needs a new policy
review, native negative tests and an explicit administrator decision. A
sampled memory watchdog is not a hard aggregate cap: implementation must prove
an enforceable bound or document the residual risk for review before release.
The implementing change must settle and review the package trust root, catalogue
update cadence and exact supported MCP protocol versions. Bundling only a test
fixture without a real package catalogue and update/rollback controls would be
an intermediate implementation, not completion of issue #99.

## Acceptance before any release or issue closure

1. On HAOS-DEV, use the candidate App's actual AppArmor policy and pinned Codex
   runtime. A real packaged stdio server must initialise, discover tools and
   complete allowed tool calls through the Bridge adapter. Blocked tools,
   newly discovered tools and scheduled work must obey the same selection.
2. From inside the worker, prove that private Bridge, Supervisor and Codex
   credentials, `/config`, sibling workspaces, undeclared paths, parent file
   descriptors and proc aliases are inaccessible. DNS, TCP, UDP, loopback, LAN
   and public network attempts must fail under the no-network policy.
3. A wrong digest, tampered manifest, missing proof or exhausted resource
   budget must prevent activation with recoverable UI guidance. Verify bounded
   handling of malformed/oversized JSON-RPC, stdout and stderr.
4. Verify start, pause, resume, cancellation, crash, timeout, App restart,
   update, rollback and removal. Descendants, sessions, listeners, temporary
   files and package references must be cleaned up; uncertain recovery blocks
   new work rather than exposing a stale native binding.
5. Verify administrator-only Home Assistant routes, older-App capability
   handling, keyboard/mobile UI states, retained HTTPS/LAN MCP behaviour, Core
   configuration check, App/Integration restart and health, and absence of
   secret-bearing logs. Signed image, provenance, SBOM and native HAOS evidence
   remain separate publication gates.

## Baseline sync

This boundary is reflected in
[AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md) and
[SECURITY.md](../../../SECURITY.md). Arbitrary executable stdio commands remain
unsupported. The only first-release package is Bridge Time 1.0.0; no second
revision is bundled, so package update and rollback controls have no choice
until a reviewed later release supplies one.
