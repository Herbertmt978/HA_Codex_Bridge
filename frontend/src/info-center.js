const MAX_ROWS = 8;
const MAX_ARTIFACTS = 1000;
const MAX_COUNT = 1000000;
const MAX_HISTORY_ITEMS = 32;
const MAX_SAFE_BYTES = 1024 ** 5;
const SAFE_VERSION = /^[0-9][0-9A-Za-z.+-]{0,31}$/u;

const PLAN_NAMES = Object.freeze({
  free: "Free",
  go: "Go",
  plus: "Plus",
  pro: "Pro",
  prolite: "Pro",
  team: "Team",
  self_serve_business_usage_based: "Business",
  business: "Business",
  enterprise_cbp_usage_based: "Enterprise",
  enterprise: "Enterprise",
  edu: "Education",
});

const RUN_STATES = Object.freeze({
  starting: "Starting",
  queued: "Queued",
  running: "Working",
  in_progress: "Working",
  cancelling: "Stopping",
  completed: "Completed",
  failed: "Needs attention",
  error: "Needs attention",
  cancelled: "Stopped",
  interrupted: "Interrupted",
  idle: "Ready",
});

const MODE_NAMES = Object.freeze({
  observe: "Observe",
  edit: "Edit",
  "full-auto": "Full auto",
});

const PROJECT_KINDS = Object.freeze({
  direct: "Direct chat",
  project: "Project workspace",
  imported: "Imported workspace",
});

const RUN_ACTIVITY_STATES = Object.freeze({
  idle: "Ready",
  queued: "Queued",
  running: "Working",
  completed: "Completed",
  failed: "Needs attention",
  cancelled: "Stopped",
  interrupted: "Interrupted",
});

// run-activity.js intentionally emits these short, presentation-safe labels.
// Keep this list closed so arbitrary event/action text cannot enter the card.
const SAFE_ACTIVITY_LABELS = new Set([
  "Preparing a response",
  "Running a command",
  "Compacting context",
  "Delegating to an agent",
  "Calling a tool",
  "Applying file changes",
  "Generating an image",
  "Viewing an image",
  "Calling an MCP tool",
  "Planning the work",
  "Thinking through the request",
  "Waiting",
  "Working with a sub-agent",
  "Searching the web",
  "Reading files",
  "Listing files",
  "Searching files",
  "Opening a web page",
  "Finding text in a page",
  "Using web search",
  "Adding files",
  "Updating files",
  "Deleting files",
  "Starting a sub-agent",
  "Steering a sub-agent",
  "Resuming a sub-agent",
  "Waiting for sub-agents",
  "Closing a sub-agent",
  "Sub-agent started",
  "Sub-agent active",
  "Sub-agent interrupted",
  "Working on the request",
  "Waiting in queue",
  "Generating a response",
  "Run completed",
  "Run failed",
  "Run cancelled",
  "Run interrupted",
]);

const WEB_ACTIVITY_LABELS = new Set([
  "Searching the web",
  "Opening a web page",
  "Finding text in a page",
  "Using web search",
]);

export const INFO_SECTION_IDS = Object.freeze([
  "outputs",
  "subagents",
  "background",
  "browser",
  "sources",
  "usage",
  "system",
]);

export const INFO_TABS = Object.freeze([
  Object.freeze({ id: "activity", label: "Activity" }),
  Object.freeze({ id: "files", label: "Files" }),
  Object.freeze({ id: "usage", label: "Usage" }),
  Object.freeze({ id: "system", label: "System" }),
]);

export const KEYBOARD_SHORTCUTS = Object.freeze([
  Object.freeze({ keys: "Enter", description: "Send a message from the composer." }),
  Object.freeze({ keys: "Shift + Enter", description: "Add a new line in the composer." }),
  Object.freeze({ keys: "Escape", description: "Close an open menu, drawer, or confirmation." }),
  Object.freeze({ keys: "Tab", description: "Move through controls in their visible order." }),
]);

