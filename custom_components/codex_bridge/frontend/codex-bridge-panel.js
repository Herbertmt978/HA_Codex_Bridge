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
      --surface-alt: color-mix(in srgb, var(--surface-bg) 80%, white 20%);
      --surface-soft: color-mix(in srgb, var(--surface-bg) 92%, white 8%);
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

    button,
    input,
    textarea,
    select {
      font: inherit;
      color: inherit;
    }

    button {
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 84%, white 16%);
      border-radius: 8px;
      cursor: pointer;
      padding: 0;
    }

    button:hover {
      border-color: color-mix(in srgb, var(--accent-color) 60%, var(--border-color) 40%);
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(300px, 360px) minmax(480px, 1fr) minmax(280px, 340px);
      gap: 16px;
      min-height: calc(100vh - 64px);
      padding: 18px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 94%, white 6%), var(--panel-bg));
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

    .subline,
    .meta-line,
    .thread-meta,
    .status-text,
    .empty-note,
    .timestamp {
      font-size: 12px;
      color: var(--muted-color);
      line-height: 1.45;
    }

    .hidden {
      display: none !important;
    }

    .icon-button,
    .copy-button,
    .download-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }

    .icon-button {
      width: 38px;
      height: 38px;
      color: var(--muted-color);
    }

    .icon-button.small {
      width: 32px;
      height: 32px;
    }

    .icon-button svg,
    .download-button svg,
    .send-button svg,
    .copy-button svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .stack {
      display: grid;
      gap: 10px;
    }

    .section-scroll,
    .message-list,
    .file-section,
    .browse-list {
      overflow: auto;
      min-height: 0;
    }

    .forms-stack {
      display: grid;
      gap: 10px;
      padding: 12px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 93%, white 7%);
    }

    .panel-form {
      display: none;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-bg) 90%, white 10%);
    }

    .panel-form.visible {
      display: grid;
    }

    .field,
    .field-select,
    .composer textarea {
      width: 100%;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 90%, black 10%);
      padding: 12px 14px;
      outline: none;
    }

    .field:focus,
    .field-select:focus,
    .composer textarea:focus {
      border-color: color-mix(in srgb, var(--accent-color) 70%, white 30%);
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .project-list {
      padding: 10px;
      display: grid;
      gap: 10px;
    }

    .project-card {
      border: 1px solid var(--border-color);
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-bg) 90%, white 10%);
      overflow: hidden;
    }

    .project-card.active {
      border-color: color-mix(in srgb, var(--accent-color) 70%, white 30%);
      background: color-mix(in srgb, var(--accent-color) 12%, var(--surface-bg) 88%);
    }

    .project-head {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: start;
      padding: 12px;
      border-bottom: 1px solid color-mix(in srgb, var(--border-color) 75%, transparent);
    }

    .project-select {
      display: grid;
      gap: 6px;
      text-align: left;
      background: transparent;
      border: 0;
      padding: 0;
      color: inherit;
    }

    .project-select:hover {
      border: 0;
    }

    .project-actions {
      display: flex;
      gap: 6px;
      align-items: center;
    }

    .project-name,
    .thread-name,
    .file-name {
      font-size: 14px;
      font-weight: 600;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .chat-list {
      display: grid;
      gap: 6px;
      padding: 10px 12px 12px;
    }

    .chat-row {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      text-align: left;
      padding: 10px 12px;
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-bg) 95%, white 5%);
    }

    .chat-row.active {
      border-color: color-mix(in srgb, var(--accent-color) 70%, white 30%);
      background: color-mix(in srgb, var(--accent-color) 20%, var(--surface-bg) 80%);
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

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 220px;
      padding: 20px;
      text-align: center;
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

    .control-bar {
      display: grid;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 93%, white 7%);
    }

    .limits-grid,
    .chat-settings {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .limit-card,
    .setting-card,
    .browser-card {
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 91%, white 9%);
    }

    .limit-card.blocked {
      border-color: color-mix(in srgb, #ef4444 60%, var(--border-color) 40%);
      background: color-mix(in srgb, #ef4444 10%, var(--surface-bg) 90%);
    }

    .setting-label,
    .limit-label,
    .browser-label,
    .section-header {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted-color);
    }

    .limit-value {
      margin-top: 6px;
      font-size: 20px;
      font-weight: 600;
      line-height: 1.2;
    }

    .limit-subline {
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted-color);
    }

    .setting-card {
      display: grid;
      gap: 8px;
    }

    .setting-foot {
      font-size: 12px;
      color: var(--muted-color);
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
      max-width: 92%;
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
      user-select: text;
      -webkit-user-select: text;
    }

    .message.user .bubble {
      background: color-mix(in srgb, var(--accent-color) 18%, var(--surface-bg) 82%);
      border-color: color-mix(in srgb, var(--accent-color) 52%, var(--border-color) 48%);
    }

    .message-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .copy-button {
      gap: 8px;
      padding: 6px 10px;
      color: var(--muted-color);
      background: color-mix(in srgb, var(--surface-bg) 86%, white 14%);
    }

    .bubble-text {
      margin: 0;
      font-size: 14px;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      user-select: text;
      -webkit-user-select: text;
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

    .download-button {
      width: 34px;
      height: 34px;
      color: var(--muted-color);
    }

    .browser-actions,
    .form-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .text-button {
      padding: 10px 12px;
      border-radius: 8px;
    }

    .browse-list {
      display: grid;
      gap: 6px;
      max-height: 180px;
      margin-top: 10px;
    }

    .browse-row {
      width: 100%;
      text-align: left;
      padding: 10px 12px;
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-bg) 95%, white 5%);
    }

    .browser-path {
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted-color);
      word-break: break-word;
    }

    @media (max-width: 1260px) {
      .shell {
        grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
      }

      .files-pane {
        grid-column: 1 / -1;
        min-height: 280px;
      }
    }

    @media (max-width: 860px) {
      .shell {
        grid-template-columns: 1fr;
        padding: 12px;
      }

      .field-grid,
      .limits-grid,
      .chat-settings {
        grid-template-columns: 1fr;
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
    <aside class="pane rail-pane">
      <div class="rail-header">
        <div class="title-block">
          <span class="eyeline">Projects</span>
          <span class="title" id="panel-title">Codex Bridge</span>
        </div>
        <div class="project-actions">
          <button class="icon-button" type="button" data-action="toggle-thread-form" title="New chat" aria-label="New chat"></button>
          <button class="icon-button" type="button" data-action="toggle-project-form" title="New project" aria-label="New project"></button>
        </div>
      </div>
      <div class="forms-stack">
        <section class="panel-form" id="project-form-panel"></section>
        <section class="panel-form" id="thread-form-panel"></section>
      </div>
      <div class="section-scroll">
        <div class="project-list" id="project-list"></div>
      </div>
    </aside>
    <main class="pane main-pane">
      <div class="main-header">
        <div class="title-block">
          <span class="eyeline" id="thread-project-label">Ready</span>
          <span class="title" id="thread-title-label">Select a chat</span>
          <span class="subline" id="thread-path-label"></span>
        </div>
        <div class="composer-right">
          <div class="status-text" id="thread-status-text"></div>
          <button class="icon-button" type="button" data-action="refresh-thread" title="Refresh" aria-label="Refresh"></button>
        </div>
      </div>
      <div class="banner hidden" id="error-banner"></div>
      <section class="control-bar">
        <div class="limits-grid" id="limits-grid"></div>
        <div class="chat-settings" id="chat-settings"></div>
      </section>
      <section class="message-list" id="message-list"></section>
      <footer class="composer">
        <textarea id="prompt-input" placeholder="Message Codex through Home Assistant"></textarea>
        <div class="composer-actions">
          <div class="composer-left">
            <button class="icon-button" type="button" data-action="upload-file" title="Upload file" aria-label="Upload file"></button>
            <span class="meta-line" id="attachment-meta"></span>
            <input id="file-input" type="file" class="hidden" />
          </div>
          <div class="composer-right">
            <span class="meta-line" id="run-meta"></span>
            <button class="send-button" id="send-button" type="button" data-action="send-prompt"></button>
          </div>
        </div>
      </footer>
    </main>
    <section class="pane files-pane">
      <div class="files-header">
        <div class="title-block">
          <span class="eyeline">Files</span>
          <span class="title">Project uploads and outputs</span>
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
  bot: iconSvg('<rect x="5" y="7" width="14" height="10" rx="4"></rect><path d="M12 3v4"></path><circle cx="10" cy="12" r="1"></circle><circle cx="14" cy="12" r="1"></circle>'),
  folder: iconSvg('<path d="M3 7h6l2 2h10v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2"></path>'),
  edit: iconSvg('<path d="M12 20h9"></path><path d="m16.5 3.5 4 4L8 20H4v-4Z"></path>'),
  chat: iconSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"></path>'),
  copy: iconSvg('<rect x="9" y="9" width="10" height="10" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'),
  back: iconSvg('<path d="m15 18-6-6 6-6"></path>'),
  save: iconSvg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"></path><path d="M17 21v-8H7v8"></path><path d="M7 3v5h8"></path>'),
  browse: iconSvg('<path d="M3 12h18"></path><path d="M12 3v18"></path>')
};

class CodexBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.appendChild(template.content.cloneNode(true));
    this._hass = null;
    this._panel = null;
    this._config = null;
    this._status = null;
    this._projects = [];
    this._threads = [];
    this._selectedProjectId = null;
    this._selectedThreadId = null;
    this._activeThread = null;
    this._events = [];
    this._artifacts = [];
    this._sequence = 0;
    this._draft = "";
    this._showProjectForm = false;
    this._showThreadForm = false;
    this._projectFormMode = "create";
    this._editingProjectId = null;
    this._projectForm = {
      name: "",
      rootPath: "",
      defaultModel: "gpt-5.4",
      defaultThinkingLevel: "medium",
    };
    this._threadForm = {
      title: "",
      mode: "full-auto",
    };
    this._folderDraft = "";
    this._browseState = null;
    this._pollTimer = null;
    this._pollTick = 0;
    this._isLoading = false;
    this._error = "";
    this._renderedThreadId = null;
    this._renderedSequence = 0;
    this._forceMessageRebuild = true;
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
      await Promise.all([this._loadStatus(), this._loadProjects(), this._loadThreads()]);
      this._clearError();
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
    this.shadowRoot.querySelector('[data-action="toggle-project-form"]').innerHTML = icons.plus;
    this.shadowRoot.querySelector('[data-action="toggle-thread-form"]').innerHTML = icons.chat;
    this.shadowRoot.querySelector('[data-action="refresh-thread"]').innerHTML = icons.refresh;
    this.shadowRoot.querySelector('[data-action="upload-file"]').innerHTML = icons.upload;
    this.shadowRoot.getElementById("send-button").innerHTML = `${icons.send}<span>Send</span>`;

    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
    this.shadowRoot.addEventListener("change", (event) => this._handleChange(event));
    this.shadowRoot.getElementById("file-input").addEventListener("change", (event) => {
      const [file] = event.target.files || [];
      if (file) {
        this._uploadFile(file);
      }
      event.target.value = "";
    });
  }

  _handleClick(event) {
    const actionTarget = event.target.closest("[data-action]");
    if (!actionTarget) {
      return;
    }
    const action = actionTarget.dataset.action;
    switch (action) {
      case "toggle-project-form":
        this._openProjectFormForCreate();
        break;
      case "toggle-thread-form":
        this._toggleThreadForm();
        break;
      case "refresh-thread":
        this._refreshActiveThread();
        break;
      case "save-project":
        this._saveProject();
        break;
      case "cancel-project-form":
        this._closeProjectForm();
        break;
      case "browse-current":
        this._browseProjectPath(this._projectForm.rootPath || this._browseState?.path || null);
        break;
      case "browse-up":
        this._browseProjectPath(this._browseState?.parent_path || null);
        break;
      case "browse-roots":
        this._browseProjectPath(null);
        break;
      case "browse-entry":
        this._selectBrowseEntry(actionTarget.dataset.path || "");
        break;
      case "create-folder":
        this._createFolder();
        break;
      case "save-thread":
        this._createThread();
        break;
      case "cancel-thread-form":
        this._showThreadForm = false;
        this._render();
        break;
      case "select-project":
        this._selectProject(actionTarget.dataset.projectId || null);
        break;
      case "edit-project":
        this._openProjectFormForEdit(actionTarget.dataset.projectId || "");
        break;
      case "new-chat":
        this._openThreadFormForProject(actionTarget.dataset.projectId || null);
        break;
      case "select-thread":
        this._selectThread(actionTarget.dataset.threadId || null);
        break;
      case "send-prompt":
        this._sendPrompt();
        break;
      case "upload-file":
        this.shadowRoot.getElementById("file-input").click();
        break;
      case "download-artifact":
        this._downloadArtifact(actionTarget.dataset.artifactId || "");
        break;
      case "copy-message":
        this._copyMessage(actionTarget.dataset.sequence || "");
        break;
      default:
        break;
    }
  }

  _handleInput(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.id === "prompt-input") {
      this._draft = target.value;
      return;
    }
    if (target.id === "project-name-input") {
      this._projectForm.name = target.value;
      return;
    }
    if (target.id === "project-root-input") {
      this._projectForm.rootPath = target.value;
      return;
    }
    if (target.id === "thread-title-input") {
      this._threadForm.title = target.value;
      return;
    }
    if (target.id === "folder-name-input") {
      this._folderDraft = target.value;
    }
  }

  _handleChange(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.id === "project-model-select") {
      this._projectForm.defaultModel = target.value;
      return;
    }
    if (target.id === "project-thinking-select") {
      this._projectForm.defaultThinkingLevel = target.value;
      return;
    }
    if (target.id === "thread-mode-select") {
      this._threadForm.mode = target.value;
      return;
    }
    if (target.id === "thread-model-select") {
      this._updateThreadSettings({ model_override: target.value || null });
      return;
    }
    if (target.id === "thread-thinking-select") {
      this._updateThreadSettings({ thinking_override: target.value || null });
    }
  }

  _render() {
    this.shadowRoot.getElementById("panel-title").textContent =
      this._config?.panel_title || "Codex Bridge";

    const activeThread = this._activeThread;
    const activeProject = this._activeProject();
    const status = activeThread?.status || "idle";

    this.shadowRoot.getElementById("thread-project-label").textContent =
      activeProject?.name || "Ready";
    this.shadowRoot.getElementById("thread-title-label").textContent =
      activeThread?.title || "Select a chat";
    this.shadowRoot.getElementById("thread-path-label").textContent =
      activeThread?.workspace_path || activeProject?.root_path || "";
    this.shadowRoot.getElementById("thread-status-text").textContent = activeThread
      ? `Status: ${status}`
      : "";
    this.shadowRoot.getElementById("attachment-meta").textContent = activeThread
      ? `${activeThread.attachments.length} upload${activeThread.attachments.length === 1 ? "" : "s"}`
      : activeProject
        ? `Project: ${activeProject.name}`
        : "Select a project";
    this.shadowRoot.getElementById("run-meta").textContent = activeThread?.last_error
      ? activeThread.last_error
      : activeThread?.active_run_id
        ? `Run ${activeThread.active_run_id}`
        : "";
    this.shadowRoot.getElementById("prompt-input").value = this._draft;

    const errorBanner = this.shadowRoot.getElementById("error-banner");
    errorBanner.textContent = this._error;
    errorBanner.classList.toggle("hidden", !this._error);

    this._renderProjectForm();
    this._renderThreadForm();
    this._renderProjectList();
    this._renderLimits();
    this._renderChatSettings();
    this._renderMessages();
    this._renderAttachments();
    this._renderArtifacts();
  }

  _renderProjectForm() {
    const panel = this.shadowRoot.getElementById("project-form-panel");
    panel.classList.toggle("visible", this._showProjectForm);
    if (!this._showProjectForm) {
      panel.innerHTML = "";
      return;
    }

    const browseDirectories = (this._browseState?.directories || [])
      .map(
        (entry) => `
          <button class="browse-row" type="button" data-action="browse-entry" data-path="${this._escapeHtml(entry.path)}">
            ${this._escapeHtml(entry.name)}
          </button>
        `
      )
      .join("");

    panel.innerHTML = `
      <div class="title-block">
        <span class="eyeline">${this._projectFormMode === "edit" ? "Edit project" : "New project"}</span>
        <span class="title">${this._projectFormMode === "edit" ? "Project settings" : "Create a VM-backed project"}</span>
      </div>
      <input class="field" id="project-name-input" type="text" placeholder="Project name" value="${this._escapeHtml(this._projectForm.name)}" />
      <input class="field" id="project-root-input" type="text" placeholder="C:\\\\Projects\\\\My Work" value="${this._escapeHtml(this._projectForm.rootPath)}" />
      <div class="field-grid">
        <select class="field-select" id="project-model-select">${this._modelOptions(this._projectForm.defaultModel)}</select>
        <select class="field-select" id="project-thinking-select">${this._thinkingOptions(this._projectForm.defaultThinkingLevel)}</select>
      </div>
      <div class="browser-card">
        <div class="browser-label">Path browser</div>
        <div class="browser-path">${this._escapeHtml(this._browseState?.path || "No folder loaded yet")}</div>
        <div class="browser-actions" style="margin-top: 10px;">
          <button class="text-button" type="button" data-action="browse-current">Browse</button>
          <button class="text-button" type="button" data-action="browse-up">Up</button>
          <button class="text-button" type="button" data-action="browse-roots">Drives</button>
        </div>
        <div class="browse-list">${browseDirectories || `<div class="empty-note">Browse a path to list folders.</div>`}</div>
        <div class="browser-actions" style="margin-top: 10px;">
          <input class="field" id="folder-name-input" type="text" placeholder="New folder name" value="${this._escapeHtml(this._folderDraft)}" />
          <button class="text-button" type="button" data-action="create-folder">Create folder</button>
        </div>
      </div>
      <div class="form-actions">
        <button class="send-button" type="button" data-action="save-project">${icons.save}<span>${this._projectFormMode === "edit" ? "Update project" : "Create project"}</span></button>
        <button class="text-button" type="button" data-action="cancel-project-form">Close</button>
      </div>
    `;
  }

  _renderThreadForm() {
    const panel = this.shadowRoot.getElementById("thread-form-panel");
    panel.classList.toggle("visible", this._showThreadForm);
    if (!this._showThreadForm) {
      panel.innerHTML = "";
      return;
    }

    const activeProject = this._activeProject();
    panel.innerHTML = `
      <div class="title-block">
        <span class="eyeline">New chat</span>
        <span class="title">${activeProject ? this._escapeHtml(activeProject.name) : "Pick a project first"}</span>
      </div>
      <input class="field" id="thread-title-input" type="text" placeholder="Chat title" value="${this._escapeHtml(this._threadForm.title)}" />
      <select class="field-select" id="thread-mode-select">
        ${MODE_OPTIONS.map(
          (option) =>
            `<option value="${option.value}" ${option.value === this._threadForm.mode ? "selected" : ""}>${this._escapeHtml(option.label)}</option>`
        ).join("")}
      </select>
      <div class="meta-line">${activeProject ? this._escapeHtml(activeProject.root_path) : "Select a project from the list before creating a chat."}</div>
      <div class="form-actions">
        <button class="send-button" type="button" data-action="save-thread">${icons.chat}<span>Create chat</span></button>
        <button class="text-button" type="button" data-action="cancel-thread-form">Close</button>
      </div>
    `;
  }

  _renderProjectList() {
    const list = this.shadowRoot.getElementById("project-list");
    if (!this._projects.length) {
      list.innerHTML = `<div class="empty-state"><div><div class="title">No projects yet</div><div class="empty-note">Create the first project and point it at a real folder on the VM.</div></div></div>`;
      return;
    }

    list.innerHTML = this._projects
      .map((project) => {
        const threads = this._threads.filter((thread) => thread.project_id === project.project_id);
        const isActive = project.project_id === this._selectedProjectId;
        return `
          <section class="project-card ${isActive ? "active" : ""}">
            <div class="project-head">
              <button class="project-select" type="button" data-action="select-project" data-project-id="${project.project_id}">
                <span class="project-name">${this._escapeHtml(project.name)}</span>
                <span class="thread-meta">${this._escapeHtml(project.root_path)}</span>
                <span class="thread-meta">${this._escapeHtml(project.default_model)} · ${this._escapeHtml(project.default_thinking_level)}</span>
              </button>
              <div class="project-actions">
                <button class="icon-button small" type="button" data-action="new-chat" data-project-id="${project.project_id}" title="New chat" aria-label="New chat">${icons.chat}</button>
                <button class="icon-button small" type="button" data-action="edit-project" data-project-id="${project.project_id}" title="Edit project" aria-label="Edit project">${icons.edit}</button>
              </div>
            </div>
            <div class="chat-list">
              ${threads.length ? threads.map((thread) => this._threadRow(thread)).join("") : `<div class="empty-note">No chats yet.</div>`}
            </div>
          </section>
        `;
      })
      .join("");
  }

  _threadRow(thread) {
    const statusClass =
      thread.status === "running" ? "running" : thread.status === "error" ? "error" : "";
    return `
      <button class="chat-row ${thread.thread_id === this._selectedThreadId ? "active" : ""}" type="button" data-action="select-thread" data-thread-id="${thread.thread_id}">
        <div class="title-block">
          <span class="thread-name">${this._escapeHtml(thread.title)}</span>
          <span class="thread-meta">${this._effectiveMeta(thread)}</span>
        </div>
        <span class="status-dot ${statusClass}"></span>
      </button>
    `;
  }

  _renderLimits() {
    const container = this.shadowRoot.getElementById("limits-grid");
    const limits = this._status?.limits;
    if (!limits?.available) {
      container.innerHTML = `
        <div class="limit-card">
          <div class="limit-label">5-hour limit</div>
          <div class="limit-value">Unavailable</div>
          <div class="limit-subline">A live rate-limit snapshot appears after Codex reports one.</div>
        </div>
        <div class="limit-card">
          <div class="limit-label">Weekly limit</div>
          <div class="limit-value">Unavailable</div>
          <div class="limit-subline">The bridge still detects exhausted-credit failures cleanly.</div>
        </div>
      `;
      return;
    }

    container.innerHTML = `
      ${this._limitCard("5-hour limit", limits.primary, limits.blocked, limits.message)}
      ${this._limitCard("Weekly limit", limits.secondary, limits.blocked, limits.message)}
    `;
  }

  _renderChatSettings() {
    const container = this.shadowRoot.getElementById("chat-settings");
    const thread = this._activeThread;
    const project = this._activeProject();
    const models = this._status?.models || ["gpt-5.4"];
    const thinkingLevels = this._status?.thinking_levels || ["medium"];
    if (!thread || !project) {
      container.innerHTML = `
        <div class="setting-card">
          <div class="setting-label">Model</div>
          <div class="setting-foot">Select a chat to choose a model override.</div>
        </div>
        <div class="setting-card">
          <div class="setting-label">Thinking</div>
          <div class="setting-foot">Chat overrides inherit project defaults until you change them.</div>
        </div>
      `;
      return;
    }

    const modelValue = thread.model_override || "";
    const thinkingValue = thread.thinking_override || "";
    container.innerHTML = `
      <div class="setting-card">
        <div class="setting-label">Model</div>
        <select class="field-select" id="thread-model-select">
          <option value="">Inherit project default (${this._escapeHtml(project.default_model)})</option>
          ${models.map(
            (model) =>
              `<option value="${this._escapeHtml(model)}" ${model === modelValue ? "selected" : ""}>${this._escapeHtml(model)}</option>`
          ).join("")}
        </select>
        <div class="setting-foot">${modelValue ? `Override active · effective ${this._escapeHtml(thread.effective_model)}` : `Inherited from project · effective ${this._escapeHtml(thread.effective_model)}`}</div>
      </div>
      <div class="setting-card">
        <div class="setting-label">Thinking</div>
        <select class="field-select" id="thread-thinking-select">
          <option value="">Inherit project default (${this._escapeHtml(project.default_thinking_level)})</option>
          ${thinkingLevels.map(
            (level) =>
              `<option value="${this._escapeHtml(level)}" ${level === thinkingValue ? "selected" : ""}>${this._escapeHtml(this._titleCase(level))}</option>`
          ).join("")}
        </select>
        <div class="setting-foot">${thinkingValue ? `Override active · effective ${this._escapeHtml(thread.effective_thinking_level)}` : `Inherited from project · effective ${this._escapeHtml(thread.effective_thinking_level)}`}</div>
      </div>
    `;
  }

  _renderMessages() {
    const messageList = this.shadowRoot.getElementById("message-list");
    if (!this._selectedThreadId) {
      this._renderedThreadId = null;
      this._renderedSequence = 0;
      messageList.innerHTML = `<div class="empty-state"><div><div class="title">Start from Home Assistant</div><div class="empty-note">Pick a project, open a chat, and send prompts without leaving your HA dashboard.</div></div></div>`;
      return;
    }

    const shouldRebuild =
      this._forceMessageRebuild || this._renderedThreadId !== this._selectedThreadId;
    if (shouldRebuild) {
      this._renderedThreadId = this._selectedThreadId;
      this._renderedSequence = 0;
      this._forceMessageRebuild = false;
      messageList.innerHTML = "";
    }

    const shouldStick =
      messageList.scrollHeight - messageList.clientHeight - messageList.scrollTop < 80;
    const eventsToRender =
      this._renderedSequence === 0
        ? this._events
        : this._events.filter((event) => event.sequence > this._renderedSequence);

    if (!eventsToRender.length && !messageList.innerHTML) {
      messageList.innerHTML = `<div class="empty-state"><div><div class="title">Chat is ready</div><div class="empty-note">Send the first prompt when you're ready.</div></div></div>`;
      return;
    }

    if (eventsToRender.length && messageList.querySelector(".empty-state")) {
      messageList.innerHTML = "";
    }

    for (const event of eventsToRender) {
      messageList.insertAdjacentHTML("beforeend", this._renderEvent(event));
      this._renderedSequence = event.sequence;
    }

    if (shouldStick) {
      messageList.scrollTop = messageList.scrollHeight;
    }
  }

  _renderEvent(event) {
    if (event.event_type === "message.created") {
      return this._renderMessage("user", event.payload.text, event.sequence, false);
    }
    if (event.event_type === "message.completed") {
      return this._renderMessage("assistant", event.payload.text, event.sequence, true);
    }
    if (event.event_type === "run.started") {
      return `<div class="event-row">Run started</div>`;
    }
    if (event.event_type === "run.completed") {
      return `<div class="event-row">Run completed</div>`;
    }
    if (event.event_type === "run.failed") {
      return `<div class="event-row">Run failed: ${this._escapeHtml(event.payload.error || "Unknown error")}</div>`;
    }
    if (event.event_type === "artifact.added") {
      return `<div class="event-row">Output ready: ${this._escapeHtml(event.payload.filename || "artifact")}</div>`;
    }
    if (event.event_type === "thread.updated") {
      return `<div class="event-row">Chat settings updated</div>`;
    }
    return "";
  }

  _renderMessage(role, text, key, canCopy) {
    const icon = role === "user" ? icons.user : icons.bot;
    const messageHead = canCopy
      ? `
        <div class="message-head">
          <span class="thread-meta">Assistant</span>
          <button class="copy-button" type="button" data-action="copy-message" data-sequence="${key}" title="Copy response" aria-label="Copy response">
            ${icons.copy}
            <span>Copy</span>
          </button>
        </div>
      `
      : "";
    return `
      <article class="message ${role}" data-sequence="${key}">
        <span class="avatar">${icon}</span>
        <div class="bubble">
          ${messageHead}
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
            <button class="download-button" type="button" data-action="download-artifact" data-artifact-id="${artifact.artifact_id}" title="Download ${this._escapeHtml(artifact.filename)}" aria-label="Download ${this._escapeHtml(artifact.filename)}">
              ${icons.download}
            </button>
          </div>
        `
      )
      .join("");
  }

  async _loadStatus() {
    this._status = await this._callWS("get_status");
  }

  async _loadProjects() {
    this._projects = await this._callWS("list_projects");
    if (!this._selectedProjectId && this._projects.length) {
      this._selectedProjectId = this._projects[0].project_id;
    }
  }

  async _loadThreads() {
    this._threads = await this._callWS("list_threads");
    if (this._selectedThreadId && !this._threads.some((thread) => thread.thread_id === this._selectedThreadId)) {
      this._selectedThreadId = null;
    }
    if (!this._selectedThreadId && this._threads.length) {
      this._selectedThreadId = this._threads[0].thread_id;
    }
    if (!this._selectedProjectId && this._threads.length) {
      this._selectedProjectId = this._threads[0].project_id;
    }
    if (this._selectedThreadId) {
      await this._refreshActiveThread();
      this._startPolling();
    }
  }

  async _refreshActiveThread() {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      this._activeThread = await this._callWS("get_thread", {
        thread_id: this._selectedThreadId,
      });
      this._selectedProjectId = this._activeThread.project_id;
      await this._loadEvents(this._selectedThreadId, 0, true);
      this._artifacts = await this._callWS("list_artifacts", {
        thread_id: this._selectedThreadId,
      });
      await this._loadStatus();
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
      this._forceMessageRebuild = true;
    } else if (events.length) {
      this._events = [...this._events, ...events];
    }
    this._sequence = this._events.length ? this._events[this._events.length - 1].sequence : 0;
  }

  _openProjectFormForCreate() {
    const wasCreateMode = this._projectFormMode === "create";
    const wasVisible = this._showProjectForm;
    this._projectFormMode = "create";
    this._editingProjectId = null;
    this._showProjectForm = !(wasVisible && wasCreateMode);
    this._showThreadForm = false;
    this._projectForm = {
      name: "",
      rootPath: "",
      defaultModel: "gpt-5.4",
      defaultThinkingLevel: "medium",
    };
    this._folderDraft = "";
    this._browseState = null;
    this._render();
  }

  _openProjectFormForEdit(projectId) {
    const project = this._projects.find((item) => item.project_id === projectId);
    if (!project) {
      return;
    }
    this._projectFormMode = "edit";
    this._editingProjectId = projectId;
    this._showProjectForm = true;
    this._showThreadForm = false;
    this._projectForm = {
      name: project.name,
      rootPath: project.root_path,
      defaultModel: project.default_model,
      defaultThinkingLevel: project.default_thinking_level,
    };
    this._folderDraft = "";
    this._browseState = null;
    this._selectedProjectId = projectId;
    this._render();
  }

  _closeProjectForm() {
    this._showProjectForm = false;
    this._folderDraft = "";
    this._browseState = null;
    this._render();
  }

  _toggleThreadForm() {
    if (!this._selectedProjectId && this._projects.length) {
      this._selectedProjectId = this._projects[0].project_id;
    }
    this._showThreadForm = !this._showThreadForm;
    this._showProjectForm = false;
    if (this._showThreadForm) {
      this._threadForm.title = "";
      this._threadForm.mode = "full-auto";
    }
    this._render();
  }

  _openThreadFormForProject(projectId) {
    this._selectedProjectId = projectId || this._selectedProjectId;
    this._showThreadForm = true;
    this._showProjectForm = false;
    this._threadForm.title = "";
    this._threadForm.mode = "full-auto";
    this._render();
  }

  _selectProject(projectId) {
    this._selectedProjectId = projectId;
    if (this._selectedThreadId) {
      const selectedThread = this._threads.find((thread) => thread.thread_id === this._selectedThreadId);
      if (selectedThread && selectedThread.project_id !== projectId) {
        this._selectedThreadId = null;
        this._activeThread = null;
        this._events = [];
        this._artifacts = [];
        this._draft = "";
        this._forceMessageRebuild = true;
      }
    }
    this._render();
  }

  async _selectThread(threadId) {
    if (!threadId) {
      return;
    }
    this._selectedThreadId = threadId;
    this._sequence = 0;
    this._events = [];
    this._activeThread = null;
    this._artifacts = [];
    this._forceMessageRebuild = true;
    await this._refreshActiveThread();
    this._startPolling();
  }

  async _saveProject() {
    try {
      if (!this._projectForm.name.trim() || !this._projectForm.rootPath.trim()) {
        return;
      }

      let project;
      if (this._projectFormMode === "edit" && this._editingProjectId) {
        project = await this._callWS("update_project", {
          project_id: this._editingProjectId,
          name: this._projectForm.name.trim(),
          root_path: this._projectForm.rootPath.trim(),
          default_model: this._projectForm.defaultModel,
          default_thinking_level: this._projectForm.defaultThinkingLevel,
        });
      } else {
        project = await this._callWS("create_project", {
          name: this._projectForm.name.trim(),
          root_path: this._projectForm.rootPath.trim(),
          default_model: this._projectForm.defaultModel,
          default_thinking_level: this._projectForm.defaultThinkingLevel,
        });
      }

      this._selectedProjectId = project.project_id;
      this._showProjectForm = false;
      this._clearError();
      await this._loadProjects();
      await this._loadThreads();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _browseProjectPath(path) {
    try {
      this._browseState = await this._callWS("browse_paths", {
        path,
      });
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  _selectBrowseEntry(path) {
    this._projectForm.rootPath = path;
    this._browseProjectPath(path);
  }

  async _createFolder() {
    try {
      const parentPath = this._browseState?.path || this._projectForm.rootPath;
      if (!parentPath || !this._folderDraft.trim()) {
        return;
      }
      const created = await this._callWS("create_folder", {
        parent_path: parentPath,
        folder_name: this._folderDraft.trim(),
      });
      this._projectForm.rootPath = created.path;
      this._folderDraft = "";
      await this._browseProjectPath(parentPath);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }

  async _createThread() {
    try {
      const title = this._threadForm.title.trim();
      if (!title || !this._selectedProjectId) {
        return;
      }
      const thread = await this._callWS("create_thread", {
        title,
        project_id: this._selectedProjectId,
        mode: this._threadForm.mode,
      });
      this._threadForm.title = "";
      this._showThreadForm = false;
      this._selectedThreadId = thread.thread_id;
      this._selectedProjectId = thread.project_id;
      this._clearError();
      await this._loadThreads();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _updateThreadSettings(updates) {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      this._activeThread = await this._callWS("update_thread", {
        thread_id: this._selectedThreadId,
        ...updates,
      });
      this._syncThreadListStatus();
      this._clearError();
      this._render();
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
    if (!this._selectedThreadId || !artifactId) {
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

  async _copyMessage(sequence) {
    const numericSequence = Number(sequence);
    const event = this._events.find((item) => item.sequence === numericSequence);
    const text = event?.payload?.text || "";
    if (!text) {
      return;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const helper = document.createElement("textarea");
        helper.value = text;
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }
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
        this._pollTick += 1;
        const previousSequence = this._sequence;
        await this._loadEvents(this._selectedThreadId, this._sequence, false);
        const hasNewEvents = this._sequence !== previousSequence;
        const shouldRefreshStatus = this._pollTick % 4 === 0;
        if (shouldRefreshStatus) {
          await this._loadStatus();
        }
        if (this._activeThread?.status === "running" || hasNewEvents || shouldRefreshStatus) {
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
    if (!this._activeThread) {
      return;
    }
    this._threads = this._threads.map((thread) =>
      thread.thread_id === this._activeThread.thread_id ? this._activeThread : thread
    );
  }

  _activeProject() {
    if (this._activeThread) {
      return this._projects.find((project) => project.project_id === this._activeThread.project_id) || null;
    }
    return this._projects.find((project) => project.project_id === this._selectedProjectId) || null;
  }

  _effectiveMeta(thread) {
    return `${this._escapeHtml(thread.status)} · ${this._escapeHtml(thread.effective_model)} · ${this._escapeHtml(thread.effective_thinking_level)}`;
  }

  _limitCard(label, window, blocked, message) {
    if (!window) {
      return `
        <div class="limit-card ${blocked ? "blocked" : ""}">
          <div class="limit-label">${this._escapeHtml(label)}</div>
          <div class="limit-value">Unavailable</div>
          <div class="limit-subline">${this._escapeHtml(message || "Waiting for Codex to report a limit snapshot.")}</div>
        </div>
      `;
    }
    return `
      <div class="limit-card ${blocked ? "blocked" : ""}">
        <div class="limit-label">${this._escapeHtml(label)}</div>
        <div class="limit-value">${this._formatPercent(window.remaining_percent)}</div>
        <div class="limit-subline">Used ${this._formatPercent(window.used_percent)} · resets ${this._escapeHtml(this._formatReset(window.resets_at))}</div>
        ${blocked && message ? `<div class="limit-subline">${this._escapeHtml(message)}</div>` : ""}
      </div>
    `;
  }

  _formatPercent(value) {
    if (typeof value !== "number") {
      return "Unavailable";
    }
    return `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;
  }

  _formatReset(epochSeconds) {
    if (!epochSeconds) {
      return "unknown";
    }
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(epochSeconds * 1000));
    } catch (_error) {
      return "unknown";
    }
  }

  _modelOptions(selectedValue) {
    const models = this._status?.models || ["gpt-5.4"];
    return models
      .map(
        (model) =>
          `<option value="${this._escapeHtml(model)}" ${model === selectedValue ? "selected" : ""}>${this._escapeHtml(model)}</option>`
      )
      .join("");
  }

  _thinkingOptions(selectedValue) {
    const thinkingLevels = this._status?.thinking_levels || ["medium"];
    return thinkingLevels
      .map(
        (level) =>
          `<option value="${this._escapeHtml(level)}" ${level === selectedValue ? "selected" : ""}>${this._escapeHtml(this._titleCase(level))}</option>`
      )
      .join("");
  }

  _titleCase(value) {
    return String(value)
      .split("-")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  _setError(error) {
    this._error = error?.body?.message || error?.message || "Unexpected error";
    this._render();
  }

  _clearError() {
    this._error = "";
  }

  _escapeHtml(value) {
    return String(value ?? "")
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
