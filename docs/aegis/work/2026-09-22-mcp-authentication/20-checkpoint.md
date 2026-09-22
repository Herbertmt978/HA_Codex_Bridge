# MCP authentication checkpoint

Branch: `Herb/mcp-authentication`. Tracking issue: #98.

Implemented the private relay credential store, bounded administrator HTTP
routes, capability negotiation, masked create/replace/remove controls and
documentation. Existing registry version 1 is read in place; mutations write
version 2. Static credentials never enter native Codex configuration.

Frontend, Linux Integration and Bridge checks pass. Native HAOS-DEV fixtures
pass bearer/header authentication, rejection, rotation, restart and removal.
The final candidate image and complete App credential API checks also pass.
Task containers, volumes and transfer archives are removed; DEV103 and CT105
are restored stopped. Detailed results are in [the evidence record](90-evidence.md).

No release version has been assigned. App/Integration/panel remain 1.2.0,
Bridge remains 0.9.0 and Codex remains 0.155.1 in this development branch.
Nothing has been pushed or installed on production. The next release needs
version synchronisation, publication checks and the normal release workflow.
