# Planned releases

These are separate feature-release plans, based on the gaps reviewed on
21 September 2026. MCP-01 is implemented for App and Integration 1.2.0; check
the [release page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases) for
publication status. MCP-02 is implemented for App and Integration 1.3.0.
Check each entry's status and the release page before treating it as shipped;
unreleased entries have no promised delivery date.

Each plan has its own issue, scope, dependencies and acceptance criteria. Use the
linked issues for implementation progress and update this roadmap when a feature
ships. Complete the relevant local, CI and native Home Assistant checks before
marking it released.

An upgrade must not activate new MCP connections, messaging channels or host
permissions without the administrator's choice.

## MCP compatibility

MCP-01 introduces the reviewed, opt-in local relay exception. MCP-02 extends that
relay with write-only credentials under its own revised security contract.
It requires App and Integration 1.3.0; the 1.2.0 configuration flow is unchanged.
MCP-03 uses a separate accepted worker and transport design. Its first package
is bundled in the 1.8.2 source and remains opt-in.

| Plan | Outcome | Dependencies | Issue |
| --- | --- | --- | --- |
| [MCP-01: Local MCP connections](planned-releases/mcp-01-local-connections.md) | Implemented for 1.2.0: connect to HA-MCP and other servers on the LAN or HA App network. | Explicit network permission and destination controls | [#97](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/97) |
| [MCP-02: Token and API-key authentication](planned-releases/mcp-02-authentication.md) | Implemented for 1.3.0: connect servers that require a bearer token or authentication header. | Private credential storage; MCP-01 for local endpoints | [#98](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/98) |
| [MCP-03: Isolated stdio servers](planned-releases/mcp-03-stdio.md) | Included in 1.8.2 source: run the verified Bridge Time package with bounded access. | Accepted worker isolation and private adapter | [#99](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/99) |
| [MCP-04: Interactive MCP requests](planned-releases/mcp-04-interactive-requests.md) | Included in 1.6.4 source: answer supported MCP forms and authorisation requests in an active chat. | Turn-bound interaction lifecycle | [#100](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/100) |
| [MCP-05: Connection management](planned-releases/mcp-05-management.md) | Implemented for 1.4.0: edit, pause, resume and diagnose a server without deleting it. | Runtime reload and compatibility negotiation | [#101](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/101) |
| [MCP-06: Tool permissions](planned-releases/mcp-06-tool-permissions.md) | Released in 1.6.4; native allow/block, restart and changed-catalogue checks passed on DEV. | MCP-05; runtime tool filtering | [#102](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/102) |

## Home Assistant integration

| Plan | Outcome | Dependencies | Issue |
| --- | --- | --- | --- |
| [HA-01: Task actions and result events](planned-releases/ha-01-actions-events.md) | Released; native start, continue, cancel and inspect smoke checks passed. Full acceptance remains open. | Idempotent admission and permission policy | [#103](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/103) |
| [HA-02: Completion notifications](planned-releases/ha-02-notifications.md) | In development: choose when and where scheduled-task outcomes are announced. | Durable automation history and HA notification services | [#104](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/104) |
| [HA-03: Assist conversation agent](planned-releases/ha-03-assist.md) | In development: receive direct Codex answers from one explicitly selected project. | HA-01 and explicit exposure policy | [#105](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/105) |
| [HA-04: Status and usage entities](planned-releases/ha-04-status-entities.md) | Included in 1.6.4 source: use account limits, connection health and task state in HA dashboards. | Stable entity and privacy contracts | [#106](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/106) |
| [HA-05: Scoped configuration access](planned-releases/ha-05-config-workspace.md) | Review and edit selected HA configuration without a root-host grant. | Reviewed storage boundary and recovery workflow | [#107](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/107) |

## Platforms and task experience

| Plan | Outcome | Dependencies | Issue |
| --- | --- | --- | --- |
| [PLATFORM-01: ARM64 support](planned-releases/platform-01-arm64.md) | Build preparation included in 1.3.1; stable App remains amd64-only. | Native ARM64 HAOS hardware and qualification outstanding | [#111](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/111) |
| [TASK-01: Scheduling through chat](planned-releases/task-01-conversational-scheduling.md) | Included in 1.6.0: describe a task and its frequency, then confirm the interpreted schedule. | Existing scheduler and explicit schedule confirmation | [#112](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/112) |
| [SHARE-01: Public snapshots](planned-releases/share-01-public-snapshots.md) | Publish a selected, fixed, read-only copy of a chat. | Hosting and privacy design; existing proposal | [#95](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/95) |

## Optional messaging channels

| Plan | Outcome | Dependencies | Issue |
| --- | --- | --- | --- |
| [CHANNEL-01: Telegram](planned-releases/channel-01-telegram.md) | Use a privately authorised Telegram bot to talk to Codex. | HA-01 and channel identity policy | [#108](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/108) |
| [CHANNEL-02: Discord](planned-releases/channel-02-discord.md) | Local candidate: exact user/server/channel rules, separate DMs, private credentials and bounded task results; native qualification pending. | HA-01 and channel identity policy | [#109](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/109) |
| [CHANNEL-03: WhatsApp](planned-releases/channel-03-whatsapp.md) | Connect an approved WhatsApp provider to bounded Codex tasks. | HA-01, provider choice and webhook authentication | [#110](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/110) |

The initial priority is MCP-01 and MCP-02, followed by MCP-05 and MCP-06.
Interactive requests and stdio have separate security and lifecycle controls.
The remaining plans are independently trackable; this ordering does not set a
delivery date or commit to a provider's costs.

## Comparison evidence and reuse

- [Codex for Home Assistant](https://github.com/moryoav/home-assistant-codex)
  documents HA actions, task-result events, notifications, status entities,
  configuration access and aarch64 images.
- [Codex App](https://github.com/kecksdigital/codex-hass) documents direct file
  access and optional HA MCP integration.
- [Amira](https://github.com/Bobsilvio/ha-claude) documents MCP management,
  scheduled work and Telegram, Discord and WhatsApp connections. Its advertised
  Codex provider does not establish equivalence to the native Codex agent.
- [Codex MCP documentation](https://developers.openai.com/codex/mcp/) describes
  native transport, authentication and tool-filtering options. Check the pinned
  runtime's actual contract before relying on a newly documented option.

These comparisons come from documentation, not installation or security audits
of the other projects. Their features do not prove compatibility with Bridge's
sandbox or Home Assistant authentication model. No implementation has been
copied for this roadmap. Any later reuse must preserve the source licence and
required attribution; Amira's noncommercial code must not be imported into our
MIT distribution as though it were MIT-licensed. Implement its useful feature
ideas independently unless suitable permission is obtained.

## Already available

The graphical chat UI, saved chats/projects, scheduled-task editor, Stop/Steer,
context usage, workspace terminal, file previews, skills/plugins, appearance
settings, public-site browser worker and optional HAOS Host Access App are
already available, with the limits described in the user guides. Full desktop parity and unrestricted MCP support are not
claims of the current release.
