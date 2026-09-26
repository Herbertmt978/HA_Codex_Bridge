import { renderScheduleForm, scheduleSummary } from "./scheduled-tasks.js";
import { scheduleRunHistory } from "./schedule-run-history.js";
import { selection } from "./selection.js";
import { modelChoices, reasoningChoices } from "./model-choices.js";
import { DEFAULT_PREFERENCES } from "./panel-preferences.js";
import { HOST_MODE, HOST_LABEL } from "./host-access.js";
import { HA_MCP_GUIDE, renderMcpSetup, renderMcpCredentialForm, renderMcpConnectionForm, renderMcpToolPermissions, renderStdioPackages } from "./mcp-setup.js";
export { buildAutomationPayload, buildAutomationUpdatePayload } from "./scheduled-tasks.js";

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
  node.className = "panel-button";
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
  if (["starting", "running", "oauth_required", "pending"].includes(normalized)) return "is-attention";
  return "";
}

function renderEmpty(documentRef, message) {
  const empty = text(documentRef, "p", message, "desktop-empty");
  empty.setAttribute("role", "status");
  return empty;
}

function renderLoading(documentRef, destinationLabel) {
  const loading = documentRef.createElement("div");
  loading.className = "desktop-feature-loading";
  loading.setAttribute("role", "status");
  const spinner = documentRef.createElement("span");
  spinner.className = "desktop-feature-spinner";
  spinner.setAttribute("aria-hidden", "true");
  loading.append(spinner, text(documentRef, "span", `Loading ${destinationLabel.toLowerCase()}…`, "desktop-feature-loading-label"));
  return loading;
}

