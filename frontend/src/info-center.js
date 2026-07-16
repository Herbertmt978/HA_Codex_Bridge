const MAX_ROWS = 8;
const MAX_ARTIFACTS = 1000;
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

  const files = section(
    "files",
    "Files",
    fileSummary.count ? "Files created or attached in this chat are ready to review." : "No files are available for this chat yet.",
    [
      ["Available files", String(fileSummary.count)],
      ["Known size", formatBytes(fileSummary.bytes)],
      ["Workspace archive", safeThread.thread_id ? "Available from the Files section" : "Select a chat first"],
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

  return { tabs: INFO_TABS, activity, files, usage, system };
}
