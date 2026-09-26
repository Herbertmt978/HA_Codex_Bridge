# Assist MCP selection and native Home Assistant authorisation

Status: implemented locally; release and native HA acceptance pending.

## Decision

Assist defaults to no MCP tools. An administrator may select configured servers,
including custom servers, independently from ordinary chats. The selection is
canonical, immutable for an accepted task and bound to its stored conversation.
Changed permissions require a new Assist conversation; retries cannot change an
existing task's authority. Older Apps retain the previous fail-closed policy.

The native Codex session uses a Bridge-owned empty private directory outside all
workspaces. It receives an explicit read-only grant for the selected project's
workspace. Workspace configuration and instructions, plugins, apps, skills,
shell, browser and child-agent tools are disabled. Selected globally approved
MCP definitions are copied into its session overrides and all other discovered
definitions are masked. Unsupported or malformed higher-priority configuration
fails closed. The native runtime's zero idle unload delay is verified before
advertising the capability.

Cold resume follows an acknowledged native unload. Completion unloads the
session while its runtime lease is held. Lost control acknowledgements or an
invalid policy result abort the native generation before lease release. An
uncertain abort and fatal persistence error close every runtime admission,
including MCP and account configuration.

## Why a private directory

A workspace trust override alone is insufficient. Native global configuration
reload and thread-scoped inventory can rediscover workspace MCP definitions
without reapplying that trust decision. Plugin materialisation also has native
configuration reload paths outside Bridge's mutation gate. The empty private
directory removes project-layer discovery from those paths. It grants no
additional workspace or network access. Its ownership, permissions, absence of
links and empty contents are checked before every Assist start.

## Native HA shortcut

A bounded administrator HTTP action authorises the installed native MCP Server
integration. It uses the authenticated active HA administrator, the fixed
Supervisor-reachable Assist MCP endpoint and the existing private relay. It
does not accept a caller-supplied URL, user identity or token. MCP and local MCP
App options must already be enabled. No integration is installed automatically.

HA owns a separate normal refresh grant for this entry. The Integration stores
only its ownership and recovery metadata, never an access or refresh secret.
The App privately stores an eight-hour access token, renewed hourly when idle.
The server starts with a deny-all tool selection, allowing catalogue discovery
without permitting tool calls. The helper selects only
its owned name for Assist and preserves every other agent option and server.

Every automatic credential replacement and server removal requires an atomic
match of the current local bearer binding's endpoint and secret fingerprint.
Manual destination or credential edits cannot receive a renewed token or be
overwritten by cleanup. A mismatch revokes only the owned HA grant and detaches
its selection. Interrupted operations retain a bounded recovery journal.
Reload does not create authority; removing the Integration revokes it even when
the App is unavailable. Remote cleanup may then remain pending.

## Authority and limits

The native endpoint uses HA's Assist API. Custom servers can offer broader
permissions. An MCP tool grant is independent of read-only filesystem mode and
can control a home with the selected server's authority. No-user voice requests
remain separately opt-in and use that authorised connection; speech is not
identity verification. Configured instructions are plain text, not templates.

No production grant, actual home device action or physical voice result follows
from the local implementation or synthetic native runtime evidence. Release
gates and managed HA-DEV acceptance remain separate requirements.
