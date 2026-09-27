const bytes = (value) => new TextEncoder().encode(value).byteLength;
const fields = ["source_thread_id", "message_sequence", "start_char", "end_char", "content_revision"];
export const chatContextStyles = `
  #chat-context[hidden] { display: none !important; }
  #chat-context { grid-column: 1 / -1; grid-row: auto; min-width: 0; max-height: 42dvh; overflow: auto; padding: 6px; }
  #chat-context .row-actions, #chat-context .composer-utility { flex-wrap: wrap; }
  #chat-context .composer-select { min-width: 0; max-width: 100%; }
  #chat-context select { max-width: 18rem; }
  #chat-context input { min-height: 32px; font-size: var(--font-control-size); }
  #chat-context summary { cursor: pointer; padding: 6px; overflow-wrap: anywhere; }
  #chat-context pre { font-size: var(--font-control-size); }
  @media (max-width: 880px) {
    #chat-context button, #chat-context input, #chat-context select, #chat-context summary { min-height: 44px; }
    #chat-context .composer-utility { width: 100%; }
    #chat-context select { max-width: 100%; }
  }
`;
export function chatContextReference(item) {
  return Object.fromEntries(fields.map((key) => [key, item[key] ?? null]));
}
export function chatContextSelection(source, sequence, start, end) {
  if (!source) throw new Error("Choose a previous chat.");
  const selection = { source_thread_id: source };
  if (sequence) selection.message_sequence = Number(sequence);
  if (sequence && (!Number.isSafeInteger(selection.message_sequence) || selection.message_sequence < 1)) throw new Error("Choose an available message.");
  if (start !== "" || end !== "") {
    const first = Number(start), last = Number(end);
    if (!sequence || start === "" || end === "" || !Number.isSafeInteger(first) || !Number.isSafeInteger(last) || first < 0 || last <= first || last > 1048576) {
      throw new Error("Choose one message and both code-point offsets (start included, end excluded).");
    }
    selection.start_char = first; selection.end_char = last;
  }
  return selection;
}

