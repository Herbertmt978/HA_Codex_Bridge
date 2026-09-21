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
3. In **Settings → Apps → Codex Bridge → Configuration**, enable **Enable MCP**.
   For a server on your home network, also enable **Enable local MCP connections**.
   Save and restart Codex Bridge. Both options are off by default.
4. Open **Codex Bridge → Settings → MCP servers → Add MCP server →
   Home Assistant (HA-MCP)**. The guide walks through installation, tool
   permissions and enabling MCP. Select **Check connection options again**
   after restarting the App. Keep the suggested name `home-assistant`, or
   use another name with lowercase letters, numbers, hyphens or underscores.
5. Configure the **HA-MCP Server** entry under **Settings → Devices & services**
   and copy its connection URL into the Bridge form. For a local URL, select
   **Connect to a local network or Home Assistant App server** and acknowledge
   the warning. The URL is masked because it may include a secret. Add the
   server, then use **Refresh server status** to check the connection. For a
   public HTTPS server that advertises OAuth, complete **Sign in**; optional
   public OAuth fields are under **OAuth settings**. Local OAuth is not supported.
6. Start a new chat and first ask Codex to find or describe an entity. Confirm
   the result before asking it to change a device, automation or configuration.

**Other MCP server** remains available in the same Add menu. It accepts any
compatible public HTTPS MCP server with optional OAuth, or an explicitly
enabled local HTTP/HTTPS server. The
HA-MCP guide does not install software or grant additional permissions for you.

## Connection requirements

Bridge supports streamable-HTTP MCP. Public connections use trusted HTTPS
hostnames and optional OAuth. Local connections require App and Integration
1.2.0 or later, both App options above, and acknowledgement for each endpoint.

For the HA-MCP custom component, copy the **Local network** webhook URL from
its Configure screen. It uses your HA hostname or IP, port 8123 and a private
webhook path. You can also use an HA-MCP App's supported connection URL. Use
the address reachable from Codex Bridge, not `localhost`: that refers to the
Bridge container. Do not paste a Home Assistant access token into the URL.

Local addresses must resolve entirely to private LAN or App-network addresses
(RFC1918 IPv4 or IPv6 ULA). Loopback, link-local, metadata, Supervisor and
public destinations are blocked. The Bridge records the approved addresses
and checks them on every request. Redirects are not followed. If DNS changes
to a new IP, remove and add the server again to approve its new destination.
HTTPS still needs a certificate trusted by the App and valid for that hostname;
there is no certificate-bypass option.

**HTTP is unencrypted.** People or software able to inspect that network traffic
may see requests, responses and the secret connection path. Use it only on a
network you trust. Codex can use whatever tools the server exposes, so review
the server's permissions before connecting. This does not grant root access
or network access to workspace commands.

For public HTTPS, use HA-MCP's supported connection through your existing Home Assistant
remote-access route, where available. Its
[server documentation](https://github.com/homeassistant-ai/ha-mcp/blob/master/docs/in-process-server.md)
explains webhook connections through Nabu Casa or an existing reverse proxy.
Keep the Bridge and Host Access ports private. Do not disable endpoint checks
or expose an unauthenticated MCP listener to make a connection work.

Some HA-MCP connection URLs contain a secret in their path. Treat the complete
URL as a credential: enter it only in the server configuration, and keep it out
of prompts, screenshots, issue reports and shared instructions. Local connection
paths are stored privately in the App and omitted from the server list and
native connection diagnostics.

Local OAuth, bearer/API tokens, custom authentication headers, query strings,
stdio servers and interactive MCP questions are not supported by this release.
Servers requiring those options need a later compatibility release. Do not
remove authentication from a server to work around this restriction.

## Remove or disable local access

Use **Remove** beside a server to revoke its connection. To stop all local
connections, turn off **Enable local MCP connections**, save and restart the
App. Restart removes local bindings from Codex while retaining valid public
HTTPS connections. Re-enabling the option does not restore removed connections;
add and acknowledge them again. Turning off **Enable MCP** removes all MCP
bindings after restart.

If a server cannot connect, check its address, port, certificate and availability
from the App network. An IP change requires re-adding it. Do not include the
full private URL in diagnostics or support requests.

This is an optional integration recommendation, not a bundled server or an
automatic grant. The exact tools and connection options depend on the HA-MCP
version and your configuration.
