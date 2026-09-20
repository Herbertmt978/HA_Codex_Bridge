# Installation

## Status before you start

You need both the HACS Integration and the Supervisor App. The Integration
adds the panel; the App runs Codex. This release pairs App, Integration and
panel `1.0.6`, with Bridge `0.7.9` and Codex `0.155.1`.

Install versions available on the
[Releases page](https://github.com/Herbertmt978/HA_Codex_Bridge/releases/latest).
Source changes on GitHub do not become installable updates until publication
finishes. This is a community custom repository, not an official Home Assistant
or HACS integration.

## Prerequisites

- Home Assistant Core `2026.7.2` or newer, running on Home Assistant OS for
  `amd64`, with administrator access.
- HACS, plus a ChatGPT account that can use Codex. No OpenAI API key is needed.
- Browser access to the ChatGPT device-login page for initial sign-in and
  re-authentication.

Home Assistant Container cannot run Supervisor Apps. A Windows VM is optional
legacy infrastructure for the external Bridge; it is not a requirement for
this installation. See [external-Bridge migration](migration-from-windows.md)
if you already use one.

## Install the Integration

1. Open HACS and its **Custom repositories** menu.
2. Add `https://github.com/Herbertmt978/HA_Codex_Bridge` with category
   **Integration**.
3. Find **Codex Bridge**, download its latest release, and restart Home
   Assistant.

Continue with the App installation before completing integration discovery.
Installing through HACS alone does not run Codex.

## Install the App

1. Open **Settings → Apps → App store**.
2. Open the three-dot menu, choose **Repositories**, and add
   `https://github.com/Herbertmt978/HA_Codex_Bridge`.
3. Find **Codex Bridge**, install it and select **Start**.
4. Wait for startup to finish, then open **Settings → Devices & services**.
5. Select **Configure** on the discovered **Codex Bridge** integration and
   confirm it.

Supervisor supplies the private connection automatically. There is no App web
page to open and no host, port or token to copy. If discovery arrives while
the App is still starting, wait for readiness and retry the same discovery
flow. If logs report `sandbox_unavailable`, see [Support](../SUPPORT.md);
do not change App permissions to bypass the check.

## First run

1. Open **Codex Bridge** in the Home Assistant sidebar as an administrator.
2. Choose **Sign in with ChatGPT**. Open the displayed device-login page,
   enter the one-time code and approve the intended ChatGPT account.
3. Wait for the panel to confirm the account. **Cancel** ends a pending login;
   **Sign out** disconnects an account that is already signed in.
4. Choose **New chat** and send a simple prompt. Create a project when you
   want to group related chats, and upload any files Codex needs.

The App creates private workspaces beneath its own `/config/workspaces`.
That path belongs to the App, not Home Assistant Core's configuration folder.
Do not add broad host or Home Assistant configuration mounts.

Changing ChatGPT accounts leaves local chats, files and projects in place.
The next message starts through the newly connected account; earlier local
messages are not automatically replayed to that account.

## Configure capabilities

Use the sidebar for **Scheduled**, **Skills**, **Plugins** and **Settings**.
The [Scheduled guide](scheduled-tasks.md) explains task titles, instructions,
chat selection, repeat options and time zones.

Models and reasoning levels come from Codex and your account. Update the App
if a newly available model is missing. Web search and image generation depend
on the runtime's advertised support and the signed-in account.

MCP is off by default. To use it, open **Settings → Apps → Codex Bridge →
Configuration**, enable **Enable MCP**, save and restart the App. Add only
trusted HTTPS servers. Private addresses and bearer-token settings are not
supported; see [App documentation](../codex_bridge_app/DOCS.md).

## Update an existing installation

1. Finish active work, pause scheduled tasks and make a
   [cold backup](backup-restore.md).
2. Read the release notes to see which components changed.
3. In **Settings → Apps → Codex Bridge**, install the available App update.
   This updates Codex and the Bridge service.
4. In HACS, open **Codex Bridge** and update the Integration. If necessary,
   use **Redownload** and select the latest published version.
5. Restart Home Assistant after the Integration update, then reload every
   open Codex Bridge tab. This loads the new panel code.
6. Check that the App is running, ChatGPT is connected and existing chats are
   present before resuming scheduled work.

If no update appears, first check the GitHub release exists and its App
publication workflow succeeded. Refresh the App store/repository and HACS
repository information separately. Compare their installed versions with the
release notes: one component may already be current. A hard browser reload
refreshes the page but does not install an Integration or App update.

The App's **Auto update** option can install newly published App images. It
does not replace HACS Integration updates. Codex does not self-update inside a
running App container.

## After installation

Read [Remote access](remote-access.md) before using the panel away from home.
Check [Backup and recovery](backup-restore.md) before relying on a restore;
arbitrary previous-image rollback remains unvalidated. Keep credentials,
device codes and private workspace contents out of public diagnostics.
Use [Support](../SUPPORT.md) if setup or an update fails.
