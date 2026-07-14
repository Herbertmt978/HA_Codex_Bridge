const APPROVAL_ACTIONS = new Map([
  ["accept", "Accept"],
  ["decline", "Decline"],
  ["cancel", "Cancel"],
]);

const MAX_TITLE_LENGTH = 160;
const MAX_SUMMARY_LENGTH = 512;
const MAX_COMMAND_LENGTH = 512;
const MAX_SCOPE_PATHS = 128;

function plainText(value, limit) {
  if (typeof value !== "string") return "";
  return [...value]
    .filter((character) => {
      const code = character.codePointAt(0);
      return code > 31 && code !== 127;
    })
    .join("")
    .trim()
    .slice(0, limit);
}

function safeWorkspacePath(value) {
  const path = plainText(value, 240).replaceAll("\\", "/");
  if (!path || path.startsWith("/") || /^[A-Za-z]:/u.test(path) || path.includes("://")) return null;
  const segments = path.split("/");
  return segments.every((segment) => segment && segment !== "." && segment !== "..") ? path : null;
}

function expiryState(value, now) {
  if (typeof value !== "string") return { expired: true, label: "Expiry unavailable" };
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return { expired: true, label: "Expiry unavailable" };
  if (timestamp <= now) return { expired: true, label: "Expired" };
  return { expired: false, label: `Expires ${new Date(timestamp).toISOString().replace(".000", "")}` };
}

/**
 * Project a pending command or file-change request into a render-safe decision card.
 * `pending` represents a local submission already in flight, not the server's pending status.
 */
export function getApprovalViewModel(interaction = {}, { now = Date.now(), pending = false, stale = false } = {}) {
  const display = interaction && typeof interaction.display === "object" ? interaction.display : {};
  const expiry = expiryState(interaction?.expires_at, now);
  const interactionPending = interaction?.status === "pending";
  const unavailable = Boolean(pending || stale || expiry.expired || !interactionPending);
  const state = pending ? "submitting" : stale ? "stale" : expiry.expired ? "expired" : interactionPending ? "ready" : "unavailable";
  const allowedActions = Array.isArray(interaction?.allowed_actions) ? interaction.allowed_actions : [];
  const actions = [...APPROVAL_ACTIONS]
    .filter(([action]) => allowedActions.includes(action))
    .map(([id, label]) => ({ id, label, disabled: unavailable }));
  const scope = Array.isArray(display.workspace_paths)
    ? display.workspace_paths.map(safeWorkspacePath).filter(Boolean).slice(0, MAX_SCOPE_PATHS)
    : [];
  const command = plainText(display.command, MAX_COMMAND_LENGTH);
  const kind = interaction?.kind === "file_change_approval" ? "File change approval" : "Command approval";
  return {
    interactionId: typeof interaction?.interaction_id === "string" ? interaction.interaction_id : "",
    title: plainText(display.title, MAX_TITLE_LENGTH) || kind,
    summary: plainText(display.summary, MAX_SUMMARY_LENGTH) || "Codex needs your decision to continue.",
    kind,
    command: command || null,
    scope,
    expiry: expiry.label,
    state,
    disabled: unavailable,
    actions,
  };
}

/** Render a decision card with text-only untrusted content and native disabled controls. */
export function renderApproval(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = `approval-card approval-${model.state}`;
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "false");
  card.tabIndex = -1;
  const accessibleId = /^[A-Za-z0-9_.:-]{1,128}$/u.test(model.interactionId)
    ? model.interactionId
    : "pending";
  const titleId = `approval-${accessibleId}-title`;
  const summaryId = `approval-${accessibleId}-summary`;
  card.setAttribute("aria-labelledby", titleId);
  card.setAttribute("aria-describedby", summaryId);

  const title = document.createElement("h3");
  title.id = titleId;
  title.textContent = model.title;
  const summary = document.createElement("p");
  summary.id = summaryId;
  summary.textContent = model.summary;
  const status = document.createElement("p");
  status.className = "decision-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = model.state === "submitting" ? "Sending decision..." : model.expiry;
  card.append(title, summary, status);

  if (model.command) {
    const commandLabel = document.createElement("span");
    commandLabel.className = "decision-label";
    commandLabel.textContent = "Command";
    const command = document.createElement("pre");
    command.className = "decision-command";
    command.textContent = model.command;
    card.append(commandLabel, command);
  }
  if (model.scope.length) {
    const scopeLabel = document.createElement("span");
    scopeLabel.className = "decision-label";
    scopeLabel.textContent = "Workspace files";
    const list = document.createElement("ul");
    list.className = "decision-scope";
    for (const path of model.scope) {
      const item = document.createElement("li");
      item.textContent = path;
      list.append(item);
    }
    card.append(scopeLabel, list);
  }

  const actions = document.createElement("div");
  actions.className = "decision-actions";
  for (const action of model.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = `${action.id}-interaction`;
    button.dataset.decision = action.id;
    button.textContent = action.label;
    button.disabled = action.disabled;
    button.setAttribute("aria-disabled", String(action.disabled));
    actions.append(button);
  }
  card.append(actions);
  container.append(card);
}