export const ABOUT_SCREENS = Object.freeze([
  Object.freeze({
    id: "how-it-works",
    title: "How it works",
    summary: "Home Assistant is the browser-facing control plane; Codex runs through the private Bridge.",
  }),
  Object.freeze({
    id: "privacy-security",
    title: "Privacy and security",
    summary: "Use a small granted workspace, review approvals, and keep the App and Bridge private to Home Assistant.",
  }),
  Object.freeze({
    id: "keyboard-shortcuts",
    title: "Keyboard shortcuts",
    summary: "Keyboard controls follow the current panel state and preserve visible focus.",
    rows: KEYBOARD_SHORTCUTS,
  }),
  Object.freeze({
    id: "about-version",
    title: "About and versions",
    summary: "Version labels describe the installed panel and private runtime components without exposing connection details.",
  }),
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeVersion(value) {
  return typeof value === "string" && SAFE_VERSION.test(value) ? value : "Unavailable";
}

function safeBooleanLabel(value, positive, negative = "Unavailable") {
  return value === true ? positive : value === false ? negative : "Unavailable";
}

function safeEnum(value, values, fallback = "Unavailable") {
  return typeof value === "string" && Object.hasOwn(values, value) ? values[value] : fallback;
}

function safePlan(value) {
  if (typeof value !== "string") return "Unknown";
  return PLAN_NAMES[value.trim().toLowerCase()] || "Unknown";
}

function safePercent(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) {
    return "Unavailable";
  }
  return `${Math.round(value)}% remaining`;
}

function safeCount(value) {
  return Number.isSafeInteger(value) && value >= 0
    ? Math.min(MAX_COUNT, value)
    : 0;
}

function safeActivityLabel(value) {
  return typeof value === "string" && SAFE_ACTIVITY_LABELS.has(value) ? value : "";
}

function safeRunActivity(value) {
  if (!isRecord(value)) {
    return {
      state: "No active run",
      action: "",
      actionHistoryCount: 0,
      files: { changed: 0, additions: 0, deletions: 0 },
      subagents: null,
      webSearchActive: false,
    };
  }
  const stateValue = value.state || value.status;
  const state = typeof stateValue === "string" && stateValue
    ? safeEnum(stateValue, RUN_ACTIVITY_STATES, "Unavailable")
    : "No active run";
  const action = safeActivityLabel(value.currentActivity || value.action);
  const history = Array.isArray(value.actionHistory)
    ? value.actionHistory.slice(0, MAX_HISTORY_ITEMS).filter((item) => (
      isRecord(item) ? safeActivityLabel(item.label) : safeActivityLabel(item)
    )).length
    : 0;
  const files = isRecord(value.files) ? value.files : {};
  const subagentInput = isRecord(value.subagents) ? value.subagents : null;
  const subagents = subagentInput
    ? {
      total: safeCount(subagentInput.total),
      active: safeCount(subagentInput.active),
      completed: safeCount(subagentInput.completed),
      attention: safeCount(subagentInput.attention),
    }
    : null;
  return {
    state,
    action,
    actionHistoryCount: history,
    files: {
      changed: safeCount(files.changed),
      additions: safeCount(files.additions),
      deletions: safeCount(files.deletions),
    },
    subagents,
    webSearchActive: value.webSearchActive === true || value.web_search_active === true || WEB_ACTIVITY_LABELS.has(action),
  };
}

function safeSourceCount(value) {
  if (Array.isArray(value)) return Math.min(MAX_ARTIFACTS, value.length);
  return safeCount(value);
}

function safeArtifactSummary(artifacts) {
  if (!Array.isArray(artifacts)) {
    return { count: 0, bytes: 0 };
  }
  let bytes = 0;
  for (const artifact of artifacts.slice(0, MAX_ARTIFACTS)) {
    const size = isRecord(artifact) ? artifact.size_bytes : null;
    if (Number.isSafeInteger(size) && size >= 0) {
      bytes = Math.min(MAX_SAFE_BYTES, bytes + size);
    }
  }
  return { count: Math.min(MAX_ARTIFACTS, artifacts.length), bytes };
}

function formatBytes(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = "B";
  for (const candidate of units) {
    value /= 1024;
    unit = candidate;
    if (value < 1024 || candidate === "PB") break;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
}

function section(id, title, summary, rows) {
  return {
    id,
    title,
    summary,
    rows: rows.slice(0, MAX_ROWS).map(([label, value]) => ({ label, value })),
  };
}

/**
 * Return a compact, presentation-safe information center. This deliberately
 * projects only named scalar fields; it never reflects raw events, paths,
 * artifact names, account identifiers, connection details, or unknown data.
 */
export function getInfoCenterViewModel({
  status = {},
  thread = {},
  project = {},
  artifacts = [],
  panelVersion = "",
  runActivity = null,
  browser = {},
  sources = [],
} = {}) {
  const safeStatus = isRecord(status) ? status : {};
  const auth = isRecord(safeStatus.auth) ? safeStatus.auth : {};
  const account = isRecord(safeStatus.account) ? safeStatus.account : {};
  const limits = isRecord(safeStatus.limits) ? safeStatus.limits : {};
  const primary = isRecord(limits.primary) ? limits.primary : {};
  const secondary = isRecord(limits.secondary) ? limits.secondary : {};
  const diagnostics = isRecord(safeStatus.diagnostics) ? safeStatus.diagnostics : {};
  const app = isRecord(safeStatus.app) ? safeStatus.app : {};
  const integration = isRecord(safeStatus.integration) ? safeStatus.integration : {};
  const safeThread = isRecord(thread) ? thread : {};
  const safeProject = isRecord(project) ? project : {};
  const fileSummary = safeArtifactSummary(artifacts);
  const attachmentCount = Array.isArray(safeThread.attachments)
    ? Math.min(MAX_ARTIFACTS, safeThread.attachments.length)
    : 0;
  const activitySnapshot = safeRunActivity(runActivity);
  const providerCapabilities = isRecord(safeStatus.provider_capabilities)
    ? safeStatus.provider_capabilities
    : {};
  const safeBrowser = isRecord(browser) ? browser : {};
  const webSearchAvailable = providerCapabilities.web_search === true
    || safeBrowser.web_search === true;
  const webSearchUnavailable = providerCapabilities.web_search === false
    || safeBrowser.web_search === false;
  const sourceCount = Math.max(
    safeSourceCount(sources),
    safeSourceCount(safeBrowser.source_count),
    safeSourceCount(safeBrowser.sources),
  );
  const runState = safeEnum(safeThread.status, RUN_STATES, "No active run");
  const connected = auth.state === "ok" && auth.auth_required === false && account.auth_mode === "chatgpt";
  const appReady = app.connected === true;
  const integrationReady = integration.ready === true;
  const bridgeReady = Number(safeStatus.api_version) === 1 && safeStatus.bridge_ready !== false;
  const codexReady = safeStatus.codex_ready === true || (
    typeof diagnostics.app_server_version === "string" && bridgeReady
  );

  const activity = section(
    "activity",
    "Current activity",
    runState === "No active run" ? "Choose a chat to see its current run state." : `This chat is ${runState.toLowerCase()}.`,
    [
      ["Chat", safeThread.thread_id ? "Selected" : "Not selected"],
      ["Run", runState],
      ["Permission", safeEnum(safeThread.mode, MODE_NAMES, "Unavailable")],
      ["Workspace type", safeEnum(safeProject.kind, PROJECT_KINDS, "Unavailable")],
    ],
  );

  const outputs = section(
    "outputs",
    "Outputs",
    fileSummary.count ? "Files created or attached in this chat are ready to review." : "No files are available for this chat yet.",
    [
      ["Available files", String(fileSummary.count)],
      ["Known size", formatBytes(fileSummary.bytes)],
      ["Attachments", String(attachmentCount)],
      ["Workspace archive", safeThread.thread_id ? "Available from the Files section" : "Select a chat first"],
    ],
  );

  const subagents = section(
    "subagents",
    "Subagents",
    activitySnapshot.subagents?.total
      ? "Aggregate sub-agent activity is shown without names or prompts."
      : "No sub-agent activity has been reported for this run.",
    [
      ["Total", String(activitySnapshot.subagents?.total || 0)],
      ["Active", String(activitySnapshot.subagents?.active || 0)],
      ["Completed", String(activitySnapshot.subagents?.completed || 0)],
      ["Needs attention", String(activitySnapshot.subagents?.attention || 0)],
    ],
  );

  const background = section(
    "background",
    "Background activity",
    activitySnapshot.action || activitySnapshot.state === "Working"
      ? `This chat is ${activitySnapshot.state.toLowerCase()}.`
      : "No background activity is currently reported.",
    [
      ["Run", activitySnapshot.state],
      ["Current step", activitySnapshot.action || "Unavailable"],
      ["Recent stages", String(activitySnapshot.actionHistoryCount)],
      ["Files changed", String(activitySnapshot.files.changed)],
    ],
  );

  const browserSection = section(
    "browser",
    "Browser",
    activitySnapshot.webSearchActive
      ? "Web search is active for this run."
      : webSearchAvailable
        ? "Web search is available when the run requests it."
        : "No browser activity is currently reported.",
    [
      ["Web search", webSearchAvailable ? "Available" : webSearchUnavailable ? "Unavailable" : "Unknown"],
      ["Current use", activitySnapshot.webSearchActive ? "Active" : "Not active"],
    ],
  );

  const sourcesSection = section(
    "sources",
    "Sources",
    sourceCount
      ? `${sourceCount} source${sourceCount === 1 ? "" : "s"} reported for this run.`
      : "No source context is available for this run.",
    [
      ["Sources", String(sourceCount)],
      ["Details", sourceCount ? "Available in the transcript" : "Unavailable"],
    ],
  );

  const usage = section(
    "usage",
    "Usage and account",
    limits.blocked === true
      ? "Usage is temporarily unavailable for this account."
      : connected
        ? "Usage is shown from the latest safe account snapshot."
        : "Connect a ChatGPT account to view usage.",
    [
      ["ChatGPT", connected ? "Connected" : "Not connected"],
      ["Plan", safePlan(account.plan_type)],
      ["5-hour limit", limits.available === true ? safePercent(primary.remaining_percent) : "Unavailable"],
      ["Weekly limit", limits.available === true ? safePercent(secondary.remaining_percent) : "Unavailable"],
    ],
  );

  const system = section(
    "system",
    "System status",
    appReady && integrationReady && bridgeReady && codexReady
      ? "The private runtime is ready."
      : "One or more private runtime components need attention.",
    [
      ["Panel", safeVersion(panelVersion)],
      ["App", `${safeBooleanLabel(app.connected, "Connected", "Not connected")} · ${safeVersion(app.version || diagnostics.app_version)}`],
      ["Integration", `${safeBooleanLabel(integration.ready, "Ready", "Needs attention")} · ${safeVersion(integration.version)}`],
      ["Bridge", `${bridgeReady ? "Ready" : "Needs attention"} · ${safeVersion(diagnostics.bridge_version)}`],
      ["Codex", `${codexReady ? "Ready" : "Needs attention"} · ${safeVersion(diagnostics.app_server_version || diagnostics.active_codex_version)}`],
    ],
  );

  const sections = [outputs, subagents, background, browserSection, sourcesSection, usage, system];
  return {
    tabs: INFO_TABS,
    sections,
    // Legacy aliases remain stable for callers that render the existing tabs.
    activity,
    files: outputs,
    usage,
    system,
  };
}
