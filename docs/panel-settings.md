# Panel settings

Open **Settings** in the Codex Bridge sidebar.

## General

Choose the permissions, model and reasoning level for new chats created in
this browser. **Inherit** uses the selected project's defaults. Changing these
preferences does not alter existing chats or scheduled tasks.

- **Observe** keeps the workspace read-only and asks before commands.
- **Edit workspace** allows file changes and asks before commands.
- **Full auto · workspace** lets Codex work automatically within the selected
  workspace and the tools enabled by the administrator.

Full auto does not grant access to Home Assistant's configuration, the VM's
files or unrestricted networking. Native web search, optional browser tools
and configured MCP tools have their own access rules.

The model and reasoning menus use the installed runtime's catalogue. If a saved
choice is no longer advertised, it remains visible with an unavailable label;
choose a currently available model before starting work with it.

## Access

Open **Settings → Access** for workspace permissions, Home Assistant tools and
host control. **General** also links to this section below the new-chat defaults.

**Full access · Home Assistant OS** is a separate, optional mode for a particular
chat or scheduled task. It requires the Codex Host Access App. If that App is
ready, selecting the mode shows the warning, an unchecked acknowledgement and
the enable action. If it is missing or unavailable, the dialog links to the
[installation instructions](../codex_host_access_app/DOCS.md).

Host access means root commands on HAOS, with access to host files, saved
credentials, services, the internet and the local network. It is never a
new-chat default. **Revoke host access** invalidates existing host selections
and stops new requests; it cannot undo changes already made.

An older App connection shows update guidance instead of hiding host access.
Update the App and HACS Integration separately, restart Home Assistant and
reload the panel. **Check connection options again** refreshes capabilities;
it does not install an update or grant access.

For devices and automations, the Access page links to the optional
[HA-MCP setup guide](home-assistant-mcp.md). MCP permissions and host access
are independent.

## Appearance

Chat responses use plain text on the conversation background, with Copy below
each completed response. Messages have no avatars or repeated assistant headings;
screen readers still identify who sent each message. A partial response keeps its
label if a run stops before finishing. Each run has one completion indicator.
Select it to open run details when steps or activity history are available.

- **Theme:** follow Home Assistant, or choose Light or Dark for this panel.
- **Chat text size:** choose Default, Large or Larger for messages and the composer.
- **Motion:** follow your device preference or reduce animations in the panel.

Changes apply immediately and are saved for the current Home Assistant user in
this browser. They do not change Home Assistant's theme or carry over to another
device. If browser storage is unavailable, the panel explains that the change
will last only for the current visit.

## Other settings

**MCP servers** manages trusted public HTTPS tools and explicitly approved local
connections after MCP has been enabled in the
App. **Add MCP server** offers guided **Home Assistant (HA-MCP)** setup alongside
**Other MCP server** for custom connections. App and Integration 1.3.0 also offer
write-only bearer tokens and API-key headers.
See the [MCP guide](home-assistant-mcp.md) for permission, transport and backup
warnings. **Instructions** edits global or
project instructions. **Keyboard shortcuts**
lists the supported shortcuts, and **About / security** explains where Codex
runs and the access limits. These are the settings supported by Codex Bridge;
desktop features that depend on a local PC are not implied by these controls.

The **Skills** page groups skills by their category or plugin prefix, with a
separate heading and count for each group. Skills without a prefix appear under
**General**. Each skill keeps its own enable, disable and delete controls.
