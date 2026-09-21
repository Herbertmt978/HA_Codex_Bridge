# Local MCP connections

Deliver roadmap item MCP-01 ([issue 97](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/97)) as a paired App and Integration release.

The feature adds local HTTP/HTTPS connections through a private relay. It needs a separate App option, a warning and acknowledgement for each endpoint, connection-time destination enforcement, restart recovery and compatibility with older Apps. Guided HA-MCP setup and the existing custom-server flow both remain available.

Success means relevant unit and browser checks pass, a native HAOS-DEV instance discovers and calls a local tool, restart and disabled-state checks pass, and the paired release is signed and published. Production local access remains off unless an administrator explicitly enables it.

Bearer credentials, custom headers, stdio servers and interactive MCP questions belong to later roadmap items. This release does not broaden the workspace, browser or Host Access boundary.

The baseline is AGENTS.md, CONTEXT.md, SECURITY.md, the MCP setup guide, the pinned native protocol and runtime, and the MCP-01 plan. The App owns network enforcement; the Integration owns HA administrator access; the panel collects explicit endpoint consent. ADR 0007 records the relay design.
