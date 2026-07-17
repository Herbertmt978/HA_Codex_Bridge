const DESTINATIONS = Object.freeze([
  { id: "chats", label: "Chats", icon: "chat" },
  { id: "scheduled", label: "Scheduled", icon: "calendar" },
  { id: "skills", label: "Skills", icon: "spark" },
  { id: "plugins", label: "Plugins", icon: "puzzle" },
  { id: "settings", label: "Settings", icon: "settings" },
]);

const asRecord = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

/**
 * Turn the intentionally small native-provider status contract into stable UI
 * copy. Unknown is distinct from unavailable: older Apps may not advertise
 * this capability yet, and should not be presented as a failed account.
 */
export function getNativeToolsViewModel(status = {}, config = {}) {
  const statusRecord = asRecord(status);
  const providerCapabilities = asRecord(statusRecord.provider_capabilities);
  const auth = asRecord(statusRecord.auth);
  const explicitlySignedOut = Object.keys(auth).length > 0
    && (auth.auth_required === true || (typeof auth.state === "string" && auth.state !== "ok"));
  const imageGeneration = providerCapabilities.image_generation === true
    && providerCapabilities.namespace_tools === true
    && !explicitlySignedOut
    ? { label: "Available", state: "available" }
    : providerCapabilities.image_generation === false
      || providerCapabilities.namespace_tools === false
      || explicitlySignedOut
      ? { label: "Unavailable", state: "unavailable" }
      : { label: "Checking", state: "checking" };
  const webSearchMode = asRecord(config).web_search_mode;
  let webSearch = { label: "Checking", state: "checking" };
  if (webSearchMode === "disabled") {
    webSearch = { label: "Off", state: "unavailable" };
  } else if (webSearchMode === "live") {
    webSearch = providerCapabilities.web_search === true && !explicitlySignedOut
      ? { label: "Live", state: "available" }
      : providerCapabilities.web_search === false || explicitlySignedOut
        ? { label: "Unavailable", state: "unavailable" }
        : webSearch;
  }
  return { imageGeneration, webSearch };
}

/** Convert the bridge's list responses (array, {items}, or {data}) to a safe array. */
export function normalizeDesktopList(value) {
  if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
  const record = asRecord(value);
  for (const key of ["items", "data", "results", "automations", "skills", "plugins", "marketplaces", "servers", "runs"]) {
    if (Array.isArray(record[key])) return record[key].filter((item) => item && typeof item === "object");
  }
  return [];
}

export function normalizeSkillsResponse(value) {
  const record = asRecord(value);
  const entries = Array.isArray(record.data) ? record.data : normalizeDesktopList(value);
  return entries.flatMap((entry) => Array.isArray(entry?.skills) ? entry.skills.map((skill) => ({ ...skill, scope: skill.scope || entry.cwd })) : []).filter((skill) => skill && typeof skill === "object");
}

export function normalizePluginsResponse(value) {
  const record = asRecord(value);
  const marketplaces = Array.isArray(record.marketplaces) ? record.marketplaces : Array.isArray(record.data) ? record.data : normalizeDesktopList(value);
  return marketplaces.flatMap((marketplace) => (Array.isArray(marketplace?.plugins) ? marketplace.plugins : []).map((plugin) => ({ ...plugin, marketplace_name: plugin.marketplace_name || marketplace.name }))).filter((plugin) => plugin && typeof plugin === "object");
}

export function normalizeMarketplacesResponse(value) {
  const record = asRecord(value);
  const marketplaces = Array.isArray(record.marketplaces) ? record.marketplaces : Array.isArray(record.data) ? record.data : normalizeDesktopList(value);
  return marketplaces.filter((marketplace) => marketplace && typeof marketplace === "object").map((marketplace) => ({ name: marketplace.name, plugins: Array.isArray(marketplace.plugins) ? marketplace.plugins : [] }));
}

