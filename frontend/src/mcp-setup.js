export const HA_MCP_GUIDE = "https://github.com/Herbertmt978/HA_Codex_Bridge/blob/main/docs/home-assistant-mcp.md";

const text = (doc, tag, value, className = "") => {
  const node = doc.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};
const button = (doc, label, action) => {
  const node = text(doc, "button", label, "panel-button");
  node.type = "button";
  node.dataset.desktopAction = action;
  return node;
};
const link = (doc, label, href) => {
  const node = text(doc, "a", label);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
};

/** Guided HA setup and the existing custom-server form share the same MCP API. */
export function renderMcpSetup(doc, state, enabled) {
  const section = text(doc, "section", "", "mcp-setup");
  if (state.form === "mcp-choice") {
    section.append(text(doc, "h3", "Choose an MCP server", "desktop-subheading"));
    const choices = text(doc, "div", "", "mcp-choices");
    for (const [title, description, action] of [
      ["Home Assistant (HA-MCP)", "Guided installation and connection for devices, states and automations.", "choose-ha-mcp"],
      ["Other MCP server", "Add any compatible trusted HTTPS server, with optional OAuth settings.", "choose-custom-mcp"],
    ]) {
      const choice = button(doc, "", action);
      choice.className = "mcp-choice";
      choice.append(text(doc, "strong", title), text(doc, "span", description));
      choices.append(choice);
    }
    section.append(choices, button(doc, "Cancel", "close-form"));
    return section;
  }
  const guided = state.form === "mcp-ha";
  if (guided) {
    section.append(text(doc, "h3", "Connect Home Assistant with HA-MCP", "desktop-subheading"));
    const steps = text(doc, "ol", "", "mcp-steps");
    const install = text(doc, "li", "");
    install.append(text(doc, "strong", "Install HA-MCP once"), text(doc, "p", "In HACS, add homeassistant-ai/ha-mcp-integration as a custom Integration repository, download it and restart Home Assistant. Then go to Settings → Devices & services → Add integration → HA-MCP Custom Component and add HA-MCP Server."), link(doc, "HA-MCP installation instructions", HA_MCP_GUIDE));
    const connect = text(doc, "li", "");
    connect.append(text(doc, "strong", "Copy your connection URL"), text(doc, "p", "Configure the HA-MCP Server entry and copy its HTTPS connection URL through Nabu Casa or your existing reverse proxy. If HA-MCP is already installed as an App or another service, use that server instead."), text(doc, "p", "Choose only the tools you need. Device controls and automation edits can affect your home; optional file and YAML tools need their own review. HA-MCP does not require root host access."));
    const enable = text(doc, "li", "");
    enable.append(text(doc, "strong", "Enable MCP in Codex Bridge"), text(doc, "p", "Go to Settings → Apps → Codex Bridge → Configuration, turn on Enable MCP, save and restart the App. Return here and check the connection options again."));
    steps.append(install, connect, enable);
    section.append(steps);
  }
  section.append(text(doc, "p", enabled ? "MCP is enabled. You can add a server below." : "MCP is not available on this connection. Enable MCP in the Codex Bridge App and restart it. If the option is missing, update both the App and HACS Integration, then restart Home Assistant.", "desktop-note"), button(doc, "Check connection options again", "refresh-settings-capabilities"));
  const form = doc.createElement("form");
  form.className = "desktop-form";
  form.dataset.desktopForm = "mcp";
  const field = (label, name, type = "text") => {
    const wrap = text(doc, "label", "", "desktop-field");
    const control = doc.createElement("input");
    control.type = type;
    control.name = name;
    control.dataset.desktopField = name;
    control.value = state.formDraft?.[name] ?? "";
    control.autocomplete = "off";
    control.spellcheck = false;
    control.required = ["name", "url"].includes(name);
    if (name === "name") {
      control.pattern = "[a-z][a-z0-9_\\-]{0,63}";
      control.title = "Start with a lowercase letter. Use lowercase letters, numbers, hyphens or underscores, up to 64 characters.";
    }
    wrap.append(text(doc, "span", label, "desktop-field-label"), control);
    return wrap;
  };
  form.append(field("Name", "name"), field(guided ? "HA-MCP HTTPS connection URL" : "HTTPS URL", "url", "password"), text(doc, "p", "Keep the full URL private: it may contain a secret. Use a public HTTPS hostname. Local addresses, HTTP, query strings and bearer-token settings are not supported.", "desktop-note"));
  const oauth = text(doc, "details", "", "mcp-oauth");
  oauth.append(text(doc, "summary", "OAuth settings (optional)"), field("OAuth client ID (public)", "oauth_client_id"), field("OAuth resource", "oauth_resource"));
  form.append(oauth);
  if (state.formError) {
    const error = text(doc, "p", state.formError, "desktop-error");
    error.setAttribute("role", "alert");
    form.append(error);
  }
  const actions = text(doc, "div", "", "desktop-form-actions");
  const add = button(doc, "Add server", "submit-mcp");
  add.classList.add("panel-button-primary");
  add.disabled = !enabled;
  actions.append(add, button(doc, "Cancel", "close-form"));
  form.append(actions);
  section.append(form);
  if (guided) section.append(text(doc, "h3", "Check it works", "desktop-subheading"), text(doc, "p", "After adding the server, use Sign in if it asks for OAuth. Refresh the server status, then start a new chat and ask Codex to describe an entity without changing it. Confirm the result before allowing changes.", "desktop-note"));
  return section;
}
