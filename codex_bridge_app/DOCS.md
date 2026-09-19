# Codex Bridge App documentation

For setup, follow [Installation](../docs/installation.md). For common problems,
see [Support](../SUPPORT.md). This page explains the App's settings and limits.

## Configuration

The App has one configuration option:

| Option | Default | What it does |
| --- | --- | --- |
| Enable MCP (`enable_mcp`) | Off | Allows configuration of trusted outbound HTTPS MCP servers. Save and restart the App after changing it. |

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
account. Astra is supported by the bundled Codex `0.155.1` when advertised for
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

MCP is disabled by default, including saved server configuration. Enable the
App option and restart before adding a server in **Settings → MCP servers**.
Disabling MCP and restarting clears the saved native MCP server table; it
leaves skills, plugins, marketplaces and instructions alone.

Only outbound streamable-HTTP servers at trusted HTTPS hostnames are allowed.
Literal IPs, local/internal names and known non-public DNS answers are rejected.
DNS checks happen during validation, not on every connection, so you must
still trust the provider. Bearer-token settings are not supported.

OAuth sign-in is an explicit, one-time flow. Do not save or share its temporary
URL. MCP requests that require elicitation are declined. Enabling MCP does not
publish an App or Bridge endpoint.

## Search, images and browser support

Native web search is enabled by default for Supervisor connections when the
runtime supports it; Integration options can disable it. Look for search
activity when a task needs current information. Image generation also depends
on runtime support and the signed-in account. It does not need an API key.

File previews and downloads use Home Assistant's authenticated route. The PDF
viewer renders validated files locally, with a maximum size of 8 MB and
scripting disabled. It is not an interactive web browser.

The App-owned browser worker remains disabled pending isolation and network
attestation. [Issue #43](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/43)
and the [browser acceptance record](../docs/acceptance/browser-worker.md)
explain the remaining work. Native web search does not enable this worker.

## Updates and recovery

This release pairs App, Integration and panel `1.0.5`, with Bridge `0.7.8` and
Codex `0.155.1`. Update the App through Supervisor and the Integration through
HACS, then restart Home Assistant and reload the panel after an Integration
change. See [update troubleshooting](../docs/installation.md#update-an-existing-installation).

Codex is part of the immutable App image and does not self-update inside a
running container. The daily verified updater opens repository PRs for stable
runtime releases; a signed App release must be published before HA can use it.

Take a [cold backup](../docs/backup-restore.md) before updating. Full cold
restore acceptance and arbitrary App-image rollback remain unvalidated.
Remote access must terminate at Home Assistant, using its normal Nabu Casa,
Cloudflare or HTTPS reverse-proxy route. Keep the App and Bridge private.