export function buildAutomationPayload(values = {}) {
  const target = values.thread_id ? { kind: "continue_thread", thread_id: values.thread_id } : { kind: "standalone", project_id: values.project_id };
  const kind = values.schedule_type || "once";
  const schedule = kind === "interval"
    ? { kind, seconds: Number(values.interval_seconds), anchor_at: values.anchor_at || values.run_at }
    : kind === "RRULE" || kind === "rrule"
      ? { kind: "rrule", rule: values.rrule, start_at: values.start_at || values.run_at, timezone: values.timezone }
      : { kind: "once", at: values.run_at };
  return { name: values.name || values.title || "Untitled automation", prompt: values.prompt || "", target, schedule, mode: values.mode || "observe", model: values.model || null, thinking: values.thinking || values.reasoning || null };
}

export function buildAutomationUpdatePayload(values = {}) {
  return { expected_revision: Number(values.revision), ...buildAutomationPayload(values) };
}

export function normalizeDesktopError(error) {
  const record = asRecord(error);
  const candidate = record.body?.message || record.message || record.error || record.detail || error;
  const withoutControlCharacters = Array.from(String(candidate || ""), (character) => {
    const code = character.codePointAt(0);
    return code <= 8 || code === 11 || code === 12 || (code >= 14 && code <= 31) || code === 127
      ? " "
      : character;
  }).join("");
  const safe = withoutControlCharacters
    .replace(/https?:\/\/[^\s<>"']+/giu, "[private address]")
    .replace(/(?:[A-Za-z]:\\|\\\\)[^\s<>"']+/gu, "[private path]")
    .replace(/\/(?:data|config|share|addon_configs|home|root|Users)(?:\/[^\s<>"']*)?/gu, "[private path]")
    .replace(/(^|[\s([{:])\/(?!\/)[^\s<>"']+/gu, "$1[private path]")
    .replace(/\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/-]+=*/giu, "[private credential]")
    .replace(/\b(token|api[_ -]?key|password|secret)\s*[:=]\s*[^\s,;]+/giu, "$1=[private credential]")
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/giu, "[private account]")
    .replace(/\s+/gu, " ")
    .trim();
  return (safe || "Unable to load this surface.").slice(0, 500);
}

export function createDesktopFeatureState() {
  return {
    loading: false,
    error: "",
    data: {},
    form: null,
    notice: "",
    // The desktop surface is rebuilt during status refreshes, so retain the
    // active form's unsaved values independently of its DOM controls.
    formDraft: {},
    // Keep unsaved instruction edits isolated by scope while the selector changes.
    agentsDrafts: {},
  };
}

const formValue = (state, name, fallback = "") => {
  const drafts = asRecord(state.formDraft);
  return Object.hasOwn(drafts, name) ? drafts[name] : fallback;
};

const text = (documentRef, tag, value, className = "") => {
  const node = documentRef.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "" : String(value);
  return node;
};

const button = (documentRef, label, action, extra = {}) => {
  const node = documentRef.createElement("button");
  node.type = "button";
  node.textContent = label;
  node.dataset.desktopAction = action;
  for (const [key, value] of Object.entries(extra)) node.dataset[key] = String(value);
  return node;
};

const input = (documentRef, label, name, value = "", type = "text") => {
  const wrap = documentRef.createElement("label");
  wrap.className = "desktop-field";
  wrap.append(text(documentRef, "span", label, "desktop-field-label"));
  const control = documentRef.createElement(type === "textarea" ? "textarea" : "input");
  control.name = name;
  control.value = value == null ? "" : String(value);
  control.dataset.desktopField = name;
  if (type !== "textarea") control.type = type;
  if (type === "textarea") control.rows = 4;
  wrap.append(control);
  return wrap;
};

const selectField = (documentRef, label, name, options, value = "") => {
  const wrap = documentRef.createElement("label"); wrap.className = "desktop-field";
  wrap.append(text(documentRef, "span", label, "desktop-field-label"));
  const control = documentRef.createElement("select"); control.name = name; control.dataset.desktopField = name;
  for (const option of options) { const node = documentRef.createElement("option"); node.value = option.value; node.textContent = option.label; node.selected = option.value === value; node.disabled = Boolean(option.disabled); control.append(node); }
  wrap.append(control); return wrap;
};

function displayValue(value, key = "") {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "—";
  if (typeof value === "object") {
    if (key === "schedule") {
      const kind = value.kind === "rrule" ? "RRULE" : value.kind || "once";
      const when = value.at || value.start_at || value.anchor_at;
      if (kind === "interval" && value.seconds) return `Every ${value.seconds}s${when ? ` · from ${when}` : ""}`;
      if (kind === "RRULE" && value.rule) return value.rule;
      return when ? `${kind} · ${when}` : kind;
    }
    if (Array.isArray(value)) return value.map((item) => displayValue(item)).join(", ");
    return value.name || value.label || value.status || "—";
  }
  return String(value);
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (["ready", "enabled", "connected", "completed", "success", "idle"].includes(normalized)) return "is-positive";
  if (["failed", "error", "unsupported", "disabled"].includes(normalized)) return "is-negative";
  if (["starting", "running", "paused", "oauth_required", "pending"].includes(normalized)) return "is-attention";
  return "";
}

function renderEmpty(documentRef, message) {
  const empty = text(documentRef, "p", message, "desktop-empty");
  empty.setAttribute("role", "status");
  return empty;
}

function renderTable(documentRef, rows, columns, actions = null) {
  if (!rows.length) return renderEmpty(documentRef, "Nothing here yet.");
  const table = documentRef.createElement("table");
  table.className = "desktop-table";
  table.append(text(documentRef, "caption", `${columns.map(([, label]) => label).join(", ")} list`, "sr-only"));
  const head = documentRef.createElement("thead");
  const headRow = documentRef.createElement("tr");
  for (const [, label] of columns) headRow.append(text(documentRef, "th", label));
  if (actions) headRow.append(text(documentRef, "th", "Actions"));
  head.append(headRow);
  table.append(head);
  const body = documentRef.createElement("tbody");
  for (const row of rows) {
    const tr = documentRef.createElement("tr");
    for (const [key, label] of columns) {
      const td = text(documentRef, "td", displayValue(row[key], key));
      td.dataset.label = label;
      const tone = statusClass(row[key]);
      if (tone) td.classList.add(tone);
      tr.append(td);
    }
    if (actions) {
      const td = documentRef.createElement("td");
      td.dataset.label = "Actions";
      td.className = "desktop-table-actions";
      actions(row, td);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  return table;
}

function renderScheduled(documentRef, state, defaultTimezone = "UTC") {
  const section = documentRef.createElement("div");
  section.className = "desktop-feature-content";
  const toolbar = documentRef.createElement("div");
  toolbar.className = "desktop-toolbar";
  toolbar.append(text(documentRef, "div", "Automations", "desktop-section-label"), button(documentRef, "New schedule", "open-schedule-form"));
  section.append(toolbar);
  if (state.form === "schedule" || state.form === "schedule-edit") {
    const editing = state.editingAutomation || {};
    const schedule = editing.schedule || {};
    const form = documentRef.createElement("form");
    form.className = "desktop-form";
    form.dataset.desktopForm = "schedule";
    form.append(text(documentRef, "p", state.form === "schedule-edit" ? "Update the automation and keep its revision current." : "Create a bounded task that runs in this workspace.", "desktop-form-intro"));
    form.append(input(documentRef, "Title", "title", formValue(state, "title", editing.name || "")));
    form.append(input(documentRef, "Project ID", "project_id", formValue(state, "project_id", editing.target?.project_id || "")));
    form.append(input(documentRef, "Thread ID", "thread_id", formValue(state, "thread_id", editing.target?.thread_id || "")));
    form.append(input(documentRef, "Prompt", "prompt", formValue(state, "prompt", editing.prompt || ""), "textarea"));
    form.append(selectField(documentRef, "Schedule", "schedule_type", [{ value: "once", label: "One time" }, { value: "interval", label: "Interval" }, { value: "rrule", label: "RRULE" }], formValue(state, "schedule_type", schedule.kind === "rrule" ? "rrule" : schedule.kind || "once")));
    form.append(input(documentRef, "Run at (ISO)", "run_at", formValue(state, "run_at", schedule.at || schedule.start_at || schedule.anchor_at || "")));
    form.append(input(documentRef, "Interval seconds", "interval_seconds", formValue(state, "interval_seconds", schedule.seconds || "")));
    form.append(input(documentRef, "RRULE", "rrule", formValue(state, "rrule", schedule.rule || "")));
    form.append(input(documentRef, "Home Assistant timezone", "timezone", formValue(state, "timezone", schedule.timezone || defaultTimezone)));
    form.append(input(documentRef, "Model", "model", formValue(state, "model", editing.model || "")));
    form.append(input(documentRef, "Reasoning", "thinking", formValue(state, "thinking", editing.thinking || "")));
    form.append(selectField(documentRef, "Mode", "mode", [{ value: "observe", label: "Observe" }, { value: "edit", label: "Edit" }, { value: "full-auto", label: "Full auto" }], formValue(state, "mode", editing.mode || "observe")));
    form.append(input(documentRef, "Revision", "revision", formValue(state, "revision", editing.revision || "")));
    const actions = documentRef.createElement("div");
    actions.className = "desktop-form-actions";
    actions.append(button(documentRef, state.form === "schedule-edit" ? "Save schedule" : "Create schedule", state.form === "schedule-edit" ? "submit-schedule-update" : "submit-schedule"), button(documentRef, "Cancel", "close-form"));
    form.append(actions);
    section.append(form);
  }
  const rows = normalizeDesktopList(state.data.automations || state.data);
  section.append(renderTable(documentRef, rows, [["title", "Title"], ["schedule", "Schedule"], ["status", "Status"]], (row, td) => {
    const id = row.id || row.automation_id || "";
    const common = { id, revision: row.revision || "0" };
    td.append(button(documentRef, "Run", "run-automation", common), button(documentRef, row.enabled === false ? "Resume" : "Pause", row.enabled === false ? "resume-automation" : "pause-automation", common), button(documentRef, "Runs", "list-automation-runs", common), button(documentRef, "Update", "update-automation", common), button(documentRef, "Delete", "delete-automation", common));
  }));
  const runs = normalizeDesktopList(state.data.runs);
  if (runs.length) {
    section.append(text(documentRef, "h3", "Run history", "desktop-subheading"));
    section.append(renderTable(documentRef, runs, [["status", "Status"], ["due_at", "Due"], ["started_at", "Started"], ["completed_at", "Completed"]]));
  }
  return section;
}

function renderSkills(documentRef, state) {
  const section = documentRef.createElement("div");
  section.className = "desktop-feature-content";
  const toolbar = documentRef.createElement("div");
  toolbar.className = "desktop-toolbar";
  toolbar.append(text(documentRef, "div", "Workspace capabilities", "desktop-section-label"), button(documentRef, "Create skill", "open-skill-form"));
  section.append(toolbar);
  if (state.form === "skill") {
    const form = documentRef.createElement("form");
    form.className = "desktop-form";
    form.dataset.desktopForm = "skill";
    form.append(input(documentRef, "Name", "name", formValue(state, "name")), input(documentRef, "Description", "description", formValue(state, "description"), "textarea"), input(documentRef, "Instructions", "instructions", formValue(state, "instructions"), "textarea"));
    const actions = documentRef.createElement("div"); actions.className = "desktop-form-actions";
    actions.append(button(documentRef, "Create skill", "submit-skill"), button(documentRef, "Cancel", "close-form")); form.append(actions); section.append(form);
  }
  const rows = normalizeDesktopList(state.data.skills || state.data);
  section.append(renderTable(documentRef, rows, [["name", "Skill"], ["scope", "Scope"], ["enabled", "Enabled"]], (row, td) => {
    const id = row.id || row.skill_id || row.name || "";
    td.append(button(documentRef, row.enabled === false ? "Enable" : "Disable", "toggle-skill", { id, enabled: row.enabled === false ? "true" : "false" }));
    td.append(button(documentRef, "Delete", "delete-skill", { id }));
  }));
  return section;
}

function renderPlugins(documentRef, state) {
  const section = documentRef.createElement("div");
  section.className = "desktop-feature-content";
  const toolbar = documentRef.createElement("div");
  toolbar.className = "desktop-toolbar";
  toolbar.append(text(documentRef, "div", "Plugins", "desktop-section-label"));
  section.append(toolbar);
  const rows = normalizeDesktopList(state.data.plugins || state.data);
  section.append(renderTable(documentRef, rows, [["name", "Plugin"], ["version", "Version"], ["enabled", "State"]], (row, td) => {
    const id = row.id || row.plugin_id || row.name || "";
    td.append(button(documentRef, row.installed || row.enabled ? "Uninstall" : "Install", row.installed || row.enabled ? "uninstall-plugin" : "install-plugin", { id, name: row.name || id, marketplace: row.marketplace_name || "" }));
  }));
  const market = normalizeDesktopList(state.data.marketplaces);
  const marketHeading = text(documentRef, "h3", "Trusted marketplaces", "desktop-subheading"); section.append(marketHeading);
  const marketActions = documentRef.createElement("div"); marketActions.className = "desktop-form-actions";
  marketActions.append(button(documentRef, "Add marketplace", "open-marketplace-form")); section.append(marketActions);
  if (state.form === "marketplace") {
    const form = documentRef.createElement("form"); form.className = "desktop-form"; form.dataset.desktopForm = "marketplace";
    form.append(input(documentRef, "HTTPS source URL", "source", formValue(state, "source"), "url"), input(documentRef, "Ref (optional)", "ref_name", formValue(state, "ref_name")), input(documentRef, "Sparse paths (comma separated)", "sparse_paths", formValue(state, "sparse_paths")));
    const actions = documentRef.createElement("div"); actions.className = "desktop-form-actions"; actions.append(button(documentRef, "Add marketplace", "submit-marketplace"), button(documentRef, "Cancel", "close-form")); form.append(actions); section.append(form);
  }
  const marketRows = market.map((row) => ({ ...row, plugin_count: Array.isArray(row.plugins) ? row.plugins.length : 0 }));
  section.append(renderTable(documentRef, marketRows, [["name", "Name"], ["plugin_count", "Plugins"]], (row, td) => td.append(button(documentRef, "Remove", "remove-marketplace", { id: row.name || "" }), button(documentRef, "Upgrade", "upgrade-marketplace", { id: row.name || "" }))));
  return section;
}

function renderSettings(documentRef, state, hasActiveProject = false, activeProjectId = null, status = {}, config = {}) {
  const section = documentRef.createElement("div"); section.className = "desktop-feature-content settings-content";
  const tabs = documentRef.createElement("nav"); tabs.className = "settings-tabs"; tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Settings sections");
  const tabItems = [["general", "General"], ["mcp", "MCP servers"], ["instructions", "Instructions"], ["shortcuts", "Keyboard shortcuts"], ["about", "About / security"]];
  const tab = state.settingsTab || "general";
  for (const [id, label] of tabItems) { const control = button(documentRef, label, "select-settings-tab", { tab: id }); control.className = "settings-tab"; control.id = `settings-tab-${id}`; control.dataset.settingsTab = id; control.setAttribute("role", "tab"); control.setAttribute("aria-controls", "settings-panel"); control.setAttribute("aria-selected", String(tab === id)); control.tabIndex = tab === id ? 0 : -1; tabs.append(control); }
  section.append(tabs);
  const panel = documentRef.createElement("section"); panel.id = "settings-panel"; panel.className = "settings-panel"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `settings-tab-${tab}`); section.append(panel);
  const mcp = normalizeDesktopList(state.data.mcp_servers || state.data.servers);
  if (tab === "mcp") {
    panel.append(text(documentRef, "h3", "MCP servers", "desktop-subheading"), text(documentRef, "p", "Connect trusted HTTPS tools. OAuth opens once in a new tab and is never stored by the panel.", "desktop-note"), button(documentRef, "Add MCP server", "open-mcp-form"));
    if (state.form === "mcp") { const form = documentRef.createElement("form"); form.className = "desktop-form"; form.dataset.desktopForm = "mcp"; form.append(input(documentRef, "Name", "name", formValue(state, "name")), input(documentRef, "HTTPS URL", "url", formValue(state, "url"), "url"), input(documentRef, "OAuth client ID (public)", "oauth_client_id", formValue(state, "oauth_client_id")), input(documentRef, "OAuth resource", "oauth_resource", formValue(state, "oauth_resource"))); const actions = documentRef.createElement("div"); actions.className = "desktop-form-actions"; actions.append(button(documentRef, "Add server", "submit-mcp"), button(documentRef, "Cancel", "close-form")); form.append(actions); panel.append(form); }
    panel.append(renderTable(documentRef, mcp, [["name", "Name"], ["endpoint", "Endpoint"], ["startup", "Startup"], ["auth", "Auth"]], (row, td) => { const id = row.name || ""; const oauth = row.auth === "oauth_required" || row.auth === "oauth"; td.append(button(documentRef, "Remove", "remove-mcp", { id })); if (oauth) td.append(button(documentRef, "Sign in", "login-mcp", { id })); else td.append(text(documentRef, "span", "No OAuth", "desktop-action-note")); }));
  }
  if (tab === "instructions") {
    panel.append(text(documentRef, "h3", "AGENTS.md instructions", "desktop-subheading"), text(documentRef, "p", "Keep global defaults separate from the current project. The selected scope is saved through Home Assistant.", "desktop-note"));
    const selectedScope = hasActiveProject && state.agentsScope !== "global" ? "project" : "global";
    const scope = selectField(documentRef, "Instruction scope", "agents_scope", [{ value: "global", label: "Global instructions" }, { value: "project", label: hasActiveProject ? "Current project" : "Current project (select a project first)", disabled: !hasActiveProject }], selectedScope);
    const scopeControl = scope.querySelector('[data-desktop-field="agents_scope"]');
    if (scopeControl) scopeControl.dataset.agentsProjectId = activeProjectId || "";
    panel.append(scope);
    const records = asRecord(state.data.agentsScopes); const agents = asRecord(records[selectedScope] || state.data.agents); const draftKey = selectedScope === "project" ? `project:${activeProjectId || state.agentsProjectId || ""}` : "global"; const drafts = asRecord(state.agentsDrafts); const content = Object.hasOwn(drafts, draftKey) ? drafts[draftKey] : agents.content || "";
    const contentField = input(documentRef, `${selectedScope === "global" ? "Global" : "Project"} AGENTS.md`, "agents_content", content, "textarea");
    const contentControl = contentField.querySelector('[data-desktop-field="agents_content"]');
    if (contentControl) contentControl.dataset.agentsProjectId = activeProjectId || "";
    panel.append(contentField);
    const actions = documentRef.createElement("div"); actions.className = "desktop-form-actions"; actions.append(button(documentRef, "Save instructions", "save-agents"), button(documentRef, "Delete instructions", "delete-agents")); panel.append(actions);
  }
  if (tab === "shortcuts") panel.append(text(documentRef, "h3", "Keyboard shortcuts", "desktop-subheading"), text(documentRef, "p", "⌘/Ctrl+N new chat · ⌘/Ctrl+G search · ⌘/Ctrl+F find · ⌘/Ctrl+Shift+[ or ] switch chats · Ctrl+Shift+D toggle drawer · ⌘/Ctrl+, settings · Esc closes menus", "desktop-note"));
  if (tab === "about") panel.append(text(documentRef, "h3", "About / security", "desktop-subheading"), text(documentRef, "p", "Credentials stay in Home Assistant. Remote values are rendered as plain text and external OAuth links are restricted to HTTPS.", "desktop-note"));
  if (tab === "general") {
    const nativeTools = getNativeToolsViewModel(status, config);
    const rows = documentRef.createElement("dl");
    rows.className = "native-tools-list";
    const addRow = (label, value, stateName) => {
      const row = documentRef.createElement("div");
      row.className = "native-tool-row";
      row.append(text(documentRef, "dt", label), text(documentRef, "dd", value, `native-tool-state ${stateName}`));
      rows.append(row);
    };
    addRow("Web search", nativeTools.webSearch.label, nativeTools.webSearch.state);
    addRow("Image generation", nativeTools.imageGeneration.label, nativeTools.imageGeneration.state);
    panel.append(
      text(documentRef, "h3", "General", "desktop-subheading"),
      text(documentRef, "p", "Use the sidebar to move between chats, scheduled tasks, skills, plugins, and settings. Chat-only controls stay hidden on feature surfaces.", "desktop-note"),
      text(documentRef, "h3", "Native tools", "desktop-subheading"),
      rows,
      text(documentRef, "p", "Image generation uses the signed-in ChatGPT account and Codex's native tool. Ask for an image naturally in a chat.", "desktop-note")
    );
  }
  return section;
}

export function renderDesktopFeatureSurface(container, { destination = "scheduled", state = createDesktopFeatureState(), onAction, timezone = "UTC", hasActiveProject = false, activeProjectId = null, status = {}, config = {} } = {}) {
  if (!container) return;
  const documentRef = container.ownerDocument || globalThis.document;
  container.replaceChildren();
  container.onclick = (event) => {
    const target = event.target.closest?.("[data-desktop-action]");
    if (target) onAction?.(target.dataset.desktopAction, target.dataset, target);
  };
  container.onsubmit = (event) => {
    event.preventDefault();
    const form = event.target?.closest?.("[data-desktop-form]");
    const submit = form?.querySelector('[data-desktop-action^="submit-"]');
    if (submit) onAction?.(submit.dataset.desktopAction, submit.dataset, submit);
  };
  const heading = documentRef.createElement("div"); heading.className = "desktop-feature-header";
  const destinationMeta = DESTINATIONS.find((item) => item.id === destination) || DESTINATIONS[1];
  heading.append(text(documentRef, "div", destinationMeta.label, "desktop-feature-title"));
  heading.append(text(documentRef, "p", destination === "scheduled" ? "Manage automations and run history." : destination === "skills" ? "Enable skills by scope and create bounded instructions." : destination === "plugins" ? "Install plugins and maintain trusted marketplaces." : "Connection, instructions, and security preferences.", "desktop-feature-summary"));
  container.append(heading);
  if (state.loading) { container.setAttribute("aria-busy", "true"); container.append(renderEmpty(documentRef, "Loading…")); return; }
  container.setAttribute("aria-busy", "false");
  if (state.error) { const error = text(documentRef, "p", state.error, "desktop-error"); error.setAttribute("role", "alert"); container.append(error); container.append(button(documentRef, "Retry", "retry-desktop")); return; }
  if (state.notice) { const notice = text(documentRef, "p", state.notice, "desktop-notice"); notice.setAttribute("role", "status"); container.append(notice); }
  if (state.confirmAction) { const confirm = documentRef.createElement("div"); confirm.className = "desktop-notice"; confirm.setAttribute("role", "alert"); confirm.append(text(documentRef, "span", "This action is destructive. Confirm to continue."), button(documentRef, "Confirm", "confirm-desktop"), button(documentRef, "Cancel", "cancel-desktop-confirm")); container.append(confirm); }
  const content = destination === "scheduled" ? renderScheduled(documentRef, state, timezone) : destination === "skills" ? renderSkills(documentRef, state) : destination === "plugins" ? renderPlugins(documentRef, state) : renderSettings(documentRef, state, hasActiveProject, activeProjectId, status, config);
  container.append(content);
}

export { DESTINATIONS };
