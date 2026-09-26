import { authenticatedChatUrl } from "./chat-resources.js";
import { normalizeDesktopError } from "./desktop-features.js";

export const chatMenuCss = `
  .chat-context-menu { position:fixed; z-index:70; width:min(348px,calc(100vw - 16px)); max-height:calc(100dvh - 16px); overflow:auto; padding:6px; border:1px solid var(--border-color); border-radius:14px; background:var(--surface-bg); color:var(--text-color); box-shadow:0 8px 28px #0002; }
  .chat-context-menu[hidden], .chat-menu-dialog[hidden] { display:none; }
  .chat-context-menu button { display:flex; align-items:center; gap:12px; width:100%; min-height:40px; border:0; padding:9px 12px; border-radius:7px; background:transparent; color:inherit; font:inherit; font-weight:400; text-align:left; }
  .chat-context-menu button:hover, .chat-context-menu button:focus-visible { background:var(--surface-muted); }
  .chat-context-menu button:focus-visible { outline:2px solid var(--accent-color); outline-offset:-2px; }
  .chat-context-menu button:disabled { opacity:.55; cursor:default; }
  .chat-context-menu button[hidden] { display:none; }
  .chat-context-menu .chat-menu-icon { display:flex; width:20px; flex:none; }
  .chat-context-menu svg { width:20px; height:20px; fill:none; stroke:currentColor; stroke-width:1.75; stroke-linecap:round; stroke-linejoin:round; }
  .chat-menu-label { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chat-menu-shortcut { color:var(--muted-color); font-size:12px; white-space:nowrap; }
  .chat-menu-divider { border:0; border-top:1px solid var(--border-color); margin:6px -6px; }
  .chat-menu-page { animation:chat-menu-expand 130ms ease-out; }
  .chat-menu-root { animation:none; }
  .chat-menu-submenu { position:fixed; z-index:71; width:min(300px,calc(100vw - 16px)); max-height:calc(100dvh - 16px); overflow:auto; padding:6px; border:1px solid var(--border-color); border-radius:12px; background:var(--surface-bg); box-shadow:0 8px 28px #0002; }
  @media (max-width:640px), (pointer:coarse) { .chat-context-menu.has-submenu .chat-menu-root { display:none; } .chat-menu-submenu { position:static; width:auto; padding:0; border:0; box-shadow:none; } .chat-context-menu button { min-height:44px; } .chat-menu-shortcut { display:none; } }
  .chat-menu-status { margin:6px 12px; color:var(--muted-color); font-size:13px; line-height:1.4; overflow-wrap:anywhere; }
  .chat-menu-status:empty { display:none; }
  .chat-menu-dialog { position:fixed; inset:0; z-index:80; display:grid; place-items:center; padding:16px; background:#0005; }
  .chat-menu-dialog-card { width:min(420px,100%); max-height:calc(100dvh - 32px); overflow:auto; padding:22px; border:1px solid var(--border-color); border-radius:18px; background:var(--surface-bg); box-shadow:0 12px 40px #0003; }
  .chat-menu-dialog-card h2 { margin:0 0 18px; font-size:20px; }
  .chat-menu-dialog-card label { display:grid; gap:8px; }
  .chat-menu-dialog-card input { width:100%; box-sizing:border-box; padding:12px; border:1px solid var(--border-color); border-radius:9px; color:var(--text-color); background:var(--surface-bg); font:inherit; }
  .chat-menu-dialog-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:18px; }
  .chat-menu-dialog-actions button { min-height:44px; }
  .chat-move-files { max-height:min(36dvh,320px); overflow:auto; margin:16px 0 0; padding:12px; border:1px solid var(--border-color); border-radius:10px; }
  .chat-move-files legend { padding:0 6px; font-weight:600; }
  .chat-move-files label { display:flex; align-items:start; gap:10px; min-height:44px; padding:8px 0; }
  .chat-move-files input[type="checkbox"] { width:20px; height:20px; flex:none; margin:2px 0 0; padding:0; accent-color:var(--accent-color); }
  .chat-move-file-name { display:block; overflow-wrap:anywhere; }
  .chat-move-file-path { display:block; color:var(--muted-color); font-size:12px; overflow-wrap:anywhere; }
  .chat-row.unread .thread-name { font-weight:650; }
  .chat-row.unread .thread-name::after { content:' •'; color:var(--accent-color); }
  .chat-navigation-group { margin:8px 0; }
  .chat-navigation-group summary { cursor:pointer; min-height:44px; display:flex; align-items:center; gap:8px; padding:8px 12px; font-weight:600; }
  .chat-navigation-group summary svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.75; stroke-linecap:round; stroke-linejoin:round; }
  @keyframes chat-menu-expand { from { opacity:.65; transform:translateX(5px); } to { opacity:1; transform:translateX(0); } }
  @media (pointer:coarse) { .chat-context-menu button { min-height:44px; } .chat-menu-shortcut { display:none; } }
  @media (prefers-reduced-motion:reduce) { .chat-menu-page { animation:none; } }
  :host([data-motion="reduced"]) .chat-menu-page { animation:none; }
`;

