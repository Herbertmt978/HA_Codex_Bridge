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
and configured MCP tools have their own access rules. Broader access is a
[planned feature](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/91).

The model and reasoning menus use the installed runtime's catalogue. If a saved
choice is no longer advertised, it remains visible with an unavailable label;
choose a currently available model before starting work with it.

## Appearance

- **Theme:** follow Home Assistant, or choose Light or Dark for this panel.
- **Chat text size:** choose Default, Large or Larger for messages and the composer.
- **Motion:** follow your device preference or reduce animations in the panel.

Changes apply immediately and are saved for the current Home Assistant user in
this browser. They do not change Home Assistant's theme or carry over to another
device. If browser storage is unavailable, the panel explains that the change
will last only for the current visit.

## Other settings

**MCP servers** manages trusted HTTPS tools after MCP has been enabled in the
App. **Instructions** edits global or project instructions. **Keyboard shortcuts**
lists the supported shortcuts, and **About / security** explains where Codex
runs and the access limits. These are the settings supported by Codex Bridge;
desktop features that depend on a local PC are not implied by these controls.

The **Skills** page groups skills by their category or plugin prefix, with a
separate heading and count for each group. Skills without a prefix appear under
**General**. Each skill keeps its own enable, disable and delete controls.
