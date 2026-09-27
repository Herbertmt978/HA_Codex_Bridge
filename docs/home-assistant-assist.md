# Codex Bridge as an Assist conversation agent

The Codex Bridge Integration can provide a selectable Home Assistant Assist
conversation agent. It sends a question to Codex in a project chosen by a Home
Assistant administrator and returns Codex's final text as Assist's reply. A
follow-up in the same Assist conversation reuses the same Codex chat. It can
optionally use administrator-selected MCP tools for home control.

## Set up

1. In Codex Bridge, create a dedicated project for Assist. Keep only material
   that everyone allowed to use this agent may hear in its workspace. Do not
   use a project containing private chats or configuration files.
2. In **Settings → Devices & services → Codex Bridge → Configure**, enable
   **Codex Bridge Assist conversation agent** and select that project. The
   agent is off by default. Configure **Assist model**, **Assist reasoning level**
   and **Assist instructions** here too. Model choices come from the signed-in
   Codex account; leave model or reasoning unset to use the project's defaults.
   The existing conversation entity appears on the Codex Bridge device.
   **Assist MCP servers** selects which configured MCP connections this agent
   can use. Leave it empty for answers without MCP tools. Custom compatible
   servers are supported; Home Assistant's native MCP integration is optional.
3. Select **Codex Bridge Assist** as the conversation agent in the desired
   Assist pipeline. A signed-in Home Assistant administrator can use it.
4. To use a voice satellite or another Assist entry point without a signed-in
   HA user, explicitly enable **Allow voice requests without a signed-in HA
   user**. Voice recognition is not identity verification; everyone who can
   reach that pipeline can ask questions of the selected project.

The App must advertise `assist_conversation_v1` and be signed in to Codex.
An App advertising `assist_mcp_selection_v1` isolates each Assist session from
ordinary chats' MCP settings. An empty selection works with global MCP enabled;
selected servers require **Enable MCP** and must remain enabled and approved.
An older App still requires MCP to be disabled and cannot accept a selection.

## Home control

For the installed **MCP Server** Home Assistant integration, enable **Enable
MCP** and **Enable local MCP connections** in the Bridge App, then open
**Codex Bridge → Settings → MCP servers**. Authorise the Home Assistant
connection there as an active HA administrator. No URL or token copying is
needed. The Integration creates a separate, revocable authorisation for that
administrator and the App stores its short-lived access token privately.

The connection starts with no tools permitted. Choose the allowed tools
before use. Its server is added to the Assist selection, without enabling
the agent or changing its project, model, instructions or voice setting.
Use **Configure** on Codex Bridge to review that selection. Alternatively,
configure another compatible MCP server and select it there.

Selected tools can operate devices or services with the MCP server's authority.
For the native Assist MCP endpoint, HA's Assist API and exposed-entity rules
apply. Other servers can provide broader tools; the Bridge's selection is not
an exposed-entity boundary. Review each server's permissions. Voice users act
through the authorised connection, rather than acquiring their own HA identity.
Instructions and the **Observe** filesystem mode do not restrict MCP actions.

Disconnecting revokes only the managed authorisation and removes its owned
Assist selection. Editing the managed server's destination or credential stops
automatic renewal; the Integration revokes its grant and leaves the edited
server alone. Access tokens expire after eight hours and renew hourly when
the runtime is idle. Revoked, expired or unavailable authority never creates a
replacement grant silently. Reload preserves the existing grant; removing the
Integration revokes it. If the App is unreachable, server cleanup may remain
pending, but the HA authorisation is still revoked.

## Scope and responses

Each Assist turn uses **Observe** mode, disabled web search and unattended
request handling. It has no Home Assistant OS host-access grant, browser
dynamic tools, image publication or ability to approve an interactive request.
The selected project's workspace remains readable to Codex and its answer may
quote material from it. Assist sends the question and any configured plain-text
instructions to the App. It does
not include HA states, devices, areas, user identity, voice-device identity or the
HA chat log in the prompt. Selected MCP tools can retrieve their authorised
context separately. The Bridge keeps the Codex chat in the selected project; HA keeps
its own Assist chat log according to HA's session lifecycle.

Assist waits up to 90 seconds for a direct answer. If Codex is still working,
Assist says so and the task continues in the Codex Bridge panel. A later
question in the same conversation waits for that task to finish before it can
start another turn. A failed run returns a fixed, non-sensitive message rather
than raw runtime output. Conversation IDs are opaque, bound to the initiating
HA administrator (or the explicitly enabled no-user voice class), kept only
in memory and limited to 64 active sessions. An Integration reload starts new
Assist conversations; existing Codex chats remain available in the panel.

Without selected MCP tools, Assist cannot control HA devices. The native
`codex_bridge.start_task` and related actions are
separate administrative automation features described in
[Home Assistant task actions](home-assistant-task-actions.md).

Instructions are limited to 4,096 characters and are not rendered as templates.
They do not grant tools or device access. Unsupported model/reasoning pairs
must be corrected in settings; the agent does not silently substitute another
model after an account change. Existing accepted tasks remain safe to retry.
Explicit model and reasoning settings require App 1.9.2 or later. With an older
App, leave those settings unset to retain the existing project-default agent.

Assist runs from an empty private runtime directory, with an explicit read-only
grant for the selected workspace. Project configuration and `AGENTS.md`, skills,
plugins, connected apps, shell tools, browser tools and child agents do not load
for this agent. Use **Assist instructions** for its plain-text behaviour. Native
sessions are unloaded before releasing their runtime lease, and a changed MCP
selection requires a new Assist conversation. Existing history is preserved.

These conversations appear under **HA Assistant Chats**. Their history and files
remain accessible, with a red notice explaining that messages are managed by
Home Assistant Assist. Continue in Assist, or create an ordinary Bridge chat.
Their project routing and native history remain managed by HA; fork and project
move are unavailable for these chats. Ordinary chats keep both actions.
