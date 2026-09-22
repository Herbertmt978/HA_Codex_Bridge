# MCP connection management

The owner authorised continuing through enhancements that can be implemented,
tested and merged with the available software and hardware until usage is
exhausted. MCP-05, issue #101, is the next roadmap priority after MCP-01/02.
MCP-06 tool permissions follows when this change is complete. Publication and
merge are authorised; production installation is not part of this work.

Baseline is released 1.3.1. Read AGENTS.md, CONTEXT.md, the MCP-05 plan, native
MCP manager, relay/credential store, runtime configuration gate, routes,
Integration proxies, panel setup/list components and their tests.

Outcome: edit supported connection fields, pause/resume individual servers,
retain private configuration and show bounded status/counts/recovery. Paused
connections must not call tools through retained chats or after restart.
Active turns and concurrent edits must conflict safely. Failed saves cannot
claim success or discard usable settings. Credentials stay write-only and
cannot follow an endpoint change without an explicit choice.

Keep native MCP configuration as the connection authority and the existing
private relay as credential/approved-endpoint authority. Determine native
reload behaviour with a disposable MCP fixture before selecting the final
pause mechanism. Do not expose endpoints, provider errors or credentials to
diagnostics. New APIs are capability-gated for older App compatibility.

Implementation and verification use an isolated development checkout, preserving
unrelated work in the primary checkout. Private operational evidence remains
outside this public repository.

Use synthetic local MCP fixtures, local container/browser tests, a Linux worker
for full suites and disposable candidate containers on HAOS-DEV. Preserve the
resources' original power states. No native ARM64 device is available and
issue #111 remains open.
