# Optional access to Home Assistant OS

Codex Host Access gives Codex root access to the machine running Home Assistant
OS. Install it only if you want Codex to work beyond its normal workspace.
Ordinary chats, scheduled tasks, skills and the browser worker do not need it.

For Home Assistant device and automation work, consider
[HA-MCP](https://github.com/homeassistant-ai/ha-mcp) first. It provides Home
Assistant tools and can be used alongside this App. Follow the
[Bridge connection guide](../docs/home-assistant-mcp.md). Enabling or revoking
Host Access does not change MCP permissions.

This feature currently supports amd64 Home Assistant OS installations. It does
not provide a supported host-access path for Home Assistant Container or an
external Bridge.

## What you are allowing

When enabled and selected for a task, Codex can:

- Run root commands, install or run software, manage containers and services,
  and stop or restart Home Assistant.
- Read, change or delete files on the host and mounted storage. This includes
  Home Assistant configuration, app data, backups, integration secrets and saved
  sign-in credentials, including Codex credentials.
- Use the host's internet and local-network connections. Stored credentials may
  allow access to other systems, including Proxmox, with their existing rights.
  Seeing an integration does not itself grant a shell on another machine.

Command output and file contents returned to Codex can be sent to the model
provider. Network commands can send data elsewhere. A mistake or malicious
content encountered during a task could expose credentials, delete data or
interrupt household automations.

Stop and Revoke prevent new Bridge requests and attempt to terminate tracked
commands. They cannot undo changes already made. Root commands can alter these
controls or start work that continues independently. This is deliberately broad
access, not a sandbox around your Home Assistant machine.

## Install and enable

1. Update both the Codex Bridge App and its Home Assistant Integration to a
   release that includes Host Access. Keep the normal Bridge App installed.
2. Open **Settings → Apps → App store**, then find **Codex Host Access** in
   the same repository as Codex Bridge. This App is marked experimental; enable
   **Advanced mode** in your HA profile if it is hidden. Install it.
3. On the Host Access App's information page, turn off **Protection mode**. This
   is required for its declared host privileges. The normal Codex Bridge App
   keeps its existing protection and permissions.
4. Start **Codex Host Access**. It verifies that it can enter the HAOS host
   environment, then pairs privately through the Integration. No port, address
   or token needs to be entered in the browser.
5. In Codex Bridge, select **Full access · Home Assistant OS**, or open
   **Settings → General → Set up host access**. Read the warning, check the
   acknowledgement and choose **Enable host access**.
6. Choose this mode explicitly for the chat or scheduled task that needs it.
   Scheduled tasks also require acknowledgement that they may use these rights
   automatically while you are absent.

If the App is already installed and ready, the selector shows the warning and
enable action directly. It does not send you through installation again. If it
is missing or unavailable, the selector shows guidance and a link to this page.

An ordinary workspace task cannot acquire host access by asking the model to
enable it. Installing the companion also does not enable it by itself.

## Disable access

Choose **Settings → General → Revoke host access** in Codex Bridge. Existing
host selections and scheduled-task acknowledgements become invalid. They must
be selected again after a new grant; ordinary workspace tasks continue normally.

For a stronger operational stop, stop or uninstall **Codex Host Access** in HA.
Review any changes or independently started work separately. Removing access
does not restore files, services or credentials changed by earlier root work.

## If setup is unavailable

- Check that both Apps and the Integration are current, then restart Codex Bridge
  followed by Codex Host Access so Supervisor discovery can run again.
- Check the Host Access App log. A failed HAOS check commonly means Protection
  mode is still enabled or the installation is not supported HAOS.
- Do not forward the companion's private port or paste credentials into a chat.
- If the machine, companion identity or warning changes, review and acknowledge
  the new warning. Tasks retain their previous selection and fail safely until
  you explicitly update it.

The companion starts manually by default. You may enable **Start on boot** in
its HA App page if you intend to use it for unattended scheduled tasks.
