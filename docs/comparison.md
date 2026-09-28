# How Codex Bridge compares

Documentation comparison reviewed on 28 September 2026. This is not a benchmark
or an installation or security audit of the other projects. Features can change;
use each project's documentation to check its current requirements.

## Where Bridge fits

Codex Bridge brings an always-on Codex workspace into Home Assistant: project
chats, files, selected context, plans, Git review, scheduled work, account
switching and optional home control. Your PC can be off while Home Assistant
runs scheduled tasks. ChatGPT sign-in avoids a separate OpenAI API key for Codex.
AI processing still uses OpenAI's cloud.

Its main strength is bringing those everyday work features together in the
Home Assistant sidebar, with Home Assistant handling browser access and a
private App managing the runtime. See the [feature overview](../README.md#automations-and-codex-capabilities).

## Other approaches

| Offering | Documented focus | How it differs from Bridge |
| --- | --- | --- |
| [Codex for Home Assistant](https://github.com/moryoav/home-assistant-codex) | Codex against HA configuration, configuration validation, dashboard browser checks and amd64/aarch64 images. | A strong fit for direct HA configuration work. Bridge centres on private project workspaces and explicit optional home access; its stable App is amd64-only. |
| [Codex App](https://github.com/kecksdigital/codex-hass) | A browser terminal running Codex in the HA configuration directory, with optional HA MCP. | A terminal-first route. Bridge supplies a graphical chat and project workspace through an Integration and App. |
| [Amira](https://github.com/Bobsilvio/ha-claude) | Multiple AI providers, HA tools, document retrieval, scheduled work and messaging connections. | Broader provider and messaging choices. Bridge focuses on the Codex runtime and ChatGPT sign-in; a provider called Codex does not by itself establish identical runtime capabilities. |
| [Home Assistant OpenAI integration](https://www.home-assistant.io/integrations/openai_conversation/) | Native conversation, AI Task, speech features and optional control of exposed entities using an OpenAI API key. | A direct HA-native route for those services. Bridge adds its own Codex project workspace and uses ChatGPT sign-in for Codex. |

[HA-MCP](https://github.com/homeassistant-ai/ha-mcp) and
[Home Assistant's MCP Server](https://www.home-assistant.io/integrations/mcp_server/)
are complementary tool servers: they give an AI client selected Home Assistant
capabilities. They are not replacements for the Bridge workspace. Bridge can
connect to supported installations with explicit access and tool choices.

Choose Bridge when you want Codex available inside Home Assistant for ongoing
projects and scheduled work. Check a project's current hardware, provider and
configuration-access requirements when those are the deciding factors. No
claim of full desktop Codex parity or universal MCP compatibility is intended.
