# Codex Bridge App documentation

For setup, follow [Installation](../docs/installation.md). For common problems,
see [Support](../SUPPORT.md). This page explains the App's settings and limits.

## Configuration

The App has two optional capabilities:

| Option | Default | What it does |
| --- | --- | --- |
| Enable MCP (`enable_mcp`) | Off | Allows configuration of trusted outbound HTTPS MCP servers. Save and restart the App after changing it. |
| Enable isolated stdio MCP servers (`enable_stdio_mcp`) | Off | On amd64 HAOS, permits the separately isolated worker for verified packages. Also requires Enable MCP. Save and restart the App after changing it. |
| Enable browser (`enable_browser`) | Off | Starts the isolated interactive browser worker. Save and restart the App; new chats then receive its tools. |

ChatGPT login is completed in the Codex Bridge panel. Do not enter account
credentials, device codes or API keys in App configuration.

## Files and permissions

Workspaces live beneath the App's `/config/workspaces`. This is its own
private mapping, not Home Assistant Core's configuration directory. The App
keeps Bridge records and ChatGPT login state in private `/data` storage.
It does not mount your PC, broad host shares or Home Assistant configuration.

Observe mode gives Codex a read-only workspace. Edit and Full auto allow
changes in the selected workspace. Full auto affects approvals; it does not
remove the file or network restrictions. Model-controlled shell commands
cannot access the LAN or internet. The trusted Codex runtime can contact
OpenAI for login and responses.

The App checks its sandbox at startup. If readiness reports
`sandbox_unavailable`, collect redacted logs and use [Support](../SUPPORT.md).
Do not disable AppArmor, add container privileges or broaden mounts.

The panel's workspace terminal uses the same file and network boundary. It is
administrator-only and temporarily excludes Codex runs and other workspace
mutations. It never becomes a host shell, including in a host-access chat. See
[Chat controls and terminal](../docs/chat-controls.md) for session limits.

## Authentication and models

In the panel, choose **Sign in with ChatGPT**, open the displayed device-login
page and approve the intended account. Wait for the panel to confirm it.
**Cancel** stops a pending login; **Sign out** ends an established session.
Initial login and re-authentication require access to the ChatGPT website.
Normal panel use stays on Home Assistant.

Local chats, projects and files remain when you change ChatGPT accounts. The
next message uses the newly connected account without resuming the previous
account's private provider conversation or automatically replaying old messages.

The model and reasoning menus come from the installed Codex runtime and your
account. Astra is supported by the bundled Codex `0.157.0` when advertised for
the account. If a model is missing, check App updates and connection status;
updating the HACS Integration alone does not update Codex.

## Scheduled tasks

Open **Scheduled** and choose **New schedule**. Enter a title, instructions,
chat destination, repeat pattern and time. The preview uses Home Assistant's
time zone. **Advanced** contains permission, model and reasoning overrides.
See [Scheduled tasks](../docs/scheduled-tasks.md) for all repeat options.

Home Assistant owns the clock, so it and the App must be running. The Bridge
stores definitions and run history and prevents duplicate claims. Unattended
runs cannot answer approvals or questions; overlaps, missed windows and
capacity limits can result in a skipped run. Results appear in chats and run
history. The panel does not offer desktop or mobile notification preferences.

## Skills, plugins and instructions

Skills are scoped to the selected workspace and created under `.agents/skills/`.
Global instructions are stored in the private Codex home; project instructions
are stored in the selected project's `AGENTS.md`. Changes keep private backups.

Plugins and marketplaces are read from Codex's configuration. Plugin catalogue
loading was verified with the larger catalogue in `1.0.3`; this does not mean
every third-party plugin has been tested. Review each plugin and marketplace
before installing it.

## MCP servers

