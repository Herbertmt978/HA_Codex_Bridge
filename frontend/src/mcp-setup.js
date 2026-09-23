import { selection } from "./selection.js";

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
export function renderMcpSetup(doc, state, enabled, localEnabled = false, credentialsEnabled = false) {
  const section = text(doc, "section", "", "mcp-setup");
  if (state.form === "mcp-choice") {
    section.append(text(doc, "h3", "Choose an MCP server", "desktop-subheading"));
    const choices = text(doc, "div", "", "mcp-choices");
    for (const [title, description, action] of [
      ["Home Assistant (HA-MCP)", "Guided installation and connection for devices, states and automations.", "choose-ha-mcp"],
      ["Other MCP server", "Connect a compatible public HTTPS server or an explicitly enabled local server.", "choose-custom-mcp"],
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
    connect.append(text(doc, "strong", "Copy your connection URL"), text(doc, "p", "Configure the HA-MCP Server entry and copy its connection URL. Use public HTTPS through Nabu Casa or your existing reverse proxy, or enable local MCP below to connect directly over your home network. If HA-MCP is already installed as an App or another service, use that server instead."), text(doc, "p", "Choose only the tools you need. Device controls and automation edits can affect your home; optional file and YAML tools need their own review. HA-MCP does not require root host access."));
    const enable = text(doc, "li", "");
    enable.append(text(doc, "strong", "Enable MCP in Codex Bridge"), text(doc, "p", "Go to Settings → Apps → Codex Bridge → Configuration, turn on Enable MCP, save and restart the App. Return here and check the connection options again."));
    steps.append(install, connect, enable);
    section.append(steps);
  }
  section.append(text(doc, "p", enabled ? "MCP is enabled. You can add a server below." : "MCP is not available on this connection. Enable MCP in the Codex Bridge App and restart it. If the option is missing, update both the App and HACS Integration, then restart Home Assistant.", "desktop-note"), button(doc, "Check connection options again", "refresh-settings-capabilities"));
  const form = doc.createElement("form");
  form.className = "desktop-form";
  form.dataset.desktopForm = "mcp";
  const local = localEnabled && state.formDraft?.local === true;
  const staticAuth = credentialsEnabled && ["bearer", "headers"].includes(state.formDraft?.auth_mode);
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
  const checkbox = (label, name, required = false) => {
    const wrap = text(doc, "label", "", "mcp-consent");
    const control = doc.createElement("input");
    control.type = "checkbox";
    control.dataset.desktopField = name;
    control.name = name;
    control.checked = state.formDraft?.[name] === true;
    control.required = required;
    wrap.append(control, text(doc, "span", label));
    return wrap;
  };
  form.append(field("Name", "name"));
  if (localEnabled) form.append(checkbox("Connect to a local network or Home Assistant App server", "local"));
  else form.append(text(doc, "p", "For local servers, enable both Enable MCP and Enable local MCP connections in the Codex Bridge App configuration, save and restart. Update the App and Integration if that option is missing.", "desktop-note"));
  form.append(field(guided ? "HA-MCP connection URL" : local ? "Local HTTP or HTTPS URL" : "Public HTTPS URL", "url", "password"), text(doc, "p", local ? "Use the server’s private network address or App hostname, not localhost. HTTPS must have a trusted certificate. If the server’s IP address changes, remove it and add it again." : "Use a public HTTPS hostname. Keep the full URL private: it may contain a secret.", "desktop-note"));
  if (local) {
    const warning = text(doc, "div", "", "mcp-local-warning");
    warning.append(text(doc, "strong", "Allow access to this local MCP server?"), text(doc, "p", "Codex will be able to use the tools this server exposes, including any device controls or file changes it permits. HTTP sends requests, responses and any secret in the URL without encryption across your local network. This does not grant shell or root host access."), checkbox("I trust this server and understand the access and connection risks", "local_acknowledged", true));
    form.append(warning, text(doc, "p", "Local OAuth and query strings are not supported. A server with a private connection path can use that full URL.", "desktop-note"));
  }
  if (credentialsEnabled) form.append(renderMcpAuthentication(doc, state, { local }));
  const oauth = text(doc, "details", "", "mcp-oauth");
  oauth.append(text(doc, "summary", "OAuth settings (optional)"), field("OAuth client ID (public)", "oauth_client_id"), field("OAuth resource", "oauth_resource"));
  if (!local && !staticAuth) form.append(oauth);
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

/** Secrets live only in these inputs; generic form drafts never capture them. */
export function renderMcpAuthentication(doc, state, { local = false, replacing = false } = {}) {
  const section = text(doc, "section", "", "mcp-authentication");
  const mode = state.formDraft?.auth_mode || (replacing ? state.editingMcp?.auth : "none") || "none";
  const options = [["bearer", "Bearer token"], ["headers", "API-key headers"]];
  if (!replacing) options.unshift(["none", local ? "No authentication header" : "None or OAuth"]);
  const choice = selection(doc, { name: "auth_mode", label: "Authentication", value: mode, options });
  choice.querySelector("select").dataset.desktopField = "auth_mode";
  section.append(text(doc, "span", "Authentication", "desktop-field-label"), choice);
  const secretInput = (label, attr, type = "password") => {
    const wrap = text(doc, "label", "", "desktop-field");
    const input = doc.createElement("input");
    input.type = type; input.setAttribute(attr, ""); input.autocomplete = "off";
    input.spellcheck = false; input.required = true; input.maxLength = type === "password" ? 4096 : 64;
    if (type === "password") input.minLength = 8;
    wrap.append(text(doc, "span", label, "desktop-field-label"), input);
    return wrap;
  };
  if (mode === "bearer") section.append(secretInput("Bearer token", "data-mcp-token"));
  if (mode === "headers") {
    const rows = text(doc, "div", "", "mcp-header-rows");
    const add = button(doc, "Add another header", "");
    delete add.dataset.desktopAction;
    const addRow = () => {
      const row = text(doc, "div", "", "mcp-header-row");
      row.append(secretInput("Header name", "data-mcp-header-name", "text"), secretInput("API-key value", "data-mcp-header-value"));
      const remove = button(doc, "Remove header", ""); delete remove.dataset.desktopAction;
      remove.onclick = () => { row.remove(); add.disabled = false; };
      row.append(remove); rows.append(row); add.disabled = rows.children.length >= 8;
    };
    add.onclick = addRow; addRow(); section.append(rows, add);
    section.append(text(doc, "p", "Use the authentication header named by your server, such as X-API-Key. Do not enter Host, Cookie, routing or MCP protocol headers.", "desktop-note"));
  }
  if (["bearer", "headers"].includes(mode)) {
    const warning = text(doc, "div", "", "mcp-local-warning");
    warning.append(text(doc, "strong", "Share this credential with this server?"), text(doc, "p", "Codex can use the tools this credential permits. It is stored privately in the App and included in App backups, without separate encryption. Protect those backups. Saved values cannot be displayed; you can replace or remove them."));
    if (local) warning.append(text(doc, "p", "Local HTTP also sends the credential without encryption. Use HTTPS where possible and trust the network between Home Assistant and the server."));
    const label = text(doc, "label", "", "mcp-consent");
    const check = doc.createElement("input"); check.type = "checkbox"; check.required = true;
    check.dataset.desktopField = "auth_acknowledged"; check.name = "auth_acknowledged";
    check.checked = state.formDraft?.auth_acknowledged === true;
    label.append(check, text(doc, "span", "I trust this destination and understand credential transport and backup exposure"));
    warning.append(label); section.append(warning);
    section.append(text(doc, "p", "Tokens and API-key values must be 8–4,096 characters. Shorter values are unsupported because response redaction could alter ordinary MCP messages. Use a secure connection to Home Assistant when entering credentials. Submitted values are cleared, including if saving fails.", "desktop-note"));
  }
  return section;
}

export function readMcpCredential(form) {
  const mode = form.querySelector('[data-desktop-field="auth_mode"]')?.value;
  if (mode === "bearer") return { mode, token: form.querySelector("[data-mcp-token]")?.value || "" };
  if (mode === "headers") return { mode, headers: [...form.querySelectorAll(".mcp-header-row")].map((row) => ({ name: row.querySelector("[data-mcp-header-name]").value, value: row.querySelector("[data-mcp-header-value]").value })) };
  return null;
}

export function clearMcpSecrets(form) {
  form?.querySelectorAll("[data-mcp-token], [data-mcp-header-value]").forEach((input) => { input.value = ""; });
}

export function renderMcpToolPermissions(doc, state) {
  const inventory = state.mcpToolInventory || {};
  const form = doc.createElement("form");
  form.className = "desktop-form mcp-tool-permissions";
  form.dataset.desktopForm = "mcp-tools";
  form.append(text(doc, "h3", `Tools from ${inventory.server || "server"}`, "desktop-subheading"),
    text(doc, "p", `Connection: ${inventory.endpoint || ""}. Tool descriptions and safety labels are claims made by this server; they are not verified guarantees.`, "desktop-note"));
  if (inventory.mode === "all") form.append(text(doc, "p", "All tools are currently available. Saving a selection will also block any tools this server adds later until you allow them.", "desktop-note"));
  else form.append(text(doc, "p", "Only selected tools are available in chats and scheduled tasks. New or renamed tools stay blocked.", "desktop-note"));
  if (!inventory.catalogue_available) form.append(text(doc, "p", "Tool catalogue unavailable. Refresh server status, or resume a paused connection before changing permissions.", "desktop-error"));
  if (inventory.catalogue_truncated) form.append(text(doc, "p", "This server advertises more tools than can be shown here. Unlisted tools remain blocked by a saved selection.", "desktop-note"));
  if (inventory.stale_tools?.length) form.append(text(doc, "p", inventory.catalogue_truncated
    ? `${inventory.stale_tools.length} previously allowed tool${inventory.stale_tools.length === 1 ? " is" : "s are"} not shown in this limited catalogue. They remain on the saved list until removed.`
    : `${inventory.stale_tools.length} previously allowed tool${inventory.stale_tools.length === 1 ? " is" : "s are"} no longer advertised. They remain on the saved list until removed; a renamed tool needs separate approval.`, "desktop-note"));
  const list = doc.createElement("fieldset");
  list.className = "mcp-tool-list";
  list.append(text(doc, "legend", "Allowed tools"));
  const allowed = new Set(state.mcpToolDraft || inventory.enabled_tools || []);
  for (const tool of inventory.tools || []) {
    const row = doc.createElement("label"); row.className = "mcp-tool-row";
    const checkbox = doc.createElement("input"); checkbox.type = "checkbox";
    checkbox.dataset.mcpTool = tool.name;
    checkbox.checked = state.mcpToolDraft ? allowed.has(tool.name) : inventory.mode === "all" || allowed.has(tool.name);
    row.append(checkbox, text(doc, "strong", tool.name));
    if (tool.description) row.append(text(doc, "span", tool.description, "desktop-note"));
    const hints = [tool.read_only && "Claims read-only", tool.write_possible && "May write", tool.destructive && "Claims destructive", tool.idempotent && "Claims idempotent"].filter(Boolean);
    if (hints.length) row.append(text(doc, "small", hints.join(" · "), "desktop-note"));
    list.append(row);
  }
  for (const name of inventory.stale_tools || []) {
    const row = doc.createElement("label"); row.className = "mcp-tool-row";
    const checkbox = doc.createElement("input"); checkbox.type = "checkbox";
    checkbox.dataset.mcpTool = name; checkbox.checked = allowed.has(name);
    row.append(checkbox, text(doc, "strong", name), text(doc, "small", inventory.catalogue_truncated ? "Not in the displayed catalogue" : "Not in the latest catalogue", "desktop-note"));
    list.append(row);
  }
  form.append(list);
  if (state.formError) { const error = text(doc, "p", state.formError, "desktop-error"); error.setAttribute("role", "alert"); form.append(error); }
  const actions = text(doc, "div", "", "desktop-form-actions");
  const save = button(doc, "Save allowed tools", "submit-mcp-tools");
  save.disabled = !inventory.catalogue_available || state.loading;
  actions.append(save, button(doc, "Cancel", "close-form"));
  form.append(actions);
  return form;
}

export function renderMcpConnectionForm(doc, state) {
  const server = state.editingMcp || {};
  const form = doc.createElement("form");
  form.className = "desktop-form"; form.dataset.desktopForm = "mcp";
  form.append(text(doc, "h3", `Edit ${server.name || "connection"}`, "desktop-subheading"),
    text(doc, "p", "This server stays paused after saving. Resume it when you are ready to let existing chats, new chats and scheduled tasks use its tools.", "desktop-note"));
  const wrap = text(doc, "label", "", "desktop-field");
  const url = doc.createElement("input");
  url.type = "url"; url.name = "url"; url.dataset.desktopField = "url";
  url.required = true; url.maxLength = 2048; url.autocomplete = "off"; url.spellcheck = false;
  url.value = state.formDraft?.url || "";
  wrap.append(text(doc, "span", "New connection URL", "desktop-field-label"), url);
  form.append(wrap, text(doc, "p", `Current destination: ${server.endpoint || server.name}. Saved URLs and credentials are not filled into this form. The connection remains ${server.network === "local" ? "local" : "public HTTPS"}; create a new server to change its network type.`, "desktop-note"));
  const relayed = server.network === "local" || ["bearer", "headers"].includes(server.auth);
  const options = [["", "Choose what to do with authentication"], ["keep", "Keep existing authentication"]];
  if (relayed) options.push(["replace", "Use a new credential"], ["remove", "Remove saved credential"]);
  const action = state.formDraft?.credential_action || "";
  const choice = selection(doc, { name: "credential_action", label: "Authentication when changing destination", value: action, options });
  const control = choice.querySelector("select");
  control.required = true; control.dataset.desktopField = "credential_action";
  form.append(text(doc, "span", "Authentication when changing destination", "desktop-field-label"), choice);
  if (action === "replace") {
    const replacement = { ...state, formDraft: { ...state.formDraft, auth_mode: state.formDraft?.auth_mode || (["bearer", "headers"].includes(server.auth) ? server.auth : "bearer") } };
    form.append(renderMcpAuthentication(doc, replacement, { local: server.network === "local", replacing: true }));
  }
  const warning = text(doc, "div", "", "mcp-local-warning");
  warning.append(text(doc, "strong", "Trust the new destination before saving"), text(doc, "p", "Keeping authentication allows the new destination to receive the saved credential. Native OAuth remains bound to its server URL and may require sign-in again. Removing a saved credential blocks an authenticated relay until you set a new one. HTTP sends local requests and credentials without encryption. Tool access does not grant shell or root host access."));
  const consent = text(doc, "label", "", "mcp-consent");
  const checkbox = doc.createElement("input"); checkbox.type = "checkbox"; checkbox.required = true;
  checkbox.name = "endpoint_acknowledged"; checkbox.dataset.desktopField = "endpoint_acknowledged";
  checkbox.checked = state.formDraft?.endpoint_acknowledged === true;
  consent.append(checkbox, text(doc, "span", "I trust this destination and approve the authentication choice above"));
  warning.append(consent); form.append(warning);
  if (state.formError) {
    const error = text(doc, "p", state.formError, "desktop-error"); error.setAttribute("role", "alert"); form.append(error);
  }
  const actions = text(doc, "div", "", "desktop-form-actions");
  actions.append(button(doc, "Save paused connection", "submit-mcp-connection"), button(doc, "Cancel", "close-form"));
  form.append(actions);
  return form;
}

export function renderMcpCredentialForm(doc, state) {
  const form = doc.createElement("form"); form.className = "desktop-form"; form.dataset.desktopForm = "mcp";
  form.append(text(doc, "h3", `Credential for ${state.editingMcp?.name || "server"}`, "desktop-subheading"), text(doc, "p", `Destination: ${state.editingMcp?.endpoint || ""}. This form changes only its credential. To change the destination, use Edit connection after pausing, or remove and add the server again on older Apps.`, "desktop-note"));
  form.append(renderMcpAuthentication(doc, state, { local: state.editingMcp?.network === "local", replacing: true }));
  if (state.formError) { const error = text(doc, "p", state.formError, "desktop-error"); error.setAttribute("role", "alert"); form.append(error); }
  const actions = text(doc, "div", "", "desktop-form-actions");
  const save = button(doc, "Save credential", "submit-mcp-credential"); save.disabled = state.loading; save.classList.add("panel-button-primary");
  actions.append(save, button(doc, "Cancel", "close-form")); form.append(actions);
  return form;
}
