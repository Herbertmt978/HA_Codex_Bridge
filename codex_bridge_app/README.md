# Codex Bridge App

This App runs Codex and the private Bridge service for the Codex Bridge panel
in Home Assistant. Install the [HACS Integration](../docs/installation.md) as
well; the App itself does not have a browser interface.

## Status

This release pairs App/Integration/panel **1.6.1**, Bridge **0.13.0** and Codex
**0.156.1**. The stable App supports **amd64 Home Assistant OS**.
See the [changelog](CHANGELOG.md) and
[published releases](https://github.com/Herbertmt978/HA_Codex_Bridge/releases)
for available versions.

## Installation model

Add this repository to **Settings → Apps → App store → Repositories**, then
install and start **Codex Bridge**. Confirm its discovery under **Settings →
Devices & services** after the HACS Integration is installed. Supervisor
supplies the private connection; no address, port or token needs copying.

Follow the [installation guide](../docs/installation.md) for the full sequence.
The App has no ingress route or public Bridge endpoint.

## Storage and authentication

Choose **Sign in with ChatGPT** in the Home Assistant panel and complete the
device-login page. No OpenAI API key is used. Workspaces are created beneath
the App's `/config/workspaces`; private login and Bridge state remain in its
data volume. This workspace path is separate from Home Assistant Core's
configuration directory.

## Automations, instructions, and extensions

Use **Scheduled** to set a title, task instructions, chat, repeat pattern and
time. Home Assistant runs the schedule; your PC can be off. See the
[Scheduled guide](../docs/scheduled-tasks.md).

Skills, plugins and instructions are managed through the panel. Models,
reasoning levels, native web search and image generation depend on the
installed Codex runtime and account. The App-owned browser worker is still
disabled; [issue #43](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/43)
tracks its isolation requirements.

MCP is disabled by default. Enable **Enable MCP** in App configuration and
restart only if you intend to connect a trusted HTTPS server. Read
[App documentation](DOCS.md) before configuring it.

## Updates and recovery

The App store updates the runtime; HACS updates the Integration and panel.
Make a [cold backup](../docs/backup-restore.md) before an App update, then
follow the [update instructions](../docs/installation.md#update-an-existing-installation).
The published image is signed and immutable. Arbitrary image rollback is not
validated, and a successful backup does not by itself prove a restore.
