const MODE_OPTIONS = [
  { value: "observe", label: "Observe" },
  { value: "edit", label: "Edit" },
  { value: "full-auto", label: "Full auto" },
];

const template = document.createElement("template");
template.innerHTML = `
  <style>
    :host {
      --panel-bg: var(--primary-background-color, #0f172a);
      --surface-bg: var(--card-background-color, #111827);
      --surface-alt: color-mix(in srgb, var(--surface-bg) 82%, white 18%);
      --border-color: var(--divider-color, rgba(148, 163, 184, 0.22));
      --text-color: var(--primary-text-color, #e5e7eb);
      --muted-color: var(--secondary-text-color, #9ca3af);
      --accent-color: var(--primary-color, #3b82f6);
      display: block;
      height: 100%;
      color: var(--text-color);
    }

    * {
      box-sizing: border-box;
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(240px, 280px) minmax(420px, 1fr) minmax(260px, 320px);
      gap: 16px;
      min-height: calc(100vh - 64px);
      padding: 18px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 92%, white 8%), var(--panel-bg));
    }

    .pane {
      min-height: 0;
      background: color-mix(in srgb, var(--surface-bg) 88%, black 12%);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .rail-header,
    .main-header,
    .files-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 94%, white 6%);
    }

    .title-block {
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
    }

    .eyeline {
      font-size: 12px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .title {
      font-size: 18px;
      font-weight: 600;
      line-height: 1.25;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    button,
    input,
    textarea,
    select {
      font: inherit;
      color: inherit;
    }

    button {
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 78%, white 22%);
      border-radius: 8px;
      cursor: pointer;
      padding: 0;
    }

    button:hover {
      border-color: color-mix(in srgb, var(--accent-color) 60%, var(--border-color) 40%);
    }

    .icon-button {
      width: 38px;
      height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--muted-color);
    }

    .icon-button svg,
    .download-button svg,
    .send-button svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .thread-form {
      display: none;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border-color);
      gap: 10px;
      background: color-mix(in srgb, var(--surface-bg) 92%, white 8%);
    }

    .thread-form.visible {
      display: grid;
    }

    .field,
    .composer textarea {
      width: 100%;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 90%, black 10%);
      padding: 12px 14px;
      outline: none;
    }

    .field:focus,
    .composer textarea:focus {
      border-color: color-mix(in srgb, var(--accent-color) 70%, white 30%);
    }

    .mode-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .mode-option {
      height: 38px;
      color: var(--muted-color);
      background: color-mix(in srgb, var(--surface-bg) 84%, white 16%);
    }

    .mode-option.active {
      color: white;
      border-color: transparent;
      background: linear-gradient(180deg, color-mix(in srgb, var(--accent-color) 88%, white 12%), color-mix(in srgb, var(--accent-color) 72%, black 28%));
    }

    .thread-list,
    .message-list,
    .file-section {
      overflow: auto;
      min-height: 0;
    }

    .thread-list {
      padding: 10px;
    }

    .thread-row {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 14px;
      margin-bottom: 8px;
      text-align: left;
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-bg) 90%, white 10%);
    }

    .thread-row.active {
      border-color: color-mix(in srgb, var(--accent-color) 75%, white 25%);
      background: color-mix(in srgb, var(--accent-color) 20%, var(--surface-bg) 80%);
    }

    .thread-name {
      font-size: 14px;
      font-weight: 600;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .thread-meta,
    .status-text,
    .meta-line,
    .empty-note,
    .timestamp {
      font-size: 12px;
      color: var(--muted-color);
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: #22c55e;
      box-shadow: 0 0 0 4px color-mix(in srgb, #22c55e 18%, transparent);
    }

    .status-dot.running {
      background: #f59e0b;
      box-shadow: 0 0 0 4px color-mix(in srgb, #f59e0b 18%, transparent);
    }

    .status-dot.error {
      background: #ef4444;
      box-shadow: 0 0 0 4px color-mix(in srgb, #ef4444 18%, transparent);
    }

    .message-list {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 20px;
    }

    .message {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      max-width: 86%;
    }

    .message.user {
      align-self: flex-end;
      flex-direction: row-reverse;
    }

    .avatar {
      flex: 0 0 34px;
      width: 34px;
      height: 34px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--border-color);
      color: var(--muted-color);
      background: color-mix(in srgb, var(--surface-bg) 82%, white 18%);
    }

    .bubble {
      display: grid;
      gap: 8px;
      padding: 14px 16px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 90%, white 10%);
    }

    .message.user .bubble {
      background: color-mix(in srgb, var(--accent-color) 18%, var(--surface-bg) 82%);
      border-color: color-mix(in srgb, var(--accent-color) 52%, var(--border-color) 48%);
    }

    .bubble-text {
      margin: 0;
      font-size: 14px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .event-row {
      font-size: 12px;
      color: var(--muted-color);
      padding: 0 6px;
    }

    .composer {
      border-top: 1px solid var(--border-color);
      padding: 16px 18px 18px;
      display: grid;
      gap: 12px;
      background: color-mix(in srgb, var(--surface-bg) 94%, white 6%);
    }

    .composer textarea {
      min-height: 120px;
      resize: vertical;
      line-height: 1.5;
    }

    .composer-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .composer-left,
    .composer-right {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    .send-button {
      min-width: 132px;
      height: 42px;
      padding: 0 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      border-color: transparent;
      color: white;
      background: linear-gradient(180deg, color-mix(in srgb, var(--accent-color) 88%, white 12%), color-mix(in srgb, var(--accent-color) 72%, black 28%));
    }

    .files-body {
      display: grid;
      grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
      min-height: 0;
    }

    .section-block {
      display: flex;
      flex-direction: column;
      min-height: 0;
      border-top: 1px solid var(--border-color);
    }

    .section-block:first-child {
      border-top: 0;
    }

    .section-header {
      padding: 14px 16px 10px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted-color);
    }

    .file-list {
      padding: 0 10px 10px;
      display: grid;
      gap: 8px;
    }

    .file-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 90%, white 10%);
    }

    .file-name {
      font-size: 14px;
      font-weight: 600;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .download-button {
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--muted-color);
    }

    .banner {
      margin: 0 18px;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid color-mix(in srgb, #ef4444 45%, var(--border-color) 55%);
      background: color-mix(in srgb, #ef4444 12%, var(--surface-bg) 88%);
      color: #fecaca;
      font-size: 13px;
      line-height: 1.45;
    }

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 280px;
      padding: 20px;
      color: var(--muted-color);
      text-align: center;
    }

    .hidden {
      display: none;
    }

    @media (max-width: 1180px) {
      .shell {
        grid-template-columns: minmax(240px, 280px) minmax(0, 1fr);
      }

      .files-pane {
        grid-column: 1 / -1;
        min-height: 280px;
      }
    }

    @media (max-width: 760px) {
      .shell {
        grid-template-columns: 1fr;
        padding: 12px;
      }

      .message {
        max-width: 100%;
      }

      .composer-actions {
        flex-direction: column;
        align-items: stretch;
      }

      .composer-left,
      .composer-right {
        width: 100%;
        justify-content: space-between;
      }

      .send-button {
        width: 100%;
      }
    }
  </style>
  <div class="shell">
    <aside class="pane threads-pane">
      <div class="rail-header">
        <div class="title-block">
          <span class="eyeline">Threads</span>
          <span class="title" id="panel-title">Codex Bridge</span>
        </div>
        <button class="icon-button" id="toggle-thread-form" title="New thread" aria-label="New thread"></button>
      </div>
      <div class="thread-form" id="thread-form">
        <input class="field" id="thread-title" type="text" placeholder="New thread title" />
        <div class="mode-row" id="mode-row"></div>
        <button class="send-button" id="create-thread-button" type="button">Create thread</button>
      </div>
      <div class="thread-list" id="thread-list"></div>
    </aside>
    <main class="pane main-pane">
      <div class="main-header">
        <div class="title-block">
          <span class="eyeline" id="thread-mode">Ready</span>
          <span class="title" id="thread-title-label">Select a thread</span>
        </div>
        <div class="composer-right">
          <div class="status-text" id="thread-status-text"></div>
          <button class="icon-button" id="refresh-thread" title="Refresh" aria-label="Refresh"></button>
        </div>
      </div>
      <div class="banner hidden" id="error-banner"></div>
      <section class="message-list" id="message-list"></section>
      <footer class="composer">
        <textarea id="prompt-input" placeholder="Message Codex through Home Assistant"></textarea>
        <div class="composer-actions">
          <div class="composer-left">
            <button class="icon-button" id="upload-button" title="Upload file" aria-label="Upload file"></button>
            <span class="meta-line" id="attachment-meta"></span>
            <input id="file-input" type="file" class="hidden" />
          </div>
          <div class="composer-right">
            <span class="meta-line" id="run-meta"></span>
            <button class="send-button" id="send-button" type="button"></button>
          </div>
        </div>
      </footer>
    </main>
    <section class="pane files-pane">
      <div class="files-header">
        <div class="title-block">
          <span class="eyeline">Files</span>
          <span class="title">Attachments and outputs</span>
        </div>
      </div>
      <div class="files-body">
        <section class="section-block">
          <div class="section-header">Uploads</div>
          <div class="file-section">
            <div class="file-list" id="attachment-list"></div>
          </div>
        </section>
        <section class="section-block">
          <div class="section-header">Downloads</div>
          <div class="file-section">
            <div class="file-list" id="artifact-list"></div>
          </div>
        </section>
      </div>
    </section>
  </div>
`;

