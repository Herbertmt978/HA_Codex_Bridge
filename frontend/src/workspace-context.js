const bytes = (text) => new TextEncoder().encode(text).byteLength;

export function contextReference(item) {
  return { path: item.path, start_line: item.start_line, end_line: item.end_line, content_revision: item.content_revision };
}

export function contextSelection(path, start, end) {
  if (!path || path.length > 2048) throw new Error("Choose a workspace file.");
  if (!start && !end) return { path, start_line: null, end_line: null };
  const first = Number(start), last = Number(end);
  if (!start || !end || !Number.isSafeInteger(first) || !Number.isSafeInteger(last) || first < 1 || last < first || last - first + 1 > 200) {
    throw new Error("Enter both inclusive line numbers, with at most 200 lines.");
  }
  return { path, start_line: first, end_line: last };
}

export class WorkspaceContextController {
  constructor(panel) {
    this.panel = panel;
    this.items = new Map();
    this.revisions = new Map();
    this.epoch = 0;
    this.scope = null;
    this.busy = false;
  }
  current() { return this.items.get(this.panel._selectedThreadId) || []; }
  reset() { this.epoch++; this.items.clear(); this.revisions.clear(); this.scope = null; this.busy = false; }
  revision(threadId) { return this.revisions.get(threadId) || 0; }
  setItems(threadId, items) {
    this.items.set(threadId, items);
    this.revisions.set(threadId, this.revision(threadId) + 1);
  }
  locked() { return Boolean(this.panel._promptMutationForThread(this.panel._selectedThreadId)); }
  snapshot() {
    const items = this.current();
    if (items.some((item) => item.stale)) throw new Error("A workspace file changed or moved. Refresh or remove its context before sending.");
    return items.map(contextReference);
  }
  clearCaptured(threadId, references, revision) {
    const current = this.items.get(threadId) || [];
    if (revision === this.revision(threadId) && JSON.stringify(current.map(contextReference)) === JSON.stringify(references)) {
      this.items.delete(threadId);
      this.revisions.set(threadId, this.revision(threadId) + 1);
    }
    this.render();
  }
  staleCaptured(threadId, revision) {
    if (revision === this.revision(threadId)) for (const item of this.items.get(threadId) || []) item.stale = true;
    this.render();
  }
  render() {
    const p = this.panel, root = p.shadowRoot.getElementById("workspace-context");
    if (!root) return;
    const supported = p._config?.capabilities?.includes("workspace_context_v1");
    root.hidden = !supported || !p._activeThread || p._activeThread.schedule_eligible === false;
    const scope = `${p._hass?.user?.id || ""}:${p._selectedThreadId || ""}`;
    if (scope !== this.scope) {
      this.scope = scope; this.epoch++; this.busy = false; root.replaceChildren();
      if (!root.hidden) this.build(root);
    } else if (!root.hidden && !root.firstChild) this.build(root);
    if (root.hidden || !root.firstChild) return;
    const list = root.querySelector("[data-context-items]");
    const key = JSON.stringify(this.current().map((item) => [contextReference(item), item.stale]));
    if (list.dataset.key !== key) {
      const previousCount = list.childElementCount;
      const open = new Set([...list.querySelectorAll("details")].flatMap((el, index) => el.open ? [index] : []));
      list.replaceChildren();
      this.current().forEach((item, index) => {
        const detail = document.createElement("details");
        detail.open = open.has(index) || index >= previousCount;
        const summary = document.createElement("summary");
        summary.textContent = `${item.path} · ${item.start_line === null ? "Whole file" : `Lines ${item.start_line}–${item.end_line} (excerpt)`}${item.stale ? " · Changed or moved — refresh required" : ""}`;
        const pre = document.createElement("pre"); pre.textContent = item.text;
        pre.style.cssText = "max-height:16rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere";
        detail.append(summary, pre, this.button("Refresh excerpt", () => this.refresh(index)), this.button("Remove context", () => {
          this.setItems(p._selectedThreadId, this.current().filter((_, i) => i !== index)); this.render();
        })); list.append(detail);
      });
      list.dataset.key = key;
    }
    root.querySelectorAll("button,input,select").forEach((el) => { el.disabled = this.busy || this.locked(); });
  }
  button(label, action) {
    const el = document.createElement("button"); el.type = "button"; el.className = "composer-limits-button";
    el.textContent = label; el.addEventListener("click", () => { if (!this.locked() && !this.busy) void action(); }); return el;
  }
  build(root) {
    const heading = document.createElement("summary"); heading.textContent = "Attach workspace context";
    const help = document.createElement("p"); help.className = "row-meta";
    help.textContent = "Choose a file from this chat’s workspace. Leave line numbers blank for the whole file, or select up to 200 inclusive lines. Inspect the exact text before sending. Context stays in this visit only.";
    const status = document.createElement("p"); status.dataset.contextStatus = ""; status.setAttribute("role", "status"); status.className = "row-meta";
    const browser = document.createElement("div"); browser.dataset.contextBrowser = ""; browser.className = "row-actions";
    const fields = document.createElement("div"); fields.className = "row-actions";
    for (const [name, label, type] of [["path", "Workspace-relative file", "text"], ["start", "First line (optional)", "number"], ["end", "Last line (optional)", "number"]]) {
      const wrapper = document.createElement("label"); wrapper.className = "composer-utility";
      const text = document.createElement("span"); text.className = "composer-utility-label"; text.textContent = label;
      const input = document.createElement("input"); input.dataset.contextField = name; input.type = type; input.style.minWidth = "0";
      if (type === "number") { input.min = "1"; input.step = "1"; input.style.width = "7rem"; }
      wrapper.append(text, input); fields.append(wrapper);
    }
    fields.append(this.button("Inspect and attach", () => this.attach()));
    const list = document.createElement("div"); list.dataset.contextItems = "";
    root.append(heading, help, this.button("Browse workspace", () => this.browse(".")), browser, fields, status, list);
  }
  status(text) { const el = this.panel.shadowRoot.querySelector("[data-context-status]"); if (el) el.textContent = text; }
  async request(command, payload, apply) {
    const threadId = this.panel._selectedThreadId, epoch = this.epoch;
    this.busy = true; this.status("Loading workspace context…"); this.render();
    try {
      const value = await this.panel._callWS(command, { thread_id: threadId, ...payload });
      if (epoch === this.epoch && threadId === this.panel._selectedThreadId) { this.status(""); apply(value); }
    } catch (error) {
      if (epoch === this.epoch) this.status(this.error(error));
    } finally { if (epoch === this.epoch) { this.busy = false; this.render(); } }
  }
  error(error) {
    const code = this.panel._bridgeErrorCode(error);
    return ({ workspace_context_not_found: "File not found. It may have moved or been deleted.", unsafe_workspace_context_entry: "This file cannot be read safely within the workspace.", workspace_context_limit_exceeded: "The selection is too large. Choose a smaller line range.", workspace_context_range_unavailable: "Those lines are no longer available. Check the file and range.", workspace_context_not_text: "Choose a UTF-8 text file.", workspace_context_unavailable: "Workspace context is unavailable on this runtime." })[code] || "Workspace context could not be loaded or exceeds its size limit. Check the file and range, then retry.";
  }
  async browse(directory) {
    await this.request("workspace_context", { directory }, (value) => {
      const root = this.panel.shadowRoot.querySelector("[data-context-browser]"); root.replaceChildren();
      const label = document.createElement("span"); label.textContent = `${value.directory}${value.truncated ? " · Partial listing; enter a file path if missing" : ""}`; root.append(label);
      if (directory !== ".") root.append(this.button("Parent directory", () => this.browse(directory.includes("/") ? directory.slice(0, directory.lastIndexOf("/")) : ".")));
      if (!(value.items || []).length) { const empty = document.createElement("span"); empty.textContent = "No selectable files in this directory."; root.append(empty); }
      for (const item of value.items || []) {
        if (item.kind === "directory") root.append(this.button(`${item.name}/`, () => this.browse(item.path)));
        else if (item.selectable) root.append(this.button(item.name, () => { this.panel.shadowRoot.querySelector('[data-context-field="path"]').value = item.path; }));
      }
    });
  }
  async attach() {
    if (this.locked() || this.busy) return;
    const field = (key) => this.panel.shadowRoot.querySelector(`[data-context-field="${key}"]`).value;
    let selection;
    try { selection = contextSelection(field("path"), field("start"), field("end")); }
    catch (error) { this.status(error.message); return; }
    if (this.current().length >= 8) { this.status("Remove a context item before adding more (maximum eight)."); return; }
    await this.request("read_workspace_context", selection, (value) => {
      if (value.status !== "ready" || typeof value.text !== "string") { this.status("Refresh the file selection before attaching."); return; }
      if (this.current().reduce((total, item) => total + bytes(item.text), bytes(value.text)) > 96 * 1024) throw new Error("Context exceeds its total size limit.");
      this.setItems(this.panel._selectedThreadId, [...this.current(), { ...selection, content_revision: value.content_revision, text: value.text, stale: false }]);
    });
  }
  async refresh(index) {
    const item = this.current()[index]; if (!item || this.locked() || this.busy) return;
    // Explicit refresh is the only action that replaces the reviewed snapshot.
    await this.request("read_workspace_context", { path: item.path, start_line: item.start_line, end_line: item.end_line }, (value) => {
      if (value.status !== "ready" || typeof value.text !== "string") { this.status("The file is no longer available. Remove its context or check its path."); return; }
      if (this.current().reduce((total, other, i) => total + (i === index ? 0 : bytes(other.text)), bytes(value.text)) > 96 * 1024) throw new Error("Context exceeds its total size limit.");
      this.setItems(this.panel._selectedThreadId, this.current().map((other, i) => i === index ? { ...item, text: value.text, content_revision: value.content_revision, stale: false } : other));
    });
  }
}
