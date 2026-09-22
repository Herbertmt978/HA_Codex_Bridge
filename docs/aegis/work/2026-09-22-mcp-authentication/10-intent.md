# MCP token and API-key authentication

The owner asked to code the next enhancement after MCP-01. The roadmap identifies
MCP-02, issue #98, as next: bearer tokens and named API-key headers. This task
implements and qualifies the enhancement locally. It does not publish a release
or change production Home Assistant.

Read before implementation: AGENTS.md, CONTEXT.md, SECURITY.md, the MCP-02 plan,
the existing relay/manager, HA HTTP/WebSocket boundaries, panel forms and tests.
ADR 0008 and the owning security contract were revised before code.

The Bridge owns private credentials and endpoint binding. HA owns administrator
authentication. The browser owns an unsaved, masked input; native Codex receives
a relay capability. Older Apps keep existing forms through capability negotiation.
Public OAuth and existing local consent remain.

Success requires synthetic-credential tests for valid/invalid input, private
storage, rotation/removal across restart, destination confinement, administrator
and older-App rejection, and accessible desktop/mobile controls. Native Codex
must discover and call a tool through both modes on HAOS-DEV. Production
credentials, stdio, interactive elicitation and a new OpenAI login are out of scope.
