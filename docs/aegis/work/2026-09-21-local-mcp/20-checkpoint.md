# Local MCP connections checkpoint

The MCP-01 implementation and local qualification are complete. The candidate
pairs App, Integration and panel 1.2.0 with Bridge 0.9.0. Native discovery, tool
calls, restart recovery and disabled-state checks passed on HAOS-DEV.

Publication remains gated on the pull request checks and the signed release
workflow. See [qualification evidence](90-evidence.md) and [ADR 0007](../../adr/0007-local-mcp-relay.md).

DEV103 and CT105 were stopped before this task started them. Both must return
to stopped after qualification. Docker Desktop was already running and must
remain so. Disposable volumes, candidate images and transfer archives belong to
this task. Production remains on its existing installation with local MCP off.