export class ChatContextController {
  constructor(panel) {
    this.panel = panel; this.items = new Map(); this.revisions = new Map();
    this.epoch = 0; this.owner = null; this.scope = null; this.busy = false;
  }
  reset() { this.items.clear(); this.revisions.clear(); this.epoch++; this.scope = null; this.busy = false; }
  current() { return this.items.get(this.panel._selectedThreadId) || []; }
  revision(id) { return this.revisions.get(id) || 0; }
  setItems(id, items) { this.items.set(id, items); this.revisions.set(id, this.revision(id) + 1); }
  locked() { return Boolean(this.panel._promptMutationForThread(this.panel._selectedThreadId)); }
  snapshot() {
    if (this.current().some((item) => item.stale)) throw new Error("Review or remove previous chat context before sending.");
    return this.current().map(chatContextReference);
  }
  clearCaptured(id, references, revision) {
    if (this.revision(id) === revision && JSON.stringify((this.items.get(id) || []).map(chatContextReference)) === JSON.stringify(references)) {
      this.setItems(id, []);
    }
    this.render();
  }
  staleCaptured(id, revision) {
    if (this.revision(id) === revision) this.setItems(id, (this.items.get(id) || []).map((item) => ({ ...item, stale: true })));
    this.render();
  }
  button(label, action) {
    const el = document.createElement("button"); el.type = "button"; el.className = "composer-limits-button"; el.textContent = label;
    el.addEventListener("click", () => { if (!this.busy && !this.locked()) void action(); }); return el;
  }
  status(text) { const el = this.panel.shadowRoot.querySelector("[data-chat-context-status]"); if (el) el.textContent = text; }
  error(error) {
    const code = this.panel._bridgeErrorCode(error);
    return ({ chat_context_deleted: "Source chat was deleted. Remove its context.", chat_context_expired: "Source message is no longer retained. Remove it or choose another message.", chat_context_changed: "Source changed. Refresh and review the context before sending.", chat_context_inaccessible: "Source or destination is inaccessible. Review access or remove the context.", chat_context_empty: "No retained public messages in this chat.", chat_context_limit_exceeded: "Context is too large. Choose one message or a smaller excerpt.", chat_context_range_unavailable: "These code-point offsets are no longer available. Review the message and range.", chat_context_destination_unavailable: "Choose a different ordinary destination chat." })[code] || "Previous chat context could not be loaded. Check the source and retry.";
  }
  render() {
    const p = this.panel, root = p.shadowRoot.getElementById("chat-context"); if (!root) return;
    const owner = p._hass?.user?.id || "";
    if (owner !== this.owner) { this.reset(); this.owner = owner; }
    const scope = `${owner}:${p._selectedThreadId || ""}`;
    root.hidden = !owner || !p._config?.capabilities?.includes("chat_context_v1") || !p._selectedThreadId || p._activeThread?.schedule_eligible === false;
    if (scope !== this.scope) { this.scope = scope; this.epoch++; this.busy = false; root.replaceChildren(); }
    if (root.hidden) return;
    if (!root.firstChild) this.build(root);
    const list = root.querySelector("[data-chat-context-items]");
    const key = JSON.stringify(this.current());
    if (list.dataset.key !== key) {
      list.replaceChildren();
      this.current().forEach((item, index) => {
        const detail = document.createElement("details"); detail.open = true;
        const label = document.createElement("summary"); label.textContent = `${item.title} · ${item.message_sequence ? "Message/excerpt" : "Retained chat"}${item.stale ? " · Review required" : ""}`;
        const pre = document.createElement("pre"); pre.textContent = item.text;
        pre.style.cssText = "max-height:16rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere";
        detail.append(label, pre, this.button("Refresh and review", () => this.refresh(index)), this.button("Remove chat context", () => {
          this.setItems(p._selectedThreadId, this.current().filter((_, i) => i !== index)); this.render();
        })); list.append(detail);
      }); list.dataset.key = key;
    }
    root.querySelectorAll("button,input,select").forEach((el) => { el.disabled = this.busy || this.locked(); });
  }
  build(root) {
    const p = this.panel;
    const summary = document.createElement("summary"); summary.textContent = "Attach previous chat context";
    const help = document.createElement("p"); help.className = "row-meta";
    help.textContent = "Inspect the exact attributed text before sending. Only retained public messages are shared; no attachments or hidden context. Context stays in this visit. Optional offsets count Unicode code points from zero, with the end excluded.";
    const controls = document.createElement("div"); controls.className = "row-actions";
    for (const [name, label, type] of [["filter", "Find source chat", "text"], ["source", "Previous chat", "select"], ["message", "Message", "select"], ["start", "Start code point (optional)", "number"], ["end", "End code point (optional)", "number"]]) {
      const wrapper = document.createElement("label"); wrapper.className = "composer-utility";
      const text = document.createElement("span"); text.className = "composer-utility-label"; text.textContent = label;
      const input = document.createElement(type === "select" ? "select" : "input"); input.dataset.chatContextField = name;
      input.setAttribute("aria-label", label);
      if (type !== "select") input.type = type;
      input.style.cssText = "min-width:0;max-width:100%";
      if (type === "number") { input.min = "0"; input.step = "1"; input.style.width = "8rem"; }
      if (type === "select") { input.className = "compact-select"; const shell = document.createElement("span"); shell.className = "composer-select"; shell.append(input); wrapper.append(text, shell); }
      else wrapper.append(text, input);
      controls.append(wrapper);
    }
    const status = document.createElement("p"); status.dataset.chatContextStatus = ""; status.setAttribute("role", "status"); status.className = "row-meta";
    const items = document.createElement("div"); items.dataset.chatContextItems = "";
    root.append(summary, help, controls, this.button("Browse messages", () => this.browse()), this.button("Inspect and attach", () => this.attach()), status, items);
    const source = root.querySelector('[data-chat-context-field="source"]');
    const filter = root.querySelector('[data-chat-context-field="filter"]');
    const populate = () => {
      const old = source.value; source.replaceChildren(new Option("Choose a chat", ""));
      const matches = (p._threads || []).filter((chat) => chat.thread_id !== p._selectedThreadId && String(chat.title).toLocaleLowerCase().includes(filter.value.toLocaleLowerCase()));
      for (const chat of matches.slice(0, 100)) source.append(new Option(chat.title, chat.thread_id));
      source.value = old; if (!source.value) source.value = "";
      this.status(matches.length > 100 ? "First 100 chats shown. Refine the source title to find another chat." : matches.length ? "" : "No other available chats.");
    };
    filter.addEventListener("input", populate); populate();
    source.addEventListener("change", () => { this.epoch++; this.messageOptions([]); });
    this.messageOptions([]);
  }
  field(name) { return this.panel.shadowRoot.querySelector(`[data-chat-context-field="${name}"]`); }
  messageOptions(messages, append = false) {
    const el = this.field("message"); if (!append) el.replaceChildren(new Option("Whole retained chat (bounded)", ""));
    for (const item of messages) el.append(new Option(`${item.role} · ${item.excerpt}`, String(item.message_sequence)));
    // Keep the browser picker bounded even across a very long retained chat.
    while (el.options.length > 201) el.remove(1);
  }
  async request(command, payload, apply) {
    const p = this.panel, threadId = p._selectedThreadId, owner = p._hass?.user?.id, epoch = ++this.epoch;
    this.busy = true; this.status("Loading previous chat context…"); this.render();
    try {
      const value = await p._callWS(command, { thread_id: threadId, ...payload });
      if (epoch !== this.epoch || threadId !== p._selectedThreadId || owner !== p._hass?.user?.id) return;
      this.status(""); apply(value);
    } catch (error) {
      if (epoch === this.epoch && owner === p._hass?.user?.id && threadId === p._selectedThreadId) this.status(this.error(error));
    } finally { if (epoch === this.epoch) { this.busy = false; this.render(); } }
  }
  async browse(before = null) {
    const source = this.field("source").value;
    if (!source) { this.status("Choose a previous chat."); return; }
    await this.request("chat_context", { source_thread_id: source, ...(before ? { before_sequence: before } : {}) }, (value) => {
      this.messageOptions(value.messages || [], Boolean(before));
      this.status(value.complete ? "Retained public messages only." : "Earlier messages may be outside retained history.");
      const root = this.panel.shadowRoot.getElementById("chat-context"); root.querySelector("[data-chat-context-more]")?.remove();
      if (value.has_more) { const more = this.button("Load earlier messages", () => this.browse(value.next_before_sequence)); more.dataset.chatContextMore = ""; root.append(more); }
    });
  }
  room(text, replacing = -1) {
    const workspace = this.panel._workspaceContext?.current() || [];
    const items = this.current().filter((_, i) => i !== replacing);
    if (items.length + workspace.length >= 8 || items.reduce((n, item) => n + bytes(item.text), bytes(text)) + workspace.reduce((n, item) => n + bytes(item.text) + bytes(item.path || "") + 160, 0) > 96 * 1024) {
      throw Object.assign(new Error("Context is too large."), { code: "chat_context_limit_exceeded" });
    }
  }
  async attach() {
    if (this.busy || this.locked()) return;
    let selection;
    try { selection = chatContextSelection(...["source", "message", "start", "end"].map((name) => this.field(name).value)); }
    catch (error) { this.status(error.message); return; }
    await this.request("read_chat_context", selection, (value) => {
      if (typeof value.text !== "string") throw new Error("Invalid preview");
      this.room(value.text); this.setItems(this.panel._selectedThreadId, [...this.current(), { ...value, stale: false }]);
    });
  }
  async refresh(index) {
    const item = this.current()[index]; if (!item || this.busy || this.locked()) return;
    const selection = Object.fromEntries(fields.slice(0, -1).filter((key) => item[key] !== null && item[key] !== undefined).map((key) => [key, item[key]]));
    await this.request("read_chat_context", selection, (value) => {
      if (typeof value.text !== "string") throw new Error("Invalid preview");
      this.room(value.text, index); this.setItems(this.panel._selectedThreadId, this.current().map((other, i) => i === index ? { ...value, stale: false } : other));
    });
  }
}