/** Copy only durable user/assistant messages, never raw tool or interaction data. */
export function conversationCopy(events, { markdown = false, renderText = (text) => text } = {}) {
  const messages = [];
  let characters = 0;
  for (const event of events) {
    const payload = event?.payload;
    if (!payload || typeof payload.text !== "string") continue;
    const role = event.event_type === "message.created" && (!payload.role || payload.role === "user") ? "You"
      : event.event_type === "message.completed" && (!payload.role || payload.role === "assistant") ? "Codex" : null;
    if (!role) continue;
    characters += payload.text.length + 16;
    if (characters > 2_000_000) throw new Error("This conversation is too large to copy safely.");
    messages.push(`${markdown ? `## ${role}` : `${role}:`}\n${markdown ? payload.text : renderText(payload.text)}`);
  }
  return messages.join("\n\n");
}

const control = (label, action, icon, shortcut = "", value = null) => ({ label, action, icon, shortcut, value });

/** One action owner shared by right-click, sidebar ellipsis and the chat header. */
export class ChatContextMenu {
  constructor(panel, icons) {
    this.panel = panel;
    this.icons = icons;
    this.sections = [];
    this.sectionsLoaded = false;
    this.busy = new Set();
    this.uncertain = new Set();
    this.generation = 0;
    this.point = { x: 8, y: 8 };
    this.menu = document.createElement("div");
    this.menu.className = "chat-context-menu";
    this.menu.id = "chat-context-menu";
    this.menu.hidden = true;
    this.menu.setAttribute("role", "menu");
    this.dialog = document.createElement("div");
    this.dialog.className = "chat-menu-dialog";
    this.dialog.hidden = true;
    panel.shadowRoot.append(this.menu, this.dialog);
    this.menu.addEventListener("click", (event) => {
      event.stopPropagation();
      const item = event.target.closest("button[data-chat-action]");
      if (item && !item.disabled) void this.perform(item.dataset.chatAction, item._chatValue);
    });
    this.menu.addEventListener("mouseover", (event) => {
      const item = event.target.closest('button[data-chat-action="submenu"]');
      if (item && !item.disabled && !window.matchMedia?.("(pointer:coarse)").matches) {
        window.clearTimeout(this.hoverTimer);
        this.hoverTimer = window.setTimeout(() => {
          if (this.menu.hidden || !item.isConnected) return;
          this.page = item._chatValue; this.notice = ""; this.render({ preserveFocus: true });
        }, 220);
      } else if (event.target.closest(".chat-menu-root button") && this.page) {
        window.clearTimeout(this.hoverTimer);
        this.hoverTimer = window.setTimeout(() => { this.page = null; this.render({ preserveFocus: true }); }, 220);
      }
    });
    this.menu.addEventListener("mouseout", (event) => {
      if (event.target.closest('button[data-chat-action="submenu"]')?.contains(event.relatedTarget)) return;
      window.clearTimeout(this.hoverTimer);
    });
    this.outside = (event) => {
      const path = event.composedPath();
      if (!path.includes(this.menu) && !path.includes(this.dialog) && !path.includes(this.trigger)) this.close();
    };
    this.resize = () => this.close();
  }

  get supported() { return this.panel._config?.capabilities?.includes("chat_operations_v1") === true; }
  get compact() { return window.innerWidth <= 640 || window.matchMedia?.("(pointer:coarse)")?.matches === true; }
  thread(id = this.threadId) { return this.panel._threads.find((thread) => thread.thread_id === id) || (this.panel._activeThread?.thread_id === id ? this.panel._activeThread : null); }

  sync() {
    if (this.menu.hidden) return;
    this.syncTrigger();
    if (!this.thread()) { this.close(); return; }
    if (!this.supported && ["project", "section", "fork", "manage-sections", "manage-section"].includes(this.page)) this.page = null;
    const key = JSON.stringify([this.thread(), this.supported, this.sections, this.page, this.busy.has(this.threadId), this.uncertain.has(this.threadId)]);
    if (key !== this.projectionKey) this.render({ preserveFocus: true });
  }

  syncTrigger() {
    if (this.header) return;
    const current = [...this.panel.shadowRoot.querySelectorAll(".thread-actions-toggle")].find((button) => button.dataset.threadId === this.threadId);
    if (current) {
      this.trigger = current;
      current.setAttribute("aria-expanded", String(!this.menu.hidden));
      current.setAttribute("aria-controls", this.menu.id);
      current.closest(".chat-row")?.classList.toggle("actions-open", !this.menu.hidden);
    }
  }

  async loadSections() {
    if (this.sectionsLoading) return this.sectionsLoading;
    this.sectionsAttempted = true;
    this.sectionsLoading = (async () => {
      const response = await this.panel._callWS("list_chat_sections");
      this.sections = (Array.isArray(response) ? response : response?.sections || []).filter((item) => item && typeof item.section_id === "string" && typeof item.name === "string" && Number.isSafeInteger(item.revision)).slice(0, 100);
      this.sectionsLoaded = true;
      this.panel._renderedNavigationKey = null;
      if (this.panel.isConnected) this.panel._renderNavigationSections();
    })();
    try { await this.sectionsLoading; } finally { this.sectionsLoading = null; }
  }

  connect() {
    document.addEventListener("pointerdown", this.outside, true);
    window.addEventListener("resize", this.resize);
  }

  disconnect() {
    this.close();
    this.closeDialog();
    document.removeEventListener("pointerdown", this.outside, true);
    window.removeEventListener("resize", this.resize);
  }

  async show(threadId, trigger, point = null, { header = false } = {}) {
    if (!this.thread(threadId)) return;
    if (!this.menu.hidden && this.threadId === threadId && trigger === this.trigger && !point) { this.close({ focus: true }); return; }
    this.close();
    this.panel._hideTooltip();
    this.panel._closeRailMenus();
    this.threadId = threadId;
    this.trigger = trigger;
    this.header = header;
    this.page = null;
    this.notice = this.uncertain.has(threadId) ? "A previous change could not be verified. Refresh the chat list and check the result before trying again." : "";
    const rect = trigger?.getBoundingClientRect();
    this.point = point || { x: rect?.left || 8, y: rect?.bottom || 8 };
    this.menu.hidden = false;
    this.trigger?.setAttribute("aria-expanded", "true");
    this.trigger?.closest(".chat-row")?.classList.add("actions-open");
    this.trigger?.setAttribute("aria-controls", this.menu.id);
    this.render();
    this.menu.querySelector("button:not(:disabled)")?.focus();
    const generation = this.generation;
    if (this.supported) {
      try {
        await this.loadSections();
      } catch {
        if (generation === this.generation) this.notice = "Sections could not be loaded. Refresh to try again.";
      }
      if (generation === this.generation && !this.menu.hidden) this.render({ preserveFocus: true });
    }
  }

  close({ focus = false } = {}) {
    window.clearTimeout(this.hoverTimer);
    this.generation += 1;
    this.menu.hidden = true;
    this.trigger?.setAttribute("aria-expanded", "false");
    this.trigger?.closest(".chat-row")?.classList.remove("actions-open");
    if (focus) this.returnFocus();
  }

  returnFocus() {
    const replacement = [...this.panel.shadowRoot.querySelectorAll(".thread-actions-toggle")].find((button) => button.dataset.threadId === this.threadId);
    (this.trigger?.isConnected ? this.trigger : replacement || this.panel.shadowRoot.getElementById("chat-menu-button"))?.focus();
  }

  entries(page = this.page) {
    const thread = this.thread();
    if (!thread) return [];
    if (page === "project") return [
      control("Back", "back", "chevronLeft"), null,
      ...this.panel._projects.filter((project) => project.kind === "project" && !project.archived_at && project.project_id !== thread.project_id)
        .map((project) => control(project.name || "Direct chats", "move", "folder", "", project.project_id)),
    ];
    if (page === "section") return [
      control("Back", "back", "chevronLeft"), null,
      control("No section", "section", "chat", "", null),
      ...this.sections.map((section) => control(section.name, "section", "menu", "", section.section_id)), null,
      control("New section…", "create-section", "plus"),
      ...(this.sections.length ? [control("Manage sections…", "manage-sections", "settings")] : []),
      ...(thread.section_id && this.sections.some((section) => section.section_id === thread.section_id) ? [
        control("Rename this section…", "rename-section", "edit", "", thread.section_id),
        control("Remove this section…", "remove-section", "trash", "", thread.section_id),
      ] : []),
    ];
    if (page === "manage-sections") return [control("Back", "back", "chevronLeft"), null,
      ...this.sections.map((section) => control(section.name, "manage-section", "menu", "", section.section_id))];
    if (page === "manage-section") return [control("Back", "back", "chevronLeft"), null,
      control("Rename section…", "rename-section", "edit", "", this.managedSection),
      control("Remove section…", "remove-section", "trash", "", this.managedSection)];
    if (page === "copy") return [control("Back", "back", "chevronLeft"), null,
      control("Chat title", "copy-title", "chat"), control("Chat link", "copy-link", "external"),
      control("Conversation text", "copy-text", "copy"), control("Conversation Markdown", "copy-markdown", "file")];
    if (page === "fork") return [control("Back", "back", "chevronLeft"), null,
      control("Fork conversation", "fork", "pullRequest")];
    return [
      control("Rename", "rename", "edit", "Alt+Ctrl+R"),
      ...(this.supported ? [control(thread.pinned ? "Unpin" : "Pin", "pin", "pin", "Alt+Ctrl+P"),
        control(thread.unread ? "Mark as read" : "Mark as unread", "unread", "eye", "Ctrl+Shift+U")] : []),
      control(thread.archived_at ? "Restore" : "Archive", "archive", thread.archived_at ? "restore" : "archive", "Ctrl+Shift+A"),
      control("Permanently delete", "delete", "trash"), null,
      ...(this.supported ? [control("Project", "submenu", "folder", "", "project"), control("Section", "submenu", "menu", "", "section"), null] : []),
      control("Share", "copy-link", "upload"), control("Copy", "submenu", "copy", "", "copy"), null,
      ...(this.supported ? [control("Fork", "submenu", "pullRequest", "", "fork"), null] : []),
      control("Open in new window", "open", "external"),
      ...(this.header ? [null, control("Chat settings", "settings", "settings"), control("Refresh", "refresh", "refresh")] : []),
      ...((this.uncertain.has(this.threadId) || this.notice) && !this.header ? [null, control("Refresh", "refresh", "refresh")] : []),
    ];
  }

  render({ preserveFocus = false } = {}) {
    this.projectionKey = JSON.stringify([this.thread(), this.supported, this.sections, this.page, this.busy.has(this.threadId), this.uncertain.has(this.threadId)]);
    const old = this.menu.contains(this.panel.shadowRoot.activeElement) ? this.panel.shadowRoot.activeElement : null;
    const selected = old ? [old.dataset.chatAction, old._chatValue] : null;
    this.menu.replaceChildren();
    this.menu.setAttribute("aria-label", `Actions for ${this.thread()?.title || "chat"}`);
    this.menu.setAttribute("aria-busy", String(this.busy.has(this.threadId)));
    this.menu.classList.toggle("has-submenu", Boolean(this.page));
    const buildPage = (entries, nested = false) => {
      const page = document.createElement("div");
      page.className = nested ? "chat-menu-page chat-menu-submenu" : "chat-menu-page chat-menu-root";
      if (nested) { page.setAttribute("role", "menu"); page.setAttribute("aria-label", `${this.page} options`); }
      for (const entry of entries) {
      if (!entry) { const line = document.createElement("hr"); line.className = "chat-menu-divider"; line.setAttribute("role", "separator"); page.append(line); continue; }
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.chatAction = entry.action;
      button._chatValue = entry.value;
      button.setAttribute("role", "menuitem");
      button.tabIndex = -1;
      button.disabled = this.busy.has(this.threadId) || (this.uncertain.has(this.threadId) && !["back", "refresh", "open", "copy-title", "copy-link", "copy-text", "copy-markdown", "submenu"].includes(entry.action));
      if (["move", "fork"].includes(entry.action) && this.panel._runActivityForThread(this.thread()).busy) button.disabled = true;
      if (entry.action === "section" && !this.sectionsLoaded) button.disabled = true;
      if (entry.action === "back") button.hidden = !this.compact;
      if (entry.action === "submenu") { button.setAttribute("aria-haspopup", "menu"); button.setAttribute("aria-expanded", String(this.page === entry.value || (entry.value === "section" && this.page?.startsWith("manage-section")))); }
      const icon = document.createElement("span"); icon.className = "chat-menu-icon";
      // The icon source is the panel's fixed trusted catalogue, never server content.
      this.panel._setTrustedButtonContent(icon, this.icons[entry.icon] || this.icons.chat);
      const label = document.createElement("span"); label.className = "chat-menu-label"; label.textContent = entry.label;
      button.append(icon, label);
      if (entry.shortcut) { const hint = document.createElement("span"); hint.className = "chat-menu-shortcut"; hint.textContent = entry.shortcut; button.append(hint); }
      if (entry.shortcut) button.setAttribute("aria-keyshortcuts", entry.shortcut.replace("Ctrl", "Control"));
      if (entry.action === "submenu") { const arrow = document.createElement("span"); this.panel._setTrustedButtonContent(arrow, this.icons.chevronRight); button.append(arrow); }
      page.append(button);
      }
      return page;
    };
    this.menu.append(buildPage(this.entries(null)));
    const submenu = this.page ? buildPage(this.entries(), true) : null;
    if (submenu) this.menu.append(submenu);
    const status = document.createElement("p"); status.className = "chat-menu-status"; status.setAttribute("role", "status"); status.textContent = this.notice || (this.page === "project" ? "Review the move and choose any project files to copy. Originals are retained." : this.page === "fork" ? "Creates a new conversation in this project's existing workspace, using the same signed-in account." : "");
    (submenu || this.menu).append(status);
    this.position();
    if (preserveFocus && selected) {
      ([...this.menu.querySelectorAll("button")].find((button) => !button.hidden && !button.disabled && button.dataset.chatAction === selected[0] && button._chatValue === selected[1]) || this.menu.querySelector("button:not(:disabled):not([hidden])"))?.focus();
    }
  }

  position() {
    const view = window.visualViewport;
    const width = view?.width || window.innerWidth;
    const height = view?.height || window.innerHeight;
    const left = view?.offsetLeft || 0, top = view?.offsetTop || 0;
    const rect = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(left + 8, Math.min(this.point.x, left + width - rect.width - 8))}px`;
    this.menu.style.top = `${Math.max(top + 8, Math.min(this.point.y, top + height - rect.height - 8))}px`;
    this.menu.style.maxHeight = `${Math.max(44, height - 16)}px`;
    const submenu = this.menu.querySelector(".chat-menu-submenu");
    if (submenu && !this.compact) {
      const anchorPage = this.page.startsWith("manage-section") ? "section" : this.page;
      const anchor = [...this.menu.querySelectorAll('.chat-menu-root [data-chat-action="submenu"]')].find((button) => button._chatValue === anchorPage)?.getBoundingClientRect() || rect;
      const parent = this.menu.getBoundingClientRect();
      const child = submenu.getBoundingClientRect();
      const x = parent.right + child.width + 8 <= left + width ? parent.right + 4 : parent.left - child.width - 4;
      submenu.style.left = `${Math.max(left + 8, Math.min(x, left + width - child.width - 8))}px`;
      submenu.style.top = `${Math.max(top + 8, Math.min(anchor.top, top + height - child.height - 8))}px`;
      submenu.style.maxHeight = `${Math.max(44, height - 16)}px`;
    }
  }

  handleKey(event) {
    if (!this.dialog.hidden) {
      if (event.key === "Escape" && !this.dialogBusy) { event.preventDefault(); this.closeDialog({ focus: true }); return true; }
      if (event.key === "Tab") { this.trapDialog(event); return true; }
      return true;
    }
    if (!this.menu.hidden) {
      const current = this.panel.shadowRoot.activeElement;
      const container = current?.closest(".chat-menu-submenu") || this.menu.querySelector(".chat-menu-root");
      const items = [...container.querySelectorAll("button:not(:disabled):not([hidden])")];
      if (event.key === "Escape" || (event.key === "ArrowLeft" && this.page)) { event.preventDefault(); if (this.page) void this.perform("back"); else this.close({ focus: true }); return true; }
      if (event.key === "Tab") { this.close({ focus: true }); return false; }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault(); const index = items.indexOf(current);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus(); return true;
      }
      if (event.key === "ArrowRight" && current?.dataset.chatAction === "submenu") { event.preventDefault(); void this.perform("submenu", current._chatValue); return true; }
      if (["Enter", " "].includes(event.key) && current?.dataset.chatAction) { event.preventDefault(); if (!current.disabled) void this.perform(current.dataset.chatAction, current._chatValue); return true; }
      return false;
    }
    const target = event.target;
    if (this.panel._activeDestination !== "chats" || !this.panel._selectedThreadId || this.panel._pendingDeletion || this.panel._hostAccessDialog || this.panel._appMenuOpen || this.panel._showThreadForm || this.panel._showProjectForm || target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="dialog"],.xterm')) return false;
    const key = event.key.toLowerCase();
    const action = event.ctrlKey && event.altKey && !event.shiftKey && !event.metaKey ? key === "r" ? "rename" : key === "p" && this.supported ? "pin" : null
      : event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey ? key === "u" && this.supported ? "unread" : key === "a" ? "archive" : null : null;
    if (!action) return false;
    event.preventDefault(); this.threadId = this.panel._selectedThreadId; this.trigger = target;
    const rect = target.getBoundingClientRect(); this.point = { x: rect.left, y: rect.bottom };
    void this.perform(action); return true;
  }

  async perform(action, value = null) {
    if (!this.dialog.hidden) return;
    const thread = this.thread();
    if (!thread) { this.close(); return; }
    if (this.busy.has(thread.thread_id)) return;
    if (action === "submenu" || action === "back" || action === "manage-sections" || action === "manage-section") {
      const previous = this.page;
      if (["manage-sections", "manage-section"].includes(action) && !this.supported) return;
      if (action === "submenu" && ["project", "section", "fork"].includes(value) && !this.supported) return;
      this.page = action === "back" ? this.page === "manage-section" ? "manage-sections" : this.page === "manage-sections" ? "section" : null : action === "submenu" ? value : action;
      if (action === "manage-section") this.managedSection = value;
      this.notice = ""; this.render();
      if (this.page) this.menu.querySelector(".chat-menu-submenu button:not(:disabled):not([hidden])")?.focus();
      else [...this.menu.querySelectorAll('button[data-chat-action="submenu"]')].find((button) => button._chatValue === previous)?.focus();
      return;
    }
    if (["pin", "unread", "section", "move", "fork", "create-section", "rename-section", "remove-section"].includes(action) && !this.supported) return;
    if (["move", "fork"].includes(action) && this.panel._runActivityForThread(thread).busy) return;
    if (action === "move" && !this.panel._projects.some((project) => project.project_id === value && project.kind === "project" && !project.archived_at && project.project_id !== thread.project_id)) return;
    if (this.uncertain.has(thread.thread_id) && !["refresh", "open", "copy-title", "copy-link", "copy-text", "copy-markdown"].includes(action)) return;
    if (action === "rename") { this.openDialog("Rename chat", "Chat title", thread.title || "", async (title) => this.mutate(thread, "update_thread", { title })); return; }
    if (action === "move") { await this.openMoveDialog(thread, value); return; }
    if (action === "create-section") { this.openDialog("New section", "Section name", "", async (name) => {
      if (!this.supported) throw new Error("Chat sections are no longer available. Refresh before trying again.");
      const section = await this.panel._callWS("create_chat_section", { name });
      this.sections.push(section); this.sectionsLoaded = true;
      await this.mutate(thread, "update_thread", { section_id: section.section_id, navigation_revision: thread.navigation_revision });
    }); return; }
    if (action === "rename-section" || action === "remove-section") {
      const section = this.sections.find((item) => item.section_id === value); if (!section) return;
      this.openDialog(action === "remove-section" ? "Remove section" : "Rename section", action === "remove-section" ? null : "Section name", section.name, async (name) => {
        if (!this.supported) throw new Error("Chat sections are no longer available. Refresh before trying again.");
        if (action === "remove-section") {
          await this.panel._callWS("delete_chat_section", { section_id: section.section_id, revision: section.revision });
          this.sections = this.sections.filter((item) => item.section_id !== section.section_id);
        } else {
          const updated = await this.panel._callWS("update_chat_section", { section_id: section.section_id, revision: section.revision, name });
          this.sections = this.sections.map((item) => item.section_id === section.section_id ? updated : item);
        }
        await this.panel._loadThreads(); this.panel._renderedNavigationKey = null; this.panel._render();
      }, { description: action === "remove-section" ? "Chats in this section stay available. Only the grouping is removed." : "", submit: action === "remove-section" ? "Remove section" : "Save" }); return;
    }
    if (action === "settings") { this.close(); this.panel._openThreadFormForEdit(thread.thread_id); return; }
    if (action === "delete") { const trigger = this.trigger; this.close(); await this.panel._deleteThread(thread.thread_id, trigger); return; }
    if (action === "archive") { this.busy.add(thread.thread_id); this.close(); try { await (thread.archived_at ? this.panel._restoreThread(thread.thread_id) : this.panel._archiveThread(thread.thread_id)); } finally { this.busy.delete(thread.thread_id); } return; }
    if (action === "open") { const url = authenticatedChatUrl(window.location, thread.thread_id); if (url) window.open(url, "_blank", "noopener,noreferrer"); this.close(); return; }
    const generation = this.generation;
    const isOwner = () => generation === this.generation && this.threadId === thread.thread_id && this.panel.isConnected;
    this.busy.add(thread.thread_id); this.render({ preserveFocus: true });
    try {
      if (action === "pin" || action === "unread" || action === "section") {
        const updates = action === "pin" ? { pinned: !thread.pinned } : action === "unread" ? { unread: !thread.unread } : { section_id: value };
        await this.mutate(thread, "update_thread", { ...updates, navigation_revision: thread.navigation_revision });
      } else if (action === "fork") {
        const fork = await this.panel._callWS("fork_thread", { thread_id: thread.thread_id });
        if (isOwner()) this.panel._adoptCreatedThread(fork);
        else { this.panel._threads = [fork, ...this.panel._threads.filter((item) => item.thread_id !== fork.thread_id)]; this.panel._render(); }
      } else if (action === "refresh") {
        await this.panel._loadProjects(); await this.panel._loadThreads();
        if (this.supported) await this.loadSections();
        this.uncertain.delete(thread.thread_id); this.panel._render();
      } else if (["copy-title", "copy-link", "copy-text", "copy-markdown"].includes(action)) {
        const text = action === "copy-title" ? thread.title || "Untitled chat" : action === "copy-link" ? authenticatedChatUrl(window.location, thread.thread_id)
          : conversationCopy(await this.panel._loadThreadEventHistory(thread.thread_id), { markdown: action === "copy-markdown", renderText: (value) => {
            const container = document.createElement("div"); this.panel._renderMessageBody(container, value); return [...container.querySelectorAll(".bubble-text,.code-text")].map((part) => part.textContent).join("\n");
          } });
        if (!text) throw new Error("There are no conversation messages to copy.");
        await this.panel._writeClipboardText(text);
        this.panel.shadowRoot.getElementById("share-status").textContent = action === "copy-link" ? "Chat link copied. Home Assistant sign-in is required." : "Copied to clipboard.";
      }
      if (isOwner()) this.close({ focus: action !== "fork" });
    } catch (error) {
      const writes = ["pin", "unread", "section", "move", "fork"];
      if (writes.includes(action) && !this.knownFailure(error)) this.uncertain.add(thread.thread_id);
      if (isOwner()) { this.notice = this.errorMessage(error, writes.includes(action)); this.menu.hidden = false; }
    } finally { this.busy.delete(thread.thread_id); if (isOwner() && !this.menu.hidden) this.render(); }
  }

  knownFailure(error) {
    const code = error?.code || error?.body?.code || error?.body?.detail?.code;
    return ["navigation_revision_conflict", "thread_busy", "provider_thread_unavailable", "workspace_copy_conflict", "workspace_boundary_error", "chat_operations_unavailable"].includes(code);
  }

  errorMessage(error, write = false) {
    return `${normalizeDesktopError(error) || "The action could not be completed."}${write ? " Refresh the chat list and check the result before trying again." : ""}`;
  }

  async mutate(thread, operation, fields) {
    const updated = await this.panel._callWS(operation, { thread_id: thread.thread_id, ...fields });
    this.panel._threads = this.panel._threads.map((item) => item.thread_id === thread.thread_id ? updated : item);
    if (this.panel._selectedThreadId === thread.thread_id) { this.panel._activeThread = updated; this.panel._selectedProjectId = updated.project_id; }
    this.panel._renderedNavigationKey = null;
    this.panel._clearError(); this.panel._render();
    return updated;
  }

  async markOpened(threadId) {
    const thread = this.thread(threadId);
    if (!this.supported || !thread?.unread || this.busy.has(threadId)) return;
    this.busy.add(threadId);
    try { await this.mutate(thread, "update_thread", { unread: false, navigation_revision: thread.navigation_revision }); }
    catch { /* A conflicting/opening read marker never blocks opening a conversation. */ }
    finally { this.busy.delete(threadId); }
  }

  async openMoveDialog(thread, projectId) {
    const destination = this.panel._projects.find((project) => project.project_id === projectId);
    const review = { threadId: thread.thread_id, sourceProjectId: thread.project_id, revision: thread.navigation_revision, projectId, destinationRoot: destination?.root_path, ready: false, writing: false, choices: [] };
    const localFailure = (message) => Object.assign(new Error(message), { code: "move_review_changed" });
    const controls = this.openDialog("Move chat", null, "", async () => {
      const current = this.thread(review.threadId);
      const target = this.panel._projects.find((project) => project.project_id === review.projectId);
      if (this.moveReview !== review || !review.ready) throw localFailure("The file review is no longer available. Refresh before trying again.");
      if (!this.supported) throw localFailure("Moving chats is no longer available. Refresh before trying again.");
      if (!current || !Number.isSafeInteger(review.revision) || review.revision < 1 || current.project_id !== review.sourceProjectId || current.navigation_revision !== review.revision
        || !target || target.kind !== "project" || target.archived_at || target.project_id === current.project_id || target.root_path !== review.destinationRoot) {
        throw localFailure("The chat or destination changed during this review. Refresh and review the move again.");
      }
      if (this.busy.has(review.threadId) || this.uncertain.has(review.threadId) || this.panel._runActivityForThread(current).busy || current.archived_at) {
        throw localFailure("The chat is busy or a previous change needs checking. Refresh before trying again.");
      }
      const selected = review.choices.filter((choice) => choice.input.checked).map((choice) => choice.id);
      if (selected.length > 100 || new Set(selected).size !== selected.length) throw localFailure("Choose no more than 100 project files.");
      review.writing = true;
      this.busy.add(review.threadId);
      for (const choice of review.choices) choice.input.disabled = true;
      try {
        await this.mutate(current, "move_thread_project", { project_id: review.projectId, navigation_revision: review.revision, workspace_artifact_ids: selected });
      } finally { this.busy.delete(review.threadId); }
    }, {
      description: `Move “${thread.title || "Untitled chat"}” to “${destination?.name || "Project"}”. Uploaded files, this chat's private generated images, captures and archives, and files copied by an earlier move are copied automatically. Ordinary project files are shared: select only the files you want to copy. Unselected files and originals stay in the source workspace.`,
      submit: "Move chat",
      uncertainOnFailure: (failure) => review.writing && !this.knownFailure(failure),
    });
    this.moveReview = review;
    controls.confirm.disabled = true;
    const files = document.createElement("fieldset"); files.className = "chat-move-files";
    const legend = document.createElement("legend"); legend.textContent = "Project files to copy (optional)"; files.append(legend);
    const status = document.createElement("p"); status.className = "chat-menu-status"; status.setAttribute("role", "status"); status.textContent = "Loading project files…"; files.append(status);
    controls.card.insertBefore(files, controls.error);
    controls.cancel.focus();
    try {
      const artifacts = await this.panel._callWS("list_artifacts", { thread_id: review.threadId });
      if (this.moveReview !== review || this.dialog.hidden || !this.panel.isConnected) return;
      if (!Array.isArray(artifacts)) throw new Error("Project files could not be loaded. Close this review and try again.");
      const ids = new Set();
      const candidates = artifacts.filter((artifact) => artifact?.source === "workspace" && artifact.copied_for_chat !== true);
      for (const artifact of candidates.slice(0, 100)) {
        if (typeof artifact.artifact_id !== "string" || !artifact.artifact_id || artifact.artifact_id.length > 128 || ids.has(artifact.artifact_id)
          || typeof artifact.filename !== "string" || !artifact.filename || typeof artifact.relative_path !== "string"
          || (artifact.copied_for_chat !== undefined && typeof artifact.copied_for_chat !== "boolean")) {
          throw new Error("Project files could not be verified. Close this review and refresh before trying again.");
        }
        ids.add(artifact.artifact_id);
        const label = document.createElement("label");
        const input = document.createElement("input"); input.type = "checkbox"; input.checked = false;
        const detail = document.createElement("span");
        const name = document.createElement("span"); name.className = "chat-move-file-name"; name.textContent = artifact.filename.slice(0, 255);
        const path = document.createElement("span"); path.className = "chat-move-file-path"; path.textContent = artifact.relative_path.slice(0, 1024);
        detail.append(name, path); label.append(input, detail); files.append(label);
        review.choices.push({ id: artifact.artifact_id, input });
      }
      status.textContent = candidates.length > 100 ? "Showing the first 100 project files. Files outside this list are not copied." : candidates.length ? "Project files are optional. Select only files you want to copy." : "There are no ordinary project files to select.";
      review.ready = true; controls.confirm.disabled = false;
    } catch {
      if (this.moveReview !== review || this.dialog.hidden || !this.panel.isConnected) return;
      status.textContent = "Project files could not be loaded or verified. Close this review and refresh before trying again.";
      controls.confirm.hidden = true; controls.cancel.textContent = "Close";
    }
  }

  openDialog(title, label, value, save, { description = "", submit = "Save", uncertainOnFailure = () => true } = {}) {
    this.closeDialog();
    this.close(); this.dialog.replaceChildren(); this.dialog.hidden = false;
    const ownerThreadId = this.threadId;
    const shell = this.panel.shadowRoot.querySelector(".shell"); if (shell) { shell.inert = true; shell.setAttribute("aria-hidden", "true"); }
    const card = document.createElement("form"); card.className = "chat-menu-dialog-card";
    card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true");
    const heading = document.createElement("h2"); heading.id = "chat-menu-dialog-title"; heading.textContent = title; card.setAttribute("aria-labelledby", heading.id); card.append(heading);
    let input;
    if (label) {
      const field = document.createElement("label"); field.textContent = label;
      input = document.createElement("input"); input.type = "text"; input.value = value; input.maxLength = label === "Chat title" ? 160 : 80; input.required = true; field.append(input); card.append(field);
    }
    if (description) { const text = document.createElement("p"); text.textContent = description; card.append(text); }
    const error = document.createElement("p"); error.setAttribute("role", "alert"); error.className = "chat-menu-status"; card.append(error);
    const actions = document.createElement("div"); actions.className = "chat-menu-dialog-actions";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Cancel";
    const confirm = document.createElement("button"); confirm.type = "submit"; confirm.textContent = submit;
    cancel.addEventListener("click", () => this.closeDialog({ focus: true })); actions.append(cancel, confirm); card.append(actions); this.dialog.append(card);
    this.dialog.addEventListener("click", (event) => event.stopPropagation(), { once: true });
    card.addEventListener("submit", async (event) => {
      event.preventDefault(); event.stopPropagation();
      if (this.dialogBusy || confirm.disabled || confirm.hidden || (input && !input.value.trim())) return;
      this.dialogBusy = true; confirm.disabled = true; cancel.disabled = true; if (input) input.disabled = true;
      let saved = false;
      try { await save(input?.value.trim()); saved = true; }
      catch (failure) { error.textContent = this.errorMessage(failure, true); confirm.hidden = true; cancel.textContent = "Close"; if (uncertainOnFailure(failure)) this.uncertain.add(ownerThreadId); }
      finally { this.dialogBusy = false; cancel.disabled = false; if (input) input.disabled = false; if (saved) this.closeDialog({ focus: true }); else cancel.focus(); }
    });
    queueMicrotask(() => { if (card.isConnected && !this.dialog.hidden) { (input || (confirm.disabled ? cancel : confirm)).focus(); input?.select(); } });
    return { card, confirm, cancel, error };
  }

  closeDialog({ focus = false } = {}) {
    if (this.dialogBusy) return;
    this.moveReview = null;
    this.dialog.hidden = true;
    const shell = this.panel.shadowRoot.querySelector(".shell");
    if (shell && !this.panel._pendingDeletion && !this.panel._hostAccessDialog) { shell.inert = false; shell.removeAttribute("aria-hidden"); }
    if (focus) this.returnFocus();
  }
  trapDialog(event) {
    const items = [...this.dialog.querySelectorAll("input:not(:disabled),button:not(:disabled):not([hidden])")];
    const active = this.panel.shadowRoot.activeElement;
    if (event.shiftKey && active === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
    else if (!event.shiftKey && active === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
  }

  isGrouped(thread) { return this.supported && !thread.archived_at && (thread.pinned || (thread.section_id && this.sections.some((section) => section.section_id === thread.section_id))); }
  renderNavigation() {
    let container = this.panel.shadowRoot.getElementById("chat-navigation-sections");
    if (!container) { container = document.createElement("div"); container.id = "chat-navigation-sections"; this.panel.shadowRoot.getElementById("direct-section")?.before(container); }
    const previous = new Map([...container.querySelectorAll("details")].map((item) => [item.dataset.group, item.open]));
    container.replaceChildren(); if (!this.supported) return;
    if (!this.sectionsAttempted && this.panel._hass) void this.loadSections().catch(() => {});
    const groups = [{ section_id: "pinned", name: "Pinned", pinned: true }, ...this.sections];
    for (const group of groups) {
      const threads = this.panel._threads.filter((thread) => this.panel._threadIsPrimaryActive(thread) && this.panel._threadMatchesQuery(thread) && (group.pinned ? thread.pinned : !thread.pinned && thread.section_id === group.section_id));
      if (!threads.length && (group.pinned || this.panel._searchQuery.trim())) continue;
      const details = document.createElement("details"); details.className = "chat-navigation-group"; details.dataset.group = group.section_id; details.open = previous.get(group.section_id) ?? true;
      const summary = document.createElement("summary"); const icon = document.createElement("span"); this.panel._setTrustedButtonContent(icon, this.icons[group.pinned ? "pin" : "menu"]); summary.append(icon, document.createTextNode(group.name)); details.append(summary);
      for (const thread of threads) details.append(this.panel._threadRow(thread));
      if (!threads.length) { const empty = document.createElement("p"); empty.className = "empty-note"; empty.textContent = "No chats in this section."; details.append(empty); }
      container.append(details);
    }
  }
}