const iconSvg = (path) => `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    ${path}
  </svg>
`;

const icons = {
  plus: iconSvg('<path d="M12 5v14"></path><path d="M5 12h14"></path>'),
  refresh: iconSvg('<path d="M20 11a8 8 0 1 0 2 5.3"></path><path d="M20 4v7h-7"></path>'),
  upload: iconSvg('<path d="M12 16V4"></path><path d="m7 9 5-5 5 5"></path><path d="M5 20h14"></path>'),
  send: iconSvg('<path d="m22 2-7 20-4-9-9-4 20-7Z"></path><path d="M22 2 11 13"></path>'),
  download: iconSvg('<path d="M12 4v12"></path><path d="m7 11 5 5 5-5"></path><path d="M5 20h14"></path>'),
  user: iconSvg('<path d="M20 21a8 8 0 1 0-16 0"></path><circle cx="12" cy="7" r="4"></circle>'),
  bot: iconSvg('<rect x="5" y="7" width="14" height="10" rx="4"></rect><path d="M12 3v4"></path><circle cx="10" cy="12" r="1"></circle><circle cx="14" cy="12" r="1"></circle>')
};

class CodexBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.appendChild(template.content.cloneNode(true));
    this._hass = null;
    this._panel = null;
    this._config = null;
    this._threads = [];
    this._selectedThreadId = null;
    this._activeThread = null;
    this._events = [];
    this._artifacts = [];
    this._sequence = 0;
    this._draft = "";
    this._showThreadForm = false;
    this._newThreadMode = "full-auto";
    this._pollTimer = null;
    this._isLoading = false;
    this._error = "";
  }

  connectedCallback() {
    this._installStaticUi();
    this._render();
  }

  disconnectedCallback() {
    this._stopPolling();
  }

  set hass(value) {
    this._hass = value;
    if (!this._config) {
      this._bootstrap();
      return;
    }
    this._render();
  }

  get hass() {
    return this._hass;
  }

  set panel(value) {
    this._panel = value;
    this._render();
  }

  get panel() {
    return this._panel;
  }

  async _bootstrap() {
    if (!this._hass || this._isLoading) {
      return;
    }
    this._isLoading = true;
    try {
      this._config = await this._callWS("get_config");
      await this._loadThreads();
    } catch (error) {
      this._setError(error);
    } finally {
      this._isLoading = false;
      this._render();
    }
  }

  async _callWS(action, payload = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `codex_bridge/${action}`,
      ...payload,
    });
  }

  _accessToken() {
    return (
      this._hass?.auth?.data?.access_token ||
      this._hass?.auth?.data?.accessToken ||
      this._hass?.auth?.accessToken ||
      this._hass?.connection?.options?.auth?.accessToken ||
      ""
    );
  }

  _installStaticUi() {
    this.shadowRoot.getElementById("toggle-thread-form").innerHTML = icons.plus;
    this.shadowRoot.getElementById("refresh-thread").innerHTML = icons.refresh;
    this.shadowRoot.getElementById("upload-button").innerHTML = icons.upload;
    this.shadowRoot.getElementById("send-button").innerHTML = `${icons.send}<span>Send</span>`;

    const modeRow = this.shadowRoot.getElementById("mode-row");
    modeRow.innerHTML = MODE_OPTIONS.map(
      (option) =>
        `<button class="mode-option" type="button" data-mode="${option.value}">${option.label}</button>`
    ).join("");
  }

  _render() {
    this.shadowRoot.getElementById("panel-title").textContent =
      this._config?.panel_title || "Codex Bridge";

    const activeThread = this._activeThread;
    const status = activeThread?.status || "idle";

    this.shadowRoot.getElementById("thread-form").classList.toggle("visible", this._showThreadForm);
    this.shadowRoot.getElementById("thread-title-label").textContent =
      activeThread?.title || "Select a thread";
    this.shadowRoot.getElementById("thread-mode").textContent =
      activeThread ? (activeThread.mode || "full-auto") : "Ready";
    this.shadowRoot.getElementById("thread-status-text").textContent = activeThread
      ? `Status: ${status}`
      : "";
    this.shadowRoot.getElementById("attachment-meta").textContent = activeThread
      ? `${activeThread.attachments.length} upload${activeThread.attachments.length === 1 ? "" : "s"}`
      : "No thread selected";
    this.shadowRoot.getElementById("run-meta").textContent = activeThread?.last_error
      ? activeThread.last_error
      : activeThread?.active_run_id
        ? `Run ${activeThread.active_run_id}`
        : "";

    const errorBanner = this.shadowRoot.getElementById("error-banner");
    errorBanner.textContent = this._error;
    errorBanner.classList.toggle("hidden", !this._error);

    this._renderThreadList();
    this._renderMessages();
    this._renderAttachments();
    this._renderArtifacts();
    this._renderModeButtons();
    this._wireEvents();
  }

  _renderModeButtons() {
    for (const button of this.shadowRoot.querySelectorAll(".mode-option")) {
      button.classList.toggle("active", button.dataset.mode === this._newThreadMode);
    }
  }

  _renderThreadList() {
    const threadList = this.shadowRoot.getElementById("thread-list");
    if (!this._threads.length) {
      threadList.innerHTML = `<div class="empty-state"><div><div class="title">No threads yet</div><div class="empty-note">Create the first bridge thread to start chatting.</div></div></div>`;
      return;
    }

    threadList.innerHTML = this._threads
      .map((thread) => {
        const statusClass = thread.status === "running" ? "running" : thread.status === "error" ? "error" : "";
        return `
          <button class="thread-row ${thread.thread_id === this._selectedThreadId ? "active" : ""}" type="button" data-thread-id="${thread.thread_id}">
            <div class="title-block">
              <span class="thread-name">${this._escapeHtml(thread.title)}</span>
              <span class="thread-meta">${this._escapeHtml(thread.mode)} · ${this._escapeHtml(thread.status)}</span>
            </div>
            <span class="status-dot ${statusClass}"></span>
          </button>
        `;
      })
      .join("");
  }

  _renderMessages() {
    const messageList = this.shadowRoot.getElementById("message-list");
    if (!this._selectedThreadId) {
      messageList.innerHTML = `<div class="empty-state"><div><div class="title">Start from Home Assistant</div><div class="empty-note">Pick a thread or create a new one to send prompts, upload files, and fetch outputs.</div></div></div>`;
      return;
    }

    const fragments = [];
    for (const event of this._events) {
      if (event.event_type === "message.created") {
        fragments.push(this._renderMessage("user", event.payload.text, event.sequence));
      }
      if (event.event_type === "message.completed") {
        fragments.push(this._renderMessage("assistant", event.payload.text, event.sequence));
      }
      if (event.event_type === "run.started") {
        fragments.push(`<div class="event-row">Run started</div>`);
      }
      if (event.event_type === "run.completed") {
        fragments.push(`<div class="event-row">Run completed</div>`);
      }
      if (event.event_type === "run.failed") {
        fragments.push(`<div class="event-row">Run failed: ${this._escapeHtml(event.payload.error || "Unknown error")}</div>`);
      }
      if (event.event_type === "artifact.added") {
        fragments.push(`<div class="event-row">Output ready: ${this._escapeHtml(event.payload.filename || "artifact")}</div>`);
      }
    }

    messageList.innerHTML = fragments.join("") || `<div class="empty-state"><div><div class="title">Thread is ready</div><div class="empty-note">Send the first prompt when you’re ready.</div></div></div>`;
    messageList.scrollTop = messageList.scrollHeight;
  }

  _renderMessage(role, text, key) {
    const icon = role === "user" ? icons.user : icons.bot;
    return `
      <article class="message ${role}" data-sequence="${key}">
        <span class="avatar">${icon}</span>
        <div class="bubble">
          <pre class="bubble-text">${this._escapeHtml(text || "")}</pre>
        </div>
      </article>
    `;
  }

  _renderAttachments() {
    const attachmentList = this.shadowRoot.getElementById("attachment-list");
    const attachments = this._activeThread?.attachments || [];
    if (!attachments.length) {
      attachmentList.innerHTML = `<div class="empty-note">No uploads yet.</div>`;
      return;
    }
    attachmentList.innerHTML = attachments
      .map(
        (attachment) => `
          <div class="file-row">
            <div class="title-block">
              <span class="file-name">${this._escapeHtml(attachment.filename)}</span>
              <span class="thread-meta">${this._escapeHtml(attachment.mime_type)}</span>
            </div>
            <span class="timestamp">Uploaded</span>
          </div>
        `
      )
      .join("");
  }

  _renderArtifacts() {
    const artifactList = this.shadowRoot.getElementById("artifact-list");
    if (!this._artifacts.length) {
      artifactList.innerHTML = `<div class="empty-note">No outputs yet.</div>`;
      return;
    }
    artifactList.innerHTML = this._artifacts
      .map(
        (artifact) => `
          <div class="file-row">
            <div class="title-block">
              <span class="file-name">${this._escapeHtml(artifact.filename)}</span>
              <span class="thread-meta">${this._escapeHtml(artifact.mime_type)}</span>
            </div>
            <button class="download-button" type="button" data-artifact-id="${artifact.artifact_id}" title="Download ${this._escapeHtml(artifact.filename)}" aria-label="Download ${this._escapeHtml(artifact.filename)}">
              ${icons.download}
            </button>
          </div>
        `
      )
      .join("");
  }

  _wireEvents() {
    this.shadowRoot.getElementById("toggle-thread-form").onclick = () => {
      this._showThreadForm = !this._showThreadForm;
      this._render();
    };
    this.shadowRoot.getElementById("refresh-thread").onclick = () => this._refreshActiveThread();
    this.shadowRoot.getElementById("create-thread-button").onclick = () => this._createThread();
    this.shadowRoot.getElementById("send-button").onclick = () => this._sendPrompt();
    this.shadowRoot.getElementById("upload-button").onclick = () =>
      this.shadowRoot.getElementById("file-input").click();
    this.shadowRoot.getElementById("file-input").onchange = (event) => {
      const [file] = event.target.files || [];
      if (file) {
        this._uploadFile(file);
      }
      event.target.value = "";
    };
    this.shadowRoot.getElementById("prompt-input").oninput = (event) => {
      this._draft = event.target.value;
    };
    this.shadowRoot.getElementById("thread-title").onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        this._createThread();
      }
    };
    for (const button of this.shadowRoot.querySelectorAll(".mode-option")) {
      button.onclick = () => {
        this._newThreadMode = button.dataset.mode;
        this._renderModeButtons();
      };
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-thread-id]")) {
      button.onclick = () => this._selectThread(button.dataset.threadId);
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-artifact-id]")) {
      button.onclick = () => this._downloadArtifact(button.dataset.artifactId);
    }
  }

  async _loadThreads() {
    const threads = await this._callWS("list_threads");
    this._clearError();
    this._threads = threads;
    if (!this._selectedThreadId && threads.length) {
      this._selectedThreadId = threads[0].thread_id;
    }
    if (this._selectedThreadId) {
      await this._refreshActiveThread();
      this._startPolling();
    }
    this._render();
  }

  async _refreshActiveThread() {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      this._activeThread = await this._callWS("get_thread", {
        thread_id: this._selectedThreadId,
      });
      await this._loadEvents(this._selectedThreadId, 0, true);
      this._artifacts = await this._callWS("list_artifacts", {
        thread_id: this._selectedThreadId,
      });
      this._clearError();
      this._syncThreadListStatus();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _loadEvents(threadId, after, replace = false) {
    const events = await this._callWS("get_events", {
      thread_id: threadId,
      after,
    });
    if (replace) {
      this._events = events;
    } else if (events.length) {
      this._events = [...this._events, ...events];
    }
    this._sequence = this._events.length ? this._events[this._events.length - 1].sequence : 0;
  }

  async _selectThread(threadId) {
    this._selectedThreadId = threadId;
    this._sequence = 0;
    this._events = [];
    await this._refreshActiveThread();
    this._startPolling();
  }

  async _createThread() {
    try {
      const titleInput = this.shadowRoot.getElementById("thread-title");
      const title = titleInput.value.trim();
      if (!title) {
        return;
      }
      const thread = await this._callWS("create_thread", {
        title,
        mode: this._newThreadMode,
      });
      titleInput.value = "";
      this._showThreadForm = false;
      this._selectedThreadId = thread.thread_id;
      this._clearError();
      await this._loadThreads();
    } catch (error) {
      this._setError(error);
    }
  }

  async _sendPrompt() {
    try {
      const promptInput = this.shadowRoot.getElementById("prompt-input");
      const prompt = promptInput.value.trim();
      if (!prompt || !this._selectedThreadId) {
        return;
      }
      await this._callWS("send_prompt", {
        thread_id: this._selectedThreadId,
        prompt,
      });
      promptInput.value = "";
      this._draft = "";
      this._clearError();
      await this._refreshActiveThread();
    } catch (error) {
      this._setError(error);
    }
  }

  async _uploadFile(file) {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      const formData = new FormData();
      formData.append("file", file, file.name);
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(
        `/api/codex_bridge/threads/${this._selectedThreadId}/attachments`,
        {
          method: "POST",
          headers,
          body: formData,
        }
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.message || "Upload failed");
      }
      this._clearError();
      await this._refreshActiveThread();
    } catch (error) {
      this._setError(error);
    }
  }

  async _downloadArtifact(artifactId) {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(
        `/api/codex_bridge/threads/${this._selectedThreadId}/artifacts/${artifactId}`,
        {
          headers,
        }
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.message || "Download failed");
      }
      const blob = await response.blob();
      const contentDisposition = response.headers.get("Content-Disposition") || "";
      const filenameMatch = contentDisposition.match(/filename="(.+?)"/);
      const filename = filenameMatch ? filenameMatch[1] : "codex-artifact";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }

  _startPolling() {
    this._stopPolling();
    if (!this._selectedThreadId) {
      return;
    }
    this._pollTimer = window.setInterval(async () => {
      if (!this._selectedThreadId) {
        return;
      }
      try {
        await this._loadEvents(this._selectedThreadId, this._sequence, false);
        if (this._activeThread?.status === "running" || this._events.length) {
          this._activeThread = await this._callWS("get_thread", {
            thread_id: this._selectedThreadId,
          });
          this._syncThreadListStatus();
          if (this._activeThread.status !== "running") {
            this._artifacts = await this._callWS("list_artifacts", {
              thread_id: this._selectedThreadId,
            });
          }
          this._render();
        }
      } catch (error) {
        this._setError(error);
      }
    }, 1600);
  }

  _stopPolling() {
    if (this._pollTimer) {
      window.clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  _syncThreadListStatus() {
    this._threads = this._threads.map((thread) =>
      thread.thread_id === this._activeThread.thread_id ? this._activeThread : thread
    );
  }

  _setError(error) {
    this._error = error?.body?.message || error?.message || "Unexpected error";
    this._render();
  }

  _clearError() {
    this._error = "";
  }

  _escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
}

if (!customElements.get("codex-bridge-panel")) {
  customElements.define("codex-bridge-panel", CodexBridgePanel);
}
