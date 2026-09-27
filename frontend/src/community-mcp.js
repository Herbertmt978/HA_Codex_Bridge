const COPY = Object.freeze({
  not_connected: "The installed community HA-MCP App is available. Review the destination and authorise its connection below.",
  not_installed: "No community HA-MCP App is installed. Use the HA-MCP installation and connection guide below.",
  configured: "The connection is saved. Choose allowed tools, then enable MCP for the regular chat where you want to use them.",
  paused: "The existing connection is paused. Its allowed tools are preserved; resume it when ready.",
  unavailable: "Could not confirm the community HA-MCP App. Refresh connection options or use the existing HA-MCP setup guide.",
  ambiguous: "More than one community HA-MCP App matches. Use manual server setup to choose the intended server.",
  stopped: "The community HA-MCP App is stopped. Start it in Home Assistant if you intend to use it, then refresh connection options.",
  unsupported: "Quick connect requires a Supervisor installation. Use the existing HA-MCP setup guide for this installation.",
  endpoint_unavailable: "A supported private Home Assistant address could not be confirmed. Check Home Assistant’s internal URL and the App’s published port, or use manual setup.",
  secret_unavailable: "The App has no supported saved connection path. Check its configuration and use manual setup if needed.",
  connection_changed: "The connection or destination changed. Refresh to review it again. If a saved connection still uses the old destination, remove it before reconnecting; its tools will need selecting again.",
  restart_required: "The connection result is uncertain. Restart the Codex Bridge App before reviewing its status; no connection action will be retried automatically.",
  retry: "Codex is busy. Wait for active and queued work to finish, then refresh connection options.",
  enable_mcp: "Update the Bridge App and Integration and enable MCP and local MCP connections in the App, then refresh connection options.",
});

export const supportsCommunityMcp = (capabilities = []) => Array.isArray(capabilities)
  && ["community_mcp_quick_connect_v1", "mcp_local_v1", "mcp_admin_v1", "mcp_tool_permissions_v1"].every((key) => capabilities.includes(key));

export function normalizeCommunityMcp(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof value.state !== "string" || !Object.hasOwn(COPY, value.state)
    || typeof value.reused !== "boolean"
    || (value.server_name !== null && (typeof value.server_name !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/u.test(value.server_name)))
    || (value.version !== null && (typeof value.version !== "string" || !/^\d+(?:\.\d+){1,3}(?:[a-z0-9.-]{0,24})$/iu.test(value.version)))
    || (value.consent_revision !== null && (typeof value.consent_revision !== "string" || !/^[a-f0-9]{64}$/u.test(value.consent_revision)))) return null;
  if (value.destination !== null) {
    if (typeof value.destination !== "string" || value.destination.length > 300) return null;
    try {
      const url = new URL(value.destination);
      if (url.protocol !== "http:" || url.port !== "9583" || url.username || url.password || url.search || url.hash || url.pathname !== "/") return null;
    } catch { return null; }
  }
  return { state: value.state, server_name: value.server_name, reused: value.reused,
    version: value.version, destination: value.destination, consent_revision: value.consent_revision };
}

export const communityMcpMessage = (code) => Object.hasOwn(COPY, code) ? COPY[code]
  : "The result could not be confirmed. Refresh connection options before trying again; the action was not replayed.";

export function renderCommunityMcp(doc, state, capabilities = []) {
  const node = (tag, label, className = "desktop-note") => { const element = doc.createElement(tag); element.textContent = label; element.className = className; return element; };
  const card = node("section", "", "schedule-card settings-card community-mcp-shortcut");
  card.setAttribute("aria-labelledby", "community-mcp-title");
  const title = node("h3", "Installed community HA-MCP", "desktop-subheading"); title.id = "community-mcp-title";
  const status = normalizeCommunityMcp(state.data.community_mcp);
  const supported = supportsCommunityMcp(capabilities);
  const notice = node("p", communityMcpMessage(!supported ? "enable_mcp" : status?.state)); notice.setAttribute("role", "status");
  card.append(title, notice);
  if (status?.destination) card.append(node("p", `Home Assistant MCP Server${status.version ? ` ${status.version}` : ""} · ${status.destination}`));
  if (state.communityMcpError) { const error = node("p", communityMcpMessage(state.communityMcpError), "desktop-error"); error.setAttribute("role", "alert"); card.append(error); }
  const actions = node("div", "", "desktop-form-actions");
  if (supported && status?.state === "not_connected" && status.destination && status.consent_revision) {
    const detail = node("p", `Connect Codex to ${status.destination} using this App’s existing private connection path. Allowed tools use the community server’s Home Assistant permissions and may control devices or edit configuration. HTTP carries the credential without encryption; the private Bridge registry and its backups retain a copy. New connections allow no tools. This does not enable Assist or MCP for a chat.`);
    detail.id = "community-mcp-consent-detail";
    const label = node("label", "", "mcp-consent");
    const checkbox = doc.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.communityMcpAcknowledged = "";
    checkbox.checked = state.communityMcpAcknowledged === true; checkbox.disabled = Boolean(state.communityMcpBusy);
    checkbox.setAttribute("aria-describedby", detail.id);
    label.append(checkbox, node("span", "I authorise this Home Assistant destination and accept the connection and backup risks.", ""));
    card.append(detail, label);
    const connect = node("button", "Connect installed HA-MCP", "panel-button"); connect.type = "button"; connect.dataset.desktopAction = "community-mcp-connect";
    connect.disabled = Boolean(state.communityMcpBusy) || !checkbox.checked; actions.append(connect);
  }
  if (supported && status?.server_name) {
    const tools = node("button", "Choose allowed tools", "panel-button"); tools.type = "button"; tools.dataset.desktopAction = "edit-mcp-tools"; tools.dataset.id = status.server_name;
    actions.append(tools);
    card.append(node("p", "Manage this connection in the server list below. Existing pause state and tool permissions are preserved. In a regular chat, enable MCP in its conversation settings. Select Assist servers separately if you also want voice access."));
  }
  if (state.communityMcpBusy) actions.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  card.append(actions);
  return card;
}
