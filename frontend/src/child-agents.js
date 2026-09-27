/** Genuine, parent-scoped runtime children. No identities are inferred from totals. */
const labels = Object.freeze({
  pendingInit: "Starting", running: "Working", interrupted: "Interrupted",
  completed: "Completed", errored: "Failed", shutdown: "Stopped", notFound: "Unavailable",
});
const text = (tag, value, className = "") => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

export const childAgentsCss = `
  .child-agents-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .child-agent { padding: 8px 0; border-top: 1px solid var(--border-color); }
  .child-agent summary { cursor: pointer; padding: 6px 0; overflow-wrap: anywhere; }
  .child-agent-text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
  .child-agent-controls { display: flex; flex-wrap: wrap; gap: 8px; padding: 6px 0; }
`;

export class ChildAgentsView {
  constructor(panel) {
    this.panel = panel;
    this.threadId = null;
    this.ownerKey = null;
    this.selectionEpoch = null;
    this.rows = [];
    this.loaded = false;
    this.busy = false;
    this.error = "";
    this.notice = "";
    this.generation = 0;
    this.version = 0;
    this.open = new Set();
  }

  supported() {
    return Boolean(this.panel._selectedThreadId
      && this.panel._hass?.user?.id
      && this.panel._config?.capabilities?.includes("subagents_v1")
      && !this.panel._activeThread?.assist_origin);
  }

  sync() {
    const ownerKey = this.panel._hass?.user?.id || null;
    const selectionEpoch = this.panel._threadSelectionEpoch || 0;
    if (this.threadId === this.panel._selectedThreadId && this.ownerKey === ownerKey
      && this.selectionEpoch === selectionEpoch) return;
    this.threadId = this.panel._selectedThreadId;
    this.ownerKey = ownerKey;
    this.selectionEpoch = selectionEpoch;
    this.clear();
  }

  clear() {
    this.rows = [];
    this.loaded = false;
    this.busy = false;
    this.error = "";
    this.notice = "";
    this.open.clear();
    this.generation += 1;
    this.version += 1;
  }

  invalidate() {
    this.rows = this.rows.map((row) => ({ ...row, stale: true, can_stop: false }));
    this.busy = false;
    this.generation += 1;
    this.version += 1;
  }

  render(container) {
    this.sync();
    if (!this.supported()) return;
    const section = document.createElement("section");
    section.className = "activity-center-section";
    section.dataset.section = "individual-subagents";
    section.append(text("h3", "Individual subagents"));
    const toolbar = document.createElement("div");
    toolbar.className = "child-agents-toolbar";
    toolbar.append(this.button("Refresh list", () => this.load(), "list"));
    section.append(toolbar);
    const status = text("p", this.error || this.notice || (this.busy ? "Checking subagents…"
      : !this.loaded ? "Refresh to inspect runtime-reported children."
        : !this.rows.length ? "No retained children have been reported for this chat."
          : "Last reported status is retained. Verify a child before stopping it."), "row-meta");
    status.setAttribute("role", "status");
    section.append(status);
    section.append(text("p", "This view retains up to 64 children per chat; older terminal records may expire.", "row-meta"));
    for (const row of this.rows) {
      const details = document.createElement("details");
      details.className = "child-agent";
      details.open = this.open.has(row.child_id);
      details.addEventListener("toggle", () => {
        if (details.open) this.open.add(row.child_id);
        else this.open.delete(row.child_id);
      });
      details.append(text("summary", `${row.label || "Subagent"} · ${labels[row.status] || "Unknown"}${row.stale ? " · Last known status" : ""}${row.stop_pending ? " · Stop requested" : ""}`));
      if (row.task) details.append(text("p", row.task, "child-agent-text"));
      if (row.result) {
        details.append(text("h4", "Reported result"), text("p", row.result, "child-agent-text"));
      }
      const controls = document.createElement("div");
      controls.className = "child-agent-controls";
      controls.append(
        this.button("Verify status", () => this.action(row, "refresh"), `${row.child_id}:refresh`),
        this.button("Stop this child", () => this.action(row, "stop"), `${row.child_id}:stop`, !row.can_stop || this.panel._activeThread?.status !== "running"),
      );
      const followup = this.button("Follow-up unavailable", () => {}, `${row.child_id}:followup`, true);
      followup.setAttribute("aria-describedby", `child-followup-${row.child_id}`);
      controls.append(followup);
      const reason = text("p", row.follow_up_reason || "Direct child follow-up is unavailable in this runtime.", "row-meta");
      reason.id = `child-followup-${row.child_id}`;
      details.append(controls, reason);
      section.append(details);
    }
    container.append(section);
  }

  button(label, action, focusKey, disabled = false) {
    const button = text("button", label, "composer-limits-button");
    button.type = "button";
    button.disabled = this.busy || disabled;
    button.dataset.childFocus = focusKey;
    button.addEventListener("click", () => { void action(); });
    return button;
  }

  paint(focusKey = null) {
    this.version += 1;
    this.panel._resourceRenderKey = null;
    this.panel._renderActivityCenter();
    if (focusKey) {
      const button = [...this.panel.shadowRoot.querySelectorAll("[data-child-focus]")]
        .find((node) => node.dataset.childFocus === focusKey && !node.disabled);
      button?.focus();
    }
  }

  async request(action, payload, apply, focusKey) {
    this.sync();
    if (this.busy || !this.supported()) return;
    const generation = this.generation;
    const threadId = this.threadId;
    const ownerKey = this.ownerKey;
    this.busy = true;
    this.error = "";
    this.notice = "";
    this.paint();
    try {
      const result = await this.panel._callWS(action, { thread_id: threadId, ...payload });
      this.sync();
      if (generation !== this.generation || ownerKey !== this.ownerKey || !this.supported()) return;
      apply(result);
    } catch {
      this.sync();
      if (generation !== this.generation || ownerKey !== this.ownerKey || !this.supported()) return;
      this.error = "The child status or action could not be confirmed. Refresh the list and verify its status.";
    } finally {
      this.sync();
      if (generation === this.generation) {
        this.busy = false;
        this.paint(focusKey);
      }
    }
  }

  async load() {
    return this.request("child_agents", {}, (result) => {
      this.rows = Array.isArray(result?.children) ? result.children.filter((row) =>
        /^[a-f0-9]{32}$/.test(row?.child_id) && Number.isSafeInteger(row.revision)
        && row.revision > 0 && Object.hasOwn(labels, row.status)).slice(0, 64) : [];
      this.loaded = true;
    }, "list");
  }

  async action(row, action) {
    if (!this.rows.includes(row) || (action === "stop"
      && (!row.can_stop || this.panel._activeThread?.status !== "running"))) return;
    const payload = { child_id: row.child_id, action };
    if (action === "stop") {
      payload.revision = row.revision;
      payload.client_request_id = crypto.randomUUID();
    }
    return this.request("child_agent_action", payload, (result) => {
      const child = action === "stop" ? result?.child : result;
      if (child?.child_id === row.child_id) {
        this.rows = this.rows.map((existing) => existing.child_id === row.child_id ? child : existing);
      }
      if (action === "stop") this.notice = result?.outcome === "accepted"
        ? "Stop requested for this child. Verify its status to confirm the result."
        : "The stop outcome is unknown. Verify this child before taking another action.";
    }, `${row.child_id}:refresh`);
  }
}
