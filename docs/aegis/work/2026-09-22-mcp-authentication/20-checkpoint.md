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

## Release preparation

The owner subsequently authorised publication on 22 September 2026. The release
candidate pairs App, Integration and panel 1.3.0 with Bridge 0.10.0 and Codex
0.155.1. Release-specific local checks are recorded below in the evidence file.
The [release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/tag/1.3.0)
and GitHub workflows are the source of publication status. Live HA installation
is outside this release request.
