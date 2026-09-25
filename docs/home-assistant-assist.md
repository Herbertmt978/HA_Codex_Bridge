# Codex Bridge as an Assist conversation agent

The Codex Bridge Integration can provide a selectable Home Assistant Assist
conversation agent. It sends a question to Codex in a project chosen by a Home
Assistant administrator and returns Codex's final text as Assist's reply. A
follow-up in the same Assist conversation reuses the same Codex chat. It does
not replace Home Assistant's built-in device-control agent.

## Set up

1. In Codex Bridge, create a dedicated project for Assist. Keep only material
   that everyone allowed to use this agent may hear in its workspace. Do not
   use a project containing private chats or configuration files.
2. In **Settings → Devices & services → Codex Bridge → Configure**, enable
   **Codex Bridge Assist conversation agent** and select that project. The
   agent is off by default.
3. Select **Codex Bridge Assist** as the conversation agent in the desired
   Assist pipeline. A signed-in Home Assistant administrator can use it.
4. To use a voice satellite or another Assist entry point without a signed-in
   HA user, explicitly enable **Allow voice requests without a signed-in HA
   user**. Voice recognition is not identity verification; everyone who can
   reach that pipeline can ask questions of the selected project.

The App must advertise `assist_conversation_v1` and be signed in to Codex.
Assist is unavailable while MCP is enabled in the App because MCP tools are
configured globally in the Codex runtime and cannot yet be isolated reliably
for one voice request. Turn off MCP in the App configuration only if you want
to use Assist in this release. This does not change MCP settings automatically.

## Scope and responses

Each Assist turn uses **Observe** mode, disabled web search and unattended
request handling. It has no Home Assistant OS host-access grant, browser
dynamic tools, image publication or ability to approve an interactive request.
The selected project's workspace remains readable to Codex and its answer may
quote material from it. Assist sends only the question text to the App. It does
not send HA states, devices, areas, user identity, voice-device identity or the
HA chat log. The Bridge keeps the Codex chat in the selected project; HA keeps
its own Assist chat log according to HA's session lifecycle.

Assist waits up to 90 seconds for a direct answer. If Codex is still working,
Assist says so and the task continues in the Codex Bridge panel. A later
question in the same conversation waits for that task to finish before it can
start another turn. A failed run returns a fixed, non-sensitive message rather
than raw runtime output. Conversation IDs are opaque, bound to the initiating
HA administrator (or the explicitly enabled no-user voice class), kept only
in memory and limited to 64 active sessions. An Integration reload starts new
Assist conversations; existing Codex chats remain available in the panel.

Assist cannot control HA devices. Use the normal HA conversation agent for
device control. The native `codex_bridge.start_task` and related actions are
separate administrative automation features described in
[Home Assistant task actions](home-assistant-task-actions.md).