function renderTable(documentRef, rows, columns, actions = null, cellTone = null) {
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
      const tone = cellTone ? cellTone(row, key) : statusClass(row[key]);
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

function renderScheduled(documentRef, state, timezone, proposalsSupported = false, textEditsSupported = false) {
  const defaultTimezone = timezone || "UTC";
  const section = documentRef.createElement("div");
  section.className = "desktop-feature-content";
  const toolbar = documentRef.createElement("div");
  toolbar.className = "desktop-toolbar";
  toolbar.append(text(documentRef, "div", "Automations", "desktop-section-label"));
  if (proposalsSupported) toolbar.append(button(documentRef, "Describe a task", "open-schedule-description"));
  toolbar.append(button(documentRef, "New schedule", "open-schedule-form"));
  if (!state.form) section.append(toolbar);
  if (state.form === "schedule" || state.form === "schedule-edit") {
    section.append(renderScheduleForm(documentRef, state, defaultTimezone, { ...state.scheduleContext, proposalsSupported }));
    return section;
  }
  if (state.form === "automation-edit-description" && textEditsSupported) {
    const form = documentRef.createElement("form");
    form.className = "schedule-description";
    form.dataset.desktopForm = "automation-edit-description";
    const heading = text(documentRef, "h3", "Describe a change");
    heading.tabIndex = -1;
    form.append(heading);
    form.append(text(documentRef, "p", state.editingAutomation?.name || "Scheduled task", "desktop-note"));
    if (state.automationEditProposal) {
      const [field, value] = Object.entries(state.automationEditProposal)[0];
      form.append(text(documentRef, "h4", field === "name" ? "Title" : "Instructions"));
      form.append(text(documentRef, "p", "Current", "desktop-section-label"));
      const current = text(documentRef, "p", state.editingAutomation?.[field], "schedule-edit-value");
      if (field === "prompt") current.tabIndex = 0;
      form.append(current);
      form.append(text(documentRef, "p", "Proposed", "desktop-section-label"));
      const proposed = text(documentRef, "p", value, "schedule-edit-value");
      if (field === "prompt") proposed.tabIndex = 0;
      form.append(proposed);
    } else {
      form.append(text(documentRef, "p", "Describe a title or instruction change, then review it before saving.", "desktop-note"));
      const description = input(documentRef, "Change request", "edit_description", formValue(state, "edit_description"), "textarea");
      const control = description.querySelector("textarea");
      control.placeholder = "Rename to Morning heating check";
      control.maxLength = 4000;
      control.required = true;
      form.append(description);
      form.append(text(documentRef, "p", "Examples: Rename to Morning heating check; Set instructions to Summarise yesterday's events.", "desktop-note"));
    }
    const error = text(documentRef, "p", state.formError || "", "schedule-error");
    error.setAttribute("role", "alert"); form.append(error);
    const actions = documentRef.createElement("div"); actions.className = "schedule-actions";
    actions.append(button(documentRef, "Cancel", "close-form"));
    if (state.automationEditRefreshRequired) actions.append(button(documentRef, "Refresh task", "refresh-automation-edit", { id: state.editingAutomation?.automation_id }));
    else if (state.automationEditProposal) actions.append(button(documentRef, "Back", "revise-automation-edit"), button(documentRef, "Save changes", "save-automation-edit"));
    else actions.append(button(documentRef, "Review changes", "review-automation-edit"));
    form.append(actions); section.append(form);
    return section;
  }
  if (state.form === "schedule-description") {
    const form = documentRef.createElement("form");
    form.className = "schedule-description";
    form.dataset.desktopForm = "schedule-description";
    form.append(text(documentRef, "h3", "Describe a scheduled task"));
    form.append(text(documentRef, "p", `Include when it should run and what Codex should do. Times use Home Assistant's ${defaultTimezone} time zone. Nothing runs until you review and create the task.`, "desktop-note"));
    const description = input(documentRef, "Task and timing", "description", formValue(state, "description"), "textarea");
    const control = description.querySelector("textarea");
    control.placeholder = "Every weekday at 9 am, summarise yesterday's events";
    control.maxLength = 4000;
    control.required = true;
    form.append(description);
    form.append(text(documentRef, "p", "Examples: Every Monday at 2 pm, check the heating; On 24 September 2026 at 09:00, prepare a report.", "desktop-note"));
    const error = text(documentRef, "p", state.formError || "", "schedule-error"); error.setAttribute("role", "alert"); form.append(error);
    const actions = documentRef.createElement("div"); actions.className = "schedule-actions";
    actions.append(button(documentRef, "Cancel", "close-form"), button(documentRef, "Review timing", "review-schedule-description"));
    form.append(actions); section.append(form);
    return section;
  }
  const rows = normalizeDesktopList(state.data.automations || state.data).map((row) => ({ ...row, permissions: row.mode === HOST_MODE ? HOST_LABEL : row.mode === "full-auto" ? "Full auto · workspace" : row.mode === "edit" ? "Edit workspace" : "Observe", schedule: scheduleSummary(row.schedule, defaultTimezone) }));
  section.append(renderTable(documentRef, rows, [["title", "Title"], ["schedule", "Schedule"], ["permissions", "Permissions"], ["status", "Status"]], (row, td) => {
    const id = row.id || row.automation_id || "";
    const common = { id, revision: row.revision || "0" };
    td.append(button(documentRef, "Run", "run-automation", common), button(documentRef, row.enabled === false ? "Resume" : "Pause", row.enabled === false ? "resume-automation" : "pause-automation", common), button(documentRef, "Runs", "list-automation-runs", common), button(documentRef, "Update", "update-automation", common), button(documentRef, "Delete", "delete-automation", common));
    if (textEditsSupported) td.append(button(documentRef, "Describe change", "describe-automation-edit", common));
  }));
  const runs = normalizeDesktopList(state.data.runs);
  if (runs.length) {
    const history = scheduleRunHistory(runs, timezone);
    section.append(text(documentRef, "h3", "Run history", "desktop-subheading"));
    section.append(text(documentRef, "p", history.timezoneUnavailable
      ? "Times shown in UTC because the Home Assistant time zone is unavailable."
      : `Times shown in Home Assistant's ${history.timezone} time zone.`, "desktop-note"));
    const table = renderTable(documentRef, history.rows, [["status", "Status"], ["explanation", "Details"], ["due_at", "Due"], ["started_at", "Started"], ["completed_at", "Completed"]], null, (row, key) => key === "status" ? row.tone : "");
    table.classList.add("schedule-run-history");
    section.append(table);
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
  const groups = new Map();
  for (const row of rows) {
    const name = String(row.name || "");
    const category = name.includes(":") ? name.slice(0, name.indexOf(":")) : "General";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(row);
  }
  if (!rows.length) section.append(renderEmpty(documentRef, "No skills found in this workspace."));
  for (const [category, skills] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
    const group = documentRef.createElement("section"); group.className = "skill-group";
    const heading = text(documentRef, "h3", category.replace(/[-_]/gu, " ").replace(/^./u, (letter) => letter.toUpperCase()), "skill-group-heading");
    const count = text(documentRef, "span", `${skills.length} ${skills.length === 1 ? "skill" : "skills"}`, "skill-group-count");
    heading.append(count); group.append(heading);
    group.append(renderTable(documentRef, [...skills].sort((a, b) => String(a.name).localeCompare(String(b.name))), [["name", "Skill"], ["scope", "Scope"], ["enabled", "Enabled"]], (row, td) => {
      const id = row.id || row.skill_id || row.name || "";
      td.append(button(documentRef, row.enabled === false ? "Enable" : "Disable", "toggle-skill", { id, enabled: row.enabled === false ? "true" : "false" }));
      td.append(button(documentRef, "Delete", "delete-skill", { id }));
    }));
    section.append(group);
  }
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
  const namedRows = rows.map((row) => {
    const candidate = String(row.display_name || row.name || "").trim();
    return {
      ...row,
      visible_name: candidate && !/^app-/iu.test(candidate) ? candidate : "Unnamed plugin",
    };
  });
  section.append(renderTable(documentRef, namedRows, [["visible_name", "Plugin"], ["version", "Version"], ["enabled", "State"]], (row, td) => {
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

function renderSettings(documentRef, state, hasActiveProject = false, activeProjectId = null, status = {}, config = {}, settings = {}) {
  const section = documentRef.createElement("div"); section.className = "desktop-feature-content settings-content";
  const tabs = documentRef.createElement("nav"); tabs.className = "settings-tabs"; tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Settings sections");
  const tabItems = [["general", "General"], ["access", "Access"], ["appearance", "Appearance"], ["mcp", "MCP servers"], ["instructions", "Instructions"], ["shortcuts", "Keyboard shortcuts"], ["about", "About / security"]];
  const tab = state.settingsTab || "general";
  for (const [id, label] of tabItems) { const control = button(documentRef, label, "select-settings-tab", { tab: id }); control.className = "settings-tab"; control.id = `settings-tab-${id}`; control.dataset.settingsTab = id; control.setAttribute("role", "tab"); control.setAttribute("aria-controls", "settings-panel"); control.setAttribute("aria-selected", String(tab === id)); control.tabIndex = tab === id ? 0 : -1; tabs.append(control); }
  section.append(tabs);
  const panel = documentRef.createElement("section"); panel.id = "settings-panel"; panel.className = "settings-panel"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `settings-tab-${tab}`); section.append(panel);
  const mcp = normalizeDesktopList(state.data.mcp_servers || state.data.servers);
  const preferences = { ...DEFAULT_PREFERENCES, ...settings.preferences };
  const saved = text(documentRef, "p", "", "preference-save-status"); saved.setAttribute("role", "status");
  const pickers = {};
  const addPreference = (card, key, label, options) => {
    const row = documentRef.createElement("div"); row.className = "schedule-row";
    const picker = selection(documentRef, { name: key, label, value: preferences[key], options });
    picker.dataset.preference = key; pickers[key] = picker;
    row.append(text(documentRef, "span", label, "schedule-row-label"), picker); card.append(row);
    picker.querySelector("select").addEventListener("change", () => {
      preferences[key] = picker.querySelector("select").value;
      if (key === "model") {
        const choices = reasoningChoices(settings, preferences.model);
        if (!choices.some(([level]) => level === preferences.thinking)) preferences.thinking = "";
        pickers.thinking.setOptions(choices, preferences.thinking);
      }
      try {
        settings.onPreferenceChange?.(preferences);
        saved.textContent = "Saved for this Home Assistant user in this browser.";
      } catch { saved.textContent = "Applied for this visit. Browser storage is unavailable, so these preferences could not be saved."; }
    });
  };
  if (tab === "appearance") {
    panel.append(text(documentRef, "h3", "Appearance", "desktop-subheading"));
    const card = documentRef.createElement("div"); card.className = "schedule-card settings-card";
    addPreference(card, "theme", "Theme", [["ha", "Follow Home Assistant"], ["light", "Light"], ["dark", "Dark"]]);
    addPreference(card, "textSize", "Chat text size", [["default", "Default"], ["large", "Large"], ["larger", "Larger"]]);
    addPreference(card, "motion", "Motion", [["system", "Follow device preference"], ["reduced", "Reduce motion"]]);
    panel.append(card, text(documentRef, "p", "Appearance applies to this panel. Your Home Assistant theme stays unchanged.", "desktop-note"), saved);
  }
  if (tab === "mcp") {
    const recommendation = documentRef.createElement("section");
    recommendation.className = "desktop-note";
    recommendation.append(text(documentRef, "h3", "Home Assistant control", "desktop-subheading"), text(documentRef, "p", "HA-MCP is a recommended optional server for Home Assistant devices and automations. It does not require root host access. Enable MCP in the Bridge App, then follow the connection guide."));
    const guide = text(documentRef, "a", "HA-MCP installation and Bridge connection guide");
    guide.href = HA_MCP_GUIDE;
    guide.target = "_blank"; guide.rel = "noopener noreferrer"; guide.style.color = "inherit";
    recommendation.append(guide); panel.append(recommendation);
    panel.append(text(documentRef, "h3", "MCP servers", "desktop-subheading"), text(documentRef, "p", "Connect public HTTPS servers with optional OAuth, or explicitly enable local network connections. OAuth opens once in a new tab and is never stored by the panel.", "desktop-note"), button(documentRef, "Add MCP server", "open-mcp-form"));
    const credentials = config?.capabilities?.includes("mcp_credentials_v1");
    const management = config?.capabilities?.includes("mcp_management_v1");
    const toolPermissions = config?.capabilities?.includes("mcp_tool_permissions_v1");
    const stdio = config?.capabilities?.includes("mcp_stdio_v1")
      && config?.capabilities?.includes("mcp_admin_v1") && management && toolPermissions;
    if (["mcp-choice", "mcp", "mcp-ha"].includes(state.form)) panel.append(renderMcpSetup(documentRef, state, config?.capabilities?.includes("mcp_admin_v1"), config?.capabilities?.includes("mcp_local_v1"), credentials));
    if (state.form === "mcp-credential" && credentials) panel.append(renderMcpCredentialForm(documentRef, state));
    if (state.form === "mcp-edit" && management) panel.append(renderMcpConnectionForm(documentRef, state));
    if (state.form === "mcp-tools" && toolPermissions) panel.append(renderMcpToolPermissions(documentRef, state));
    if (stdio || mcp.some((row) => row.transport === "stdio")) panel.append(renderStdioPackages(documentRef, state, { available: stdio, management, toolPermissions }));
    if (!stdio) panel.append(text(documentRef, "p", "Isolated local packages require a newer App with its separate stdio option enabled. Update the App and Integration, then refresh connection options.", "desktop-note"));
    panel.append(text(documentRef, "p", management
      ? "Pause a server to block its tools in all chats and scheduled tasks. Saved settings stay in the App. Pause before editing its destination; changes wait until current work finishes. Resume applies to subsequent turns in existing and new chats."
      : "To edit or pause connections, update both the Codex Bridge App and HACS Integration, restart Home Assistant, then refresh server status. Existing connection controls remain available.", "desktop-note"));
    panel.append(button(documentRef, "Refresh server status", "refresh-settings-capabilities"));
    panel.append(renderTable(documentRef, mcp.filter((row) => row.transport !== "stdio"), [["name", "Name"], ["endpoint", "Endpoint"], ["startup", "Startup"], ["auth", "Auth"]], (row, td) => {
      const controls = text(documentRef, "div", "", "mcp-connection-actions");
      td.append(controls);
      const id = row.name || ""; const oauth = row.auth === "oauth_required" || row.auth === "oauth";
      if (management) {
        const paused = row.enabled === false;
        controls.append(button(documentRef, paused ? "Resume" : "Pause", paused ? "resume-mcp" : "pause-mcp", { id }));
        const edit = button(documentRef, "Edit connection", "edit-mcp-connection", { id });
        edit.disabled = !paused; edit.title = paused ? "Edit the paused connection" : "Pause this server before editing";
        controls.append(edit, text(documentRef, "span", row.status_unavailable ? "Status unavailable · refresh to retry" : `${Number.isSafeInteger(row.tool_count) ? row.tool_count : 0} tools · ${Number.isSafeInteger(row.resource_count) ? row.resource_count : 0} resources`, "desktop-action-note"));
        if (toolPermissions) controls.append(button(documentRef, row.tool_policy === "selected" ? "Review allowed tools" : "Choose allowed tools", "edit-mcp-tools", { id }));
        if (row.failure) controls.append(text(documentRef, "span", "Connection needs attention. Check the destination and authentication, then refresh status.", "desktop-action-note"));
      }
      controls.append(button(documentRef, "Remove server", "remove-mcp", { id }));
      if (credentials && ["bearer", "headers"].includes(row.auth)) {
        controls.append(text(documentRef, "span", row.credential_configured ? "Credential saved" : "Credential removed · connection blocked", "desktop-action-note"), button(documentRef, row.credential_configured ? "Replace credential" : "Set credential", "edit-mcp-credential", { id }));
        if (row.credential_configured) controls.append(button(documentRef, "Remove credential", "remove-mcp-credential", { id }));
      } else if (oauth) controls.append(button(documentRef, "Sign in", "login-mcp", { id }));
      else controls.append(text(documentRef, "span", "No OAuth", "desktop-action-note"));
    }));
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
  if (tab === "about") panel.append(text(documentRef, "h3", "About / security", "desktop-subheading"), text(documentRef, "p", "The panel connects through Home Assistant. Codex runs in the private App. Full auto allows work inside the selected workspace and enabled tools. The separate, optional Host Access App can grant root access to Home Assistant OS, including host files, credentials and networking, after an administrator acknowledges the warning and selects it for a task.", "desktop-note"));
  if (tab === "access") {
    panel.append(text(documentRef, "h3", "Choose the access your task needs", "desktop-subheading"));
    const workspace = text(documentRef, "section", "", "schedule-card host-access-settings");
    workspace.append(text(documentRef, "h3", "Full auto · workspace"), text(documentRef, "p", "Codex can use enabled tools automatically and edit files inside the selected workspace. It cannot use private host paths or direct network connections. Set your new-chat permission default in General, or change permissions for an individual chat.", "desktop-note"), button(documentRef, "New chat defaults", "select-settings-tab", { tab: "general" }));
    const mcpCard = text(documentRef, "section", "", "schedule-card host-access-settings");
    mcpCard.append(text(documentRef, "h3", "Home Assistant devices and automations"), text(documentRef, "p", "Connect HA-MCP to give Codex the Home Assistant tools you choose. This is separate from root host access; revoking one does not revoke the other.", "desktop-note"), button(documentRef, "Set up Home Assistant tools", "select-settings-tab", { tab: "mcp" }));
    const host = state.data?.host_access;
    const card = text(documentRef, "section", "", "schedule-card host-access-settings");
    card.append(text(documentRef, "h3", HOST_LABEL), text(documentRef, "p", "The optional Codex Host Access App lets Codex run commands as root on the HAOS machine. That includes its files, credentials, services, internet and local network. It can change or delete data and interrupt Home Assistant.", "desktop-note"));
    if (config?.capabilities?.includes("host_access_v1")) {
      card.append(text(documentRef, "p", host?.enabled ? "Enabled. Choose this mode explicitly for each chat or scheduled task." : "Review the full warning before enabling access. If the companion App is missing, the setup dialog links to its installation instructions.", "desktop-note"), button(documentRef, host?.enabled ? "Review host access" : "Set up host access", "review-host-access"));
      if (host?.enabled) card.append(button(documentRef, "Revoke host access", "revoke-host-access"));
      if (host?.enabled && settings.threadId) card.append(button(documentRef, "Use for current chat", "use-host-access"));
    } else {
      card.append(text(documentRef, "p", "Host access is not available on this connection. Update the Codex Bridge App and HACS Integration, then restart Home Assistant and reload this panel. Updating does not grant host access.", "desktop-note"));
      const guide = text(documentRef, "a", "Update instructions and missing-update checks");
      guide.href = "https://github.com/Herbertmt978/HA_Codex_Bridge/blob/main/docs/installation.md#update-an-existing-installation";
      guide.target = "_blank"; guide.rel = "noopener noreferrer";
      card.append(guide, button(documentRef, "Check connection options again", "refresh-settings-capabilities"));
    }
    panel.append(workspace, mcpCard, card);
  }
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
      text(documentRef, "h3", "New chat defaults", "desktop-subheading")
    );
    const defaults = documentRef.createElement("div"); defaults.className = "schedule-card settings-card";
    addPreference(defaults, "mode", "Permissions", [["observe", "Observe"], ["edit", "Edit workspace"], ["full-auto", "Full auto · workspace"]]);
    addPreference(defaults, "model", "Model", modelChoices(settings, preferences.model));
    addPreference(defaults, "thinking", "Reasoning", reasoningChoices(settings, preferences.model, preferences.thinking));
    panel.append(defaults,
      text(documentRef, "p", "Full auto lets Codex work automatically within the selected workspace and enabled tools. Observe is read-only; Edit workspace asks before commands. Private host paths and direct network access remain blocked.", "desktop-note"),
      text(documentRef, "p", "These defaults apply to new chats created in this browser. Inherit uses the project's defaults. Existing chats and scheduled tasks keep their own settings.", "desktop-note"), saved,
      button(documentRef, "Access settings and Home Assistant control", "select-settings-tab", { tab: "access" }),
      text(documentRef, "h3", "Native tools", "desktop-subheading"),
      rows,
      text(documentRef, "p", "Image generation uses the signed-in ChatGPT account and Codex's native tool. Ask for an image naturally in a chat.", "desktop-note")
    );
  }
  return section;
}

const renderedFeatureInputs = new WeakMap();

function featureDraftInputs(state) {
  return JSON.stringify({ formDraft: state.formDraft, agentsDrafts: state.agentsDrafts });
}

export function syncDesktopFeatureDrafts(container, state) {
  const rendered = renderedFeatureInputs.get(container);
  if (rendered) rendered.drafts = featureDraftInputs(state);
}

export function renderDesktopFeatureSurface(container, { destination = "scheduled", state = createDesktopFeatureState(), onAction, timezone, hasActiveProject = false, activeProjectId = null, status = {}, config = {}, settings = {} } = {}) {
  if (!container) return;
  const documentRef = container.ownerDocument || globalThis.document;
  container.onclick = (event) => {
    const target = event.target.closest?.("[data-desktop-action]");
    if (target) onAction?.(target.dataset.desktopAction, target.dataset, target);
  };
  container.onchange = (event) => {
    if (event.target?.matches?.("[data-mcp-tool]")) {
      state.mcpToolDraft = [...container.querySelectorAll("[data-mcp-tool]:checked")].map((input) => input.dataset.mcpTool);
    }
  };
  container.onsubmit = (event) => {
    event.preventDefault();
    const form = event.target?.closest?.("[data-desktop-form]");
    const submit = form?.querySelector('[data-desktop-action^="submit-"]');
    if (submit) onAction?.(submit.dataset.desktopAction, submit.dataset, submit);
  };
  // HA pushes unrelated state changes frequently. Keep the existing controls
  // and large catalogues mounted until an input used by this view changes.
  // Input handlers sync the draft snapshot because those edits are already in
  // the DOM. Programmatic draft resets must still invalidate the rendered view.
  const inputs = JSON.stringify({
    destination, state: { ...state, formDraft: undefined, agentsDrafts: undefined, mcpToolInventory: undefined, hostAccessGrant: undefined, hostUnattendedApproved: undefined, previewGeneration: undefined, createRequestId: undefined, nextRuns: undefined }, timezone, hasActiveProject, activeProjectId,
    mcpInventoryRevision: state.mcpToolInventory?.catalogue_revision,
    nativeTools: destination === "settings" ? getNativeToolsViewModel(status, config) : null,
    settingsModels: destination === "settings" ? settings.models : null,
    settingsCapabilities: destination === "settings" ? config?.capabilities : null,
    scheduledProposals: destination === "scheduled" ? config?.capabilities?.includes("automation_proposals_v1") : null,
    scheduledTextEdits: destination === "scheduled" ? config?.capabilities?.includes("automation_text_edits_v1") : null,
    settingsOwner: destination === "settings" ? settings.ownerKey || "codex-bridge:preferences:local" : null,
  });
  const drafts = featureDraftInputs(state);
  const rendered = renderedFeatureInputs.get(container);
  if (rendered?.inputs === inputs && rendered.drafts === drafts) return;
  container.replaceChildren();
  renderedFeatureInputs.set(container, { inputs, drafts });
  const heading = documentRef.createElement("div");
  heading.className = `desktop-feature-header${destination === "scheduled" ? "" : " desktop-feature-header-centered"}`;
  const destinationMeta = DESTINATIONS.find((item) => item.id === destination) || DESTINATIONS[1];
  heading.append(text(documentRef, "div", destinationMeta.label, "desktop-feature-title"));
  heading.append(text(documentRef, "p", destination === "scheduled" ? "Manage automations and run history." : destination === "skills" ? "Enable skills by scope and create bounded instructions." : destination === "plugins" ? "Install plugins and maintain trusted marketplaces." : "Connection, instructions, and security preferences.", "desktop-feature-summary"));
  if (!(destination === "scheduled" && state.form)) container.append(heading);
  if (state.loading) { container.setAttribute("aria-busy", "true"); container.append(renderLoading(documentRef, destinationMeta.label)); return; }
  container.setAttribute("aria-busy", "false");
  if (state.error) { const error = text(documentRef, "p", state.error, "desktop-error"); error.setAttribute("role", "alert"); container.append(error); container.append(button(documentRef, "Retry", "retry-desktop")); return; }
  if (state.notice) { const notice = text(documentRef, "p", state.notice, "desktop-notice"); notice.setAttribute("role", "status"); container.append(notice); }
  if (state.confirmAction) { const confirm = documentRef.createElement("div"); confirm.className = "desktop-notice"; confirm.setAttribute("role", "alert"); confirm.append(text(documentRef, "span", state.confirmAction.action === "rollback-stdio" ? "Restore the previously packaged revision? The server stays paused while you review its tools." : "This action is destructive. Confirm to continue."), button(documentRef, "Confirm", "confirm-desktop"), button(documentRef, "Cancel", "cancel-desktop-confirm")); container.append(confirm); }
  const content = destination === "scheduled" ? renderScheduled(documentRef, state, timezone, config?.capabilities?.includes("automation_proposals_v1"), config?.capabilities?.includes("automation_text_edits_v1")) : destination === "skills" ? renderSkills(documentRef, state) : destination === "plugins" ? renderPlugins(documentRef, state) : renderSettings(documentRef, state, hasActiveProject, activeProjectId, status, config, settings);
  container.append(content);
}

export { DESTINATIONS };
