# Community HA-MCP quick connect

## Intent and task start

Offer an administrator a short Settings connection flow for an already installed, running community HA-MCP Supervisor App. Detect the actual App, obtain its existing connection path privately, ask for explicit consent and reuse the current local relay and tool selection controls. Do not install or start servers, enable MCP, mint HA grants, rotate shared credentials, contact native DEV, or publish.

The clean existing question-notification checkout was reusable after its changes merged into paired 1.11.0. The original branch and archived candidate remain recoverable. Work started on `Herb/community-mcp-quick-connect-20260927` from paired 1.11.0 and incorporated merged paired 1.11.1 without conflict. Parent reserved paired 1.12.0 with Bridge 0.19.0 and released the capped desktop Docker slot. Native operations and publishing remain parent-owned.

## Baseline and design

Read repository AGENTS.md, CONTEXT.md, docs/home-assistant-mcp.md, native shortcut HTTP/runtime/UI, Bridge MCP manager/relay/routes, personal home/tool/credential guidance and canonical map. Community v8.5.0 upstream startup persists its secret path into Supervisor options and listens on port 9583. Its internal Supervisor token is not client authentication. The companion `ha_mcp_tools` domain alone is insufficient server evidence. Parent supplied a narrow started-App inventory; installed endpoint reachability and effective permissions remain unqualified.

Discovery selects exactly one recognised community App, verifies its metadata and started state, reads the secret path only server-side and derives a private HA host endpoint when host-networked. Do not trust the reported Docker bridge IP in that case; never use localhost from the Bridge container. Missing/ambiguous/unsupported metadata or auth fails with a fixed actionable state. No logs, filesystem credential reads or LAN scans.

Integration returns a fixed public status and server name only. Administrator HTTP connect accepts acknowledgement, not a user-supplied destination or credential. The private Bridge API capability-gates community quick connect. Its manager compares canonical endpoints under its existing lock and runtime mutation lease, reuses one matching approved local connection, refuses ambiguous matches or a changed deterministic connection, and otherwise creates through the existing local endpoint validator/pinning path with an empty tool allow-list. Existing connections retain pause state and permissions. The secret URL is stored in the existing private registry; this is credential reuse with an additional private copy, not zero-copy authentication.

Changed paths/destinations never silently retarget an approved connection. Settings directs the administrator to remove the old connection and explicitly reconnect; new connections again start with no allowed tools. Removal revokes only the Bridge binding, leaving the community App and its other clients running. Assist/chat enablement remains with existing controls.

## Source seams and ownership

- `community_mcp_discovery.py`: bounded Supervisor metadata/discovery and private endpoint; Luna implementation slice.
- `mcp_manager.py`, `routes/mcp.py`, App capability declaration: atomic reuse/create/status using existing relay; coordinator.
- Integration `bridge_api.py`, HTTP view: authenticated, bounded private calls and fixed public projection; coordinator.
- `mcp-setup.js`, panel and desktop settings: distinct community card, explicit consent, existing tool/row controls; coordinator.
- Focused discovery, manager, HTTP and UI tests; independent spec/security and code-quality review before full required gates.

## Verification and stop

Prove refusal of absent/stopped/ambiguous/spoofed Apps and unsafe hosts/paths; administrator/acknowledgement boundaries; no secret response, logs or exceptions; endpoint/name conflicts and rotation; unchanged reused pause/tools; new deny-all; idle mutation guard; capability negotiation and accessible UI. Then run repository-required local gates, requesting the parent's available Linux/Docker slot for applicable platform gates. Native acceptance and publishing remain parent-owned. Record any untested limits explicitly.
