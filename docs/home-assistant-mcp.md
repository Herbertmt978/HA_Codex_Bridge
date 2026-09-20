# Home Assistant tools through MCP

For managing Home Assistant from Codex, we recommend considering
[HA-MCP](https://github.com/homeassistant-ai/ha-mcp). This community project
provides tools for finding entities, checking states, controlling devices and
managing automations. Install it separately and enable only the tools you need.

HA-MCP is useful alongside Codex Bridge's normal workspace mode. It does not
require the Host Access App. Use Home Assistant tools for Home Assistant tasks;
choose [Host Access](../codex_host_access_app/DOCS.md) when a task actually needs
root commands on the HAOS machine. MCP access and host access are separate
permissions: revoking one does not revoke the other.

## Install and connect

1. Follow the [HA-MCP installation guide](https://github.com/homeassistant-ai/ha-mcp#-get-started).
   Its maintainers recommend the HACS custom component; the Supervisor App is
   also an option on HAOS. Choose one server installation, not both. Check its
   current Home Assistant version requirements before installing.
2. In HA-MCP, select the tools and permissions you want Codex to use. Review
   write operations and any optional file or YAML editing tools before enabling
   them. Those rights can affect your home even without HAOS root access.
3. In **Settings → Apps → Codex Bridge → Configuration**, enable **Enable MCP**,
   save and restart Codex Bridge.
4. Open **Codex Bridge → Settings → MCP servers → Add MCP server**. Give it a
   name such as **Home Assistant** and enter your own compatible HTTPS MCP URL.
   Complete **Sign in** if the server advertises OAuth.
5. Start a new chat and first ask Codex to find or describe an entity. Confirm
   the result before asking it to change a device, automation or configuration.

## Connection requirements

Bridge currently accepts outbound streamable-HTTP MCP at trusted HTTPS
hostnames. It rejects HTTP, literal IP addresses, local/internal hostnames,
known private DNS addresses, URLs with query strings and bearer-token settings.
A local HA-MCP address therefore cannot be pasted directly into this version
of Bridge, even when both Apps run on the same HAOS machine.

Use HA-MCP's supported HTTPS connection through your existing Home Assistant
remote-access route, where available. Its
[server documentation](https://github.com/homeassistant-ai/ha-mcp/blob/master/docs/in-process-server.md)
explains webhook connections through Nabu Casa or an existing reverse proxy.
Keep the Bridge and Host Access ports private. Do not disable endpoint checks
or expose an unauthenticated MCP listener to make a connection work.

Some HA-MCP connection URLs contain a secret in their path. Treat the complete
URL as a credential: enter it only in the server configuration, and keep it out
of prompts, screenshots, issue reports and shared instructions. If your
deployment requires unsupported authentication or is LAN-only, leave this
connection disabled until a compatible route is available.

This is an optional integration recommendation, not a bundled server or an
automatic grant. The exact tools and connection options depend on the HA-MCP
version and your configuration.