For Home Assistant control, consider [HA-MCP](https://github.com/homeassistant-ai/ha-mcp).
It is installed separately and does not require root host access. Read the
[Bridge setup guide](../docs/home-assistant-mcp.md) before enabling it.

MCP is disabled by default, including saved server configuration. Enable the
App option and restart before adding a server in **Settings → MCP servers**.
Disabling MCP and restarting clears the saved native MCP server table; it
leaves skills, plugins, marketplaces and instructions alone.

Public streamable-HTTP servers use trusted HTTPS hostnames and optional OAuth.
Their DNS check is a validation-time screen, so you must still trust the provider.
Local HTTP/HTTPS servers require the separate **Enable local MCP connections**
option, a restart and acknowledgement in the connection form. Local requests
use a private relay that pins approved private addresses and rejects redirects.
HTTP is unencrypted; HTTPS certificates must validate normally. Turning the
local option off and restarting removes local bindings without removing valid
public connections. See the [setup guide](../docs/home-assistant-mcp.md) for
allowed destinations and how to reconnect after an IP change.

Bearer tokens and named authentication headers can be configured privately for
an approved endpoint in **Settings → MCP servers**. Their values are write-only
and are not placed in the native Codex configuration. Local OAuth and arbitrary
stdio commands remain unsupported.

On amd64 HAOS, **Enable isolated stdio MCP servers** adds a separate catalogue under
**Settings → MCP servers**. The first reviewed package, Bridge Time 1.0.0,
answers local time and timezone questions. Its source, fixed command, exact
package digest and access limits appear before you add it. New connections are
paused with no allowed tools: inspect its tool descriptions, select the tools
you trust, then resume it. A package update pauses the connection and requires
another review; the previous bundled revision can be selected for rollback
when available. Removal stops its sessions and discards its saved policy.

The worker has no network, workspace, Home Assistant configuration, Codex
login or Supervisor credential access. Only the fixed bundled Python 3.14
package and scratch space are visible. Bridge verifies the package and worker
isolation on start; if either check fails, the panel shows a recovery state and
does not launch the package. Disabling this option and restarting prevents new
stdio workers without deleting saved connection choices.

OAuth sign-in is an explicit, one-time flow. Do not save or share its temporary
URL. Supported forms and authorisation links can be answered in the active chat;
unknown or credential-looking forms and all unattended requests are declined.
Enabling MCP does not publish an App or Bridge endpoint.

## Search, images and browser support

Native web search is enabled by default for Supervisor connections when the
runtime supports it; Integration options can disable it. Look for search
activity when a task needs current information. Image generation also depends
on runtime support and the signed-in account. It does not need an API key.

File previews and downloads use Home Assistant's authenticated route. The PDF
viewer renders validated files locally, with a maximum size of 8 MB and
scripting disabled. It is not an interactive web browser.

To let Codex interact with public websites, turn on **Enable browser tools**
in this App's Configuration tab, save, and restart the App. Start a new chat
afterwards. The tools become available only after the App verifies browser
isolation. Existing chats keep their existing runtime tools. Browser sessions
are temporary; they cannot use your Chrome login or reach Home Assistant and
other local devices. See [Browser tools](../docs/browser-tools.md) for examples
and limits.

## Updates and recovery

This release pairs App, Integration and panel `1.8.2`, with Bridge `0.14.2` and
Codex `0.157.0`. Update the App through Supervisor and the Integration through
HACS, then restart Home Assistant and reload the panel after an Integration
change. See [update troubleshooting](../docs/installation.md#update-an-existing-installation).

Codex is part of the immutable App image and does not self-update inside a
running container. The daily verified updater opens repository PRs for stable
runtime releases; a signed App release must be published before HA can use it.

Take a [cold backup](../docs/backup-restore.md) before updating. Full cold
restore acceptance and arbitrary App-image rollback remain unvalidated.
Remote access must terminate at Home Assistant, using its normal Nabu Casa,
Cloudflare or HTTPS reverse-proxy route. Keep the App and Bridge private.
