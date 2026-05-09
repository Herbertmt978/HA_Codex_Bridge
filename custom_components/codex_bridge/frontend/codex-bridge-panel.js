const MODE_OPTIONS = [
  { value: "observe", label: "Observe" },
  { value: "edit", label: "Edit" },
  { value: "full-auto", label: "Full auto" },
];

const PREVIEWABLE_TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "csv",
  "json",
  "log",
  "py",
  "js",
  "ts",
  "tsx",
  "jsx",
  "html",
  "css",
  "yml",
  "yaml",
  "xml",
  "bas",
  "cls",
  "frm",
  "vb",
  "vbs",
  "ps1",
  "bat",
  "cmd",
  "ini",
  "cfg",
  "toml",
]);

const template = document.createElement("template");
template.innerHTML = `
  <style>
    :host {
      --panel-bg: var(--primary-background-color, #f5f7fb);
      --surface-bg: var(--card-background-color, #ffffff);
      --surface-alt: color-mix(in srgb, var(--surface-bg) 92%, #eef3fb 8%);
      --surface-muted: color-mix(in srgb, var(--surface-bg) 96%, #f4f7fb 4%);
      --border-color: color-mix(in srgb, var(--divider-color, #d7dde7) 85%, transparent);
      --text-color: var(--primary-text-color, #151b29);
      --muted-color: var(--secondary-text-color, #667085);
      --accent-color: var(--primary-color, #28a0f0);
      --accent-soft: color-mix(in srgb, var(--accent-color) 12%, white 88%);
      --danger-color: #e25563;
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
      background: var(--surface-bg);
      border-radius: 8px;
      cursor: pointer;
      padding: 0;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
    }

    button:hover {
      border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color) 45%);
      background: color-mix(in srgb, var(--surface-bg) 92%, var(--accent-soft) 8%);
    }

    input,
    textarea,
    select {
      border: 1px solid var(--border-color);
      background: var(--surface-bg);
      border-radius: 8px;
      outline: none;
    }

    input:focus,
    textarea:focus,
    select:focus {
      border-color: color-mix(in srgb, var(--accent-color) 68%, white 32%);
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(228px, 278px) minmax(0, 1fr) minmax(228px, 286px);
      gap: 12px;
      min-height: calc(100vh - 64px);
      padding: 12px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 95%, white 5%), var(--panel-bg));
    }

    .pane {
      min-width: 0;
      min-height: 0;
      background: color-mix(in srgb, var(--surface-bg) 98%, #f5f8fd 2%);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .rail-pane,
    .side-pane {
      position: relative;
    }

    .rail-header,
    .main-header,
    .side-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 98%, #f6f9fd 2%);
    }

    .title-block {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }

    .eyeline {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .title {
      font-size: 17px;
      font-weight: 600;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .subline,
    .meta-line,
    .row-meta,
    .status-text,
    .empty-note,
    .timestamp,
    .label-text {
      font-size: 12px;
      color: var(--muted-color);
      line-height: 1.45;
    }

    .hidden {
      display: none !important;
    }

    .section-scroll,
    .message-list,
    .side-scroll,
    .browse-list,
    .artifact-preview {
      overflow: auto;
      min-height: 0;
    }

    .section-scroll,
    .message-list,
    .side-scroll {
      flex: 1 1 auto;
    }

    .icon-button,
    .copy-button,
    .tool-button,
    .download-button,
    .action-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .icon-button,
    .download-button,
    .action-button {
      width: 34px;
      height: 34px;
      color: var(--muted-color);
      flex: 0 0 auto;
    }

    .icon-button.small,
    .download-button.small,
    .action-button.small {
      width: 28px;
      height: 28px;
      border-radius: 7px;
    }

    .tool-button {
      justify-content: flex-start;
      width: 100%;
      padding: 10px 12px;
      color: var(--text-color);
      background: transparent;
      border-color: transparent;
      border-radius: 10px;
    }

    .tool-button:hover {
      background: color-mix(in srgb, var(--accent-soft) 38%, white 62%);
      border-color: transparent;
    }

    .tool-button svg,
    .icon-button svg,
    .copy-button svg,
    .download-button svg,
    .send-button svg,
    .action-button svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
    }

    .tool-button span {
      font-size: 14px;
      font-weight: 500;
    }

    .rail-actions,
    .forms-stack {
      display: grid;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 98%, #f6f9fd 2%);
    }

    .search-shell {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      padding: 0 10px;
      height: 40px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: var(--surface-bg);
      color: var(--muted-color);
    }

    .search-shell input {
      border: 0;
      background: transparent;
      padding: 0;
      height: 100%;
      font-size: 14px;
      color: var(--text-color);
    }

    .search-shell input:focus {
      border: 0;
    }

    .panel-form {
      display: none;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      background: color-mix(in srgb, var(--surface-bg) 98%, #f7fbff 2%);
    }

    .panel-form.visible {
      display: grid;
    }

    .field,
    .field-select,
    .composer textarea {
      width: 100%;
      padding: 10px 12px;
      background: var(--surface-bg);
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .browser-card {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: color-mix(in srgb, var(--surface-bg) 99%, #f7fbff 1%);
    }

    .browser-label,
    .section-label,
    .setting-label,
    .limit-label {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .browse-list {
      display: grid;
      gap: 6px;
      max-height: 170px;
      padding-right: 2px;
    }

    .browse-row {
      text-align: left;
      padding: 9px 10px;
      border-radius: 8px;
      font-size: 13px;
    }

    .browser-actions,
    .form-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .text-button {
      height: 34px;
      padding: 0 12px;
      border-radius: 8px;
      color: var(--muted-color);
      font-size: 13px;
    }

    .send-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      height: 38px;
      padding: 0 16px;
      border-radius: 10px;
      color: white;
      background: linear-gradient(135deg, color-mix(in srgb, var(--accent-color) 90%, white 10%), color-mix(in srgb, var(--accent-color) 72%, #00d4ff 28%));
      border-color: transparent;
      font-weight: 600;
    }

    .send-button:hover {
      border-color: transparent;
      background: linear-gradient(135deg, color-mix(in srgb, var(--accent-color) 82%, white 18%), color-mix(in srgb, var(--accent-color) 64%, #00d4ff 36%));
    }

    .rail-sections {
      display: grid;
      gap: 4px;
      padding: 8px 10px 10px;
      align-content: start;
    }

    .rail-section {
      overflow: hidden;
      background: transparent;
    }

    .rail-section.flat {
      border: 0;
      background: transparent;
    }

    .section-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      padding: 8px 6px 6px;
      border-bottom: 0;
      background: transparent;
    }

    .section-head.compact {
      border-bottom: 0;
    }

    .section-head-button,
    .project-button {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      text-align: left;
      background: transparent;
      border: 0;
      padding: 0;
      color: inherit;
    }

    .section-head-button:hover,
    .project-button:hover {
      border: 0;
      background: transparent;
    }

    .section-title-line {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .section-title-line svg {
      width: 16px;
      height: 16px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
      color: var(--muted-color);
    }

    .section-name,
    .project-name,
    .thread-name {
      font-size: 14px;
      font-weight: 600;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .project-list,
    .chat-list {
      display: grid;
      gap: 1px;
    }

    .project-shell {
      display: grid;
      gap: 0;
    }

    .project-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 8px;
      padding: 8px 6px 4px;
      border-top: 0;
    }

    .project-shell:first-child .project-head {
      border-top: 0;
    }

    .project-head.active {
      background: transparent;
    }

    .project-meta {
      display: grid;
      gap: 1px;
      min-width: 0;
    }

    .project-actions,
    .row-actions {
      display: flex;
      gap: 6px;
      align-items: center;
      flex: 0 0 auto;
    }

    .chat-list {
      margin-left: 14px;
      padding: 0 0 8px 12px;
      border-left: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
    }

    .chat-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 6px;
      padding: 1px 0;
    }

    .chat-select {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      min-width: 0;
      padding: 8px 10px;
      text-align: left;
      border-radius: 9px;
      background: transparent;
      border: 1px solid transparent;
      color: inherit;
    }

    .chat-select.active {
      background: color-mix(in srgb, var(--accent-soft) 58%, white 42%);
      border-color: color-mix(in srgb, var(--accent-color) 28%, var(--border-color) 72%);
    }

    .chat-select:hover {
      background: color-mix(in srgb, var(--accent-soft) 36%, white 64%);
      border-color: transparent;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 10px;
      height: 10px;
      min-width: 10px;
      padding: 0;
      border-radius: 999px;
      background: color-mix(in srgb, var(--surface-bg) 92%, #eef3fb 8%);
      color: var(--muted-color);
      font-size: 0;
      border: 1px solid color-mix(in srgb, var(--border-color) 82%, transparent);
    }

    .status-pill.running {
      color: var(--accent-color);
      background: color-mix(in srgb, var(--accent-color) 12%, white 88%);
    }

    .status-pill.error {
      color: var(--danger-color);
      background: color-mix(in srgb, var(--danger-color) 10%, white 90%);
    }

    .status-pill.idle {
      color: #1dbf73;
      background: color-mix(in srgb, #1dbf73 12%, white 88%);
    }

    .main-pane {
      background: color-mix(in srgb, var(--surface-bg) 99%, #fafcff 1%);
    }

    .main-top {
      display: grid;
      gap: 6px;
      padding: 10px 14px 0;
    }

    .error-strip {
      display: none;
      min-height: 28px;
      padding: 7px 10px;
      border-radius: 8px;
      border: 1px solid color-mix(in srgb, var(--danger-color) 20%, transparent);
      background: color-mix(in srgb, var(--danger-color) 7%, white 93%);
      color: color-mix(in srgb, var(--danger-color) 88%, black 12%);
      font-size: 12px;
      line-height: 1.45;
    }

    .error-strip.visible {
      display: block;
    }

    .compact-toolbar {
      display: grid;
      grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.25fr);
      gap: 6px;
      align-items: stretch;
    }

    .toolbar-card {
      display: grid;
      gap: 6px;
      min-width: 0;
      padding: 7px 9px;
      border: 1px solid var(--border-color);
      border-radius: 9px;
      background: color-mix(in srgb, var(--surface-bg) 98%, #f7fbff 2%);
    }

    .toolbar-card.limits {
      align-content: start;
    }

    .toolbar-card.controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      align-items: start;
    }

    .limit-pair {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .mini-limit,
    .mini-control {
      display: grid;
      gap: 3px;
      min-width: 0;
    }

    .mini-limit-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      min-width: 0;
    }

    .mini-limit-name {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .limit-value {
      font-size: 14px;
      font-weight: 700;
      line-height: 1;
      white-space: nowrap;
    }

    .limit-subline,
    .setting-foot {
      font-size: 11px;
      color: var(--muted-color);
      line-height: 1.3;
    }

    .compact-select {
      width: 100%;
      min-width: 0;
      height: 30px;
      padding: 0 8px;
      border-radius: 8px;
      background: var(--surface-bg);
      font-size: 12px;
    }

    .mini-control .setting-label {
      margin-bottom: 1px;
    }

    .message-list {
      padding: 10px 16px 6px;
      display: grid;
      gap: 12px;
      align-content: start;
    }

    .message {
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }

    .message.user {
      grid-template-columns: minmax(0, 1fr) 28px;
    }

    .message.user .avatar {
      order: 2;
      justify-self: end;
    }

    .message.user .bubble {
      order: 1;
      justify-self: end;
    }

    .avatar {
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      color: var(--muted-color);
      background: color-mix(in srgb, var(--surface-bg) 92%, #eef3fb 8%);
      border: 1px solid var(--border-color);
    }

    .bubble {
      max-width: min(780px, 100%);
      min-width: 0;
      padding: 12px 14px;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      background: color-mix(in srgb, var(--surface-bg) 99%, #f8fbff 1%);
    }

    .message.user .bubble {
      background: color-mix(in srgb, var(--accent-soft) 55%, white 45%);
      border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color) 78%);
    }

    .message-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }

    .bubble-text {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace);
      font-size: 14px;
      line-height: 1.6;
      background: transparent;
      color: inherit;
      user-select: text;
      -webkit-user-select: text;
    }

    .copy-button {
      gap: 6px;
      min-width: 0;
      padding: 0 10px;
      height: 30px;
      border-radius: 8px;
      color: var(--muted-color);
      font-size: 12px;
    }

    .event-row {
      font-size: 12px;
      color: var(--muted-color);
      padding-left: 38px;
      user-select: text;
      -webkit-user-select: text;
    }

    .composer-shell {
      display: grid;
      gap: 10px;
      padding: 10px 16px 14px;
      border-top: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 98%, #f8fbff 2%);
    }

    .attachment-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }

    .attachment-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .attachment-chips {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
      min-height: 20px;
    }

    .attachment-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 100%;
      padding: 5px 9px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--surface-bg) 94%, #edf4ff 6%);
      border: 1px solid var(--border-color);
      font-size: 12px;
      color: var(--muted-color);
    }

    .attachment-chip strong {
      color: var(--text-color);
      font-weight: 600;
    }

    .composer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }

    .composer textarea {
      min-height: 112px;
      max-height: 280px;
      resize: vertical;
      line-height: 1.5;
      padding: 12px 14px;
    }

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 180px;
      padding: 18px;
      text-align: center;
      color: var(--muted-color);
    }

    .side-scroll {
      display: grid;
      gap: 10px;
      padding: 10px;
      align-content: start;
    }

    .side-section {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: color-mix(in srgb, var(--surface-bg) 99%, #f8fbff 1%);
    }

    .section-head-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .thread-actions {
      display: inline-flex;
      gap: 6px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 120ms ease;
    }

    .chat-row.selected .thread-actions,
    .chat-row.archived .thread-actions,
    .chat-row:hover .thread-actions,
    .chat-row:focus-within .thread-actions {
      opacity: 1;
      pointer-events: auto;
    }

    .progress-list,
    .context-list,
    .artifact-list {
      display: grid;
      gap: 8px;
    }

    .progress-row,
    .context-row,
    .file-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }

    .progress-row {
      grid-template-columns: 16px minmax(0, 1fr);
    }

    .progress-dot {
      width: 16px;
      height: 16px;
      border-radius: 999px;
      border: 2px solid color-mix(in srgb, var(--border-color) 82%, transparent);
      margin-top: 1px;
    }

    .progress-dot.complete {
      border-color: #1dbf73;
      background: color-mix(in srgb, #1dbf73 16%, white 84%);
    }

    .progress-dot.active {
      border-color: var(--accent-color);
      background: color-mix(in srgb, var(--accent-color) 14%, white 86%);
    }

    .progress-dot.error {
      border-color: var(--danger-color);
      background: color-mix(in srgb, var(--danger-color) 12%, white 88%);
    }

    .file-main {
      display: grid;
      gap: 2px;
      min-width: 0;
    }

    .file-name {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-color);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .file-select {
      width: 100%;
      text-align: left;
      background: transparent;
      border: 1px solid transparent;
      padding: 0;
      color: inherit;
    }

    .file-select.active .file-name {
      color: color-mix(in srgb, var(--accent-color) 84%, black 16%);
    }

    .file-row.active {
      padding: 8px 9px;
      margin: -4px -5px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent-soft) 48%, white 52%);
    }

    .artifact-preview {
      min-height: 220px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: var(--surface-bg);
      overflow: auto;
    }

    .artifact-preview pre {
      margin: 0;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace);
      font-size: 12px;
      line-height: 1.5;
      user-select: text;
      -webkit-user-select: text;
    }

    .artifact-preview img,
    .artifact-preview iframe {
      width: 100%;
      border: 0;
      display: block;
      background: white;
    }

    .artifact-preview img {
      height: auto;
      max-height: 560px;
      object-fit: contain;
    }

    .artifact-preview iframe {
      min-height: 420px;
    }

    .preview-empty,
    .preview-binary {
      display: grid;
      gap: 8px;
      place-items: center;
      min-height: 220px;
      padding: 16px;
      text-align: center;
      color: var(--muted-color);
    }

    @media (max-width: 1280px) {
      .shell {
        grid-template-columns: minmax(220px, 264px) minmax(0, 1fr);
      }

      .side-pane {
        grid-column: 1 / -1;
        min-height: 280px;
      }
    }

    @media (max-width: 880px) {
      .shell {
        grid-template-columns: 1fr;
      }

      .compact-toolbar {
        grid-template-columns: 1fr;
      }

      .field-grid {
        grid-template-columns: 1fr;
      }

      .composer {
        grid-template-columns: 1fr;
      }
    }
  </style>
  <div class="shell">
    <aside class="pane rail-pane">
      <div class="rail-header">
        <div class="title-block">
          <span class="eyeline">Workspace</span>
          <span class="title" id="panel-title">Codex Bridge</span>
        </div>
      </div>
      <div class="rail-actions">
        <button class="tool-button" type="button" data-action="new-direct-chat" id="new-direct-chat-button"></button>
        <button class="tool-button" type="button" data-action="toggle-project-form" id="new-project-button"></button>
        <label class="search-shell" for="search-input">
          <span id="search-icon"></span>
          <input id="search-input" type="text" placeholder="Search chats and projects" />
        </label>
      </div>
      <div class="forms-stack">
        <section class="panel-form" id="project-form-panel"></section>
        <section class="panel-form" id="thread-form-panel"></section>
      </div>
      <div class="section-scroll">
        <div class="rail-sections">
          <section class="rail-section" id="direct-section"></section>
          <section class="rail-section flat" id="project-section"></section>
          <section class="rail-section" id="archived-section"></section>
        </div>
      </div>
    </aside>

    <main class="pane main-pane">
      <div class="main-header">
        <div class="title-block">
          <span class="eyeline" id="thread-project-label">Ready</span>
          <span class="title" id="thread-title-label">Select a chat</span>
          <span class="subline" id="thread-path-label"></span>
        </div>
        <div class="row-actions">
          <div class="status-text" id="thread-status-text"></div>
          <button class="icon-button" type="button" data-action="refresh-thread" title="Refresh" aria-label="Refresh" id="refresh-thread-button"></button>
        </div>
      </div>
      <div class="main-top">
        <div class="error-strip" id="error-strip"></div>
        <div class="compact-toolbar" id="compact-toolbar"></div>
      </div>
      <div class="message-list" id="message-list"></div>
      <div class="composer-shell">
        <div class="attachment-toolbar">
          <div class="attachment-actions">
            <button class="icon-button" type="button" data-action="upload-file" title="Upload files" aria-label="Upload files" id="upload-file-button"></button>
            <button class="icon-button" type="button" data-action="upload-folder" title="Upload folder" aria-label="Upload folder" id="upload-folder-button"></button>
            <span class="label-text" id="attachment-meta"></span>
          </div>
          <div class="attachment-chips" id="attachment-chip-list"></div>
        </div>
        <div class="composer">
          <textarea id="prompt-input" placeholder="Message Codex through Home Assistant"></textarea>
          <button class="send-button" type="button" data-action="send-prompt" id="send-button"></button>
        </div>
        <input id="file-input" type="file" multiple class="hidden" />
        <input id="folder-input" type="file" webkitdirectory directory multiple class="hidden" />
      </div>
    </main>

    <aside class="pane side-pane">
      <div class="side-header">
        <div class="title-block">
          <span class="eyeline">Context</span>
          <span class="title">Progress and artifacts</span>
        </div>
      </div>
      <div class="side-scroll">
        <section class="side-section">
          <span class="section-label">Progress</span>
          <div class="progress-list" id="progress-list"></div>
        </section>
        <section class="side-section">
          <div class="section-head-row">
            <span class="section-label">Artifacts</span>
            <button class="icon-button small" type="button" data-action="create-workspace-archive" title="Zip this chat workspace" aria-label="Zip this chat workspace" id="workspace-archive-button"></button>
          </div>
          <div class="artifact-list" id="artifact-list"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Preview</span>
          <div class="artifact-preview" id="artifact-preview"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Details</span>
          <div class="context-list" id="context-list"></div>
        </section>
      </div>
    </aside>
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
  folderUpload: iconSvg('<path d="M3 7h6l2 2h10v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><path d="M12 17V9"></path><path d="m8.5 12.5 3.5-3.5 3.5 3.5"></path>'),
  send: iconSvg('<path d="m22 2-7 20-4-9-9-4 20-7Z"></path><path d="M22 2 11 13"></path>'),
  download: iconSvg('<path d="M12 4v12"></path><path d="m7 11 5 5 5-5"></path><path d="M5 20h14"></path>'),
  user: iconSvg('<path d="M20 21a8 8 0 1 0-16 0"></path><circle cx="12" cy="7" r="4"></circle>'),
  bot: iconSvg('<rect x="5" y="7" width="14" height="10" rx="4"></rect><path d="M12 3v4"></path><circle cx="10" cy="12" r="1"></circle><circle cx="14" cy="12" r="1"></circle>'),
  folder: iconSvg('<path d="M3 7h6l2 2h10v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2"></path>'),
  edit: iconSvg('<path d="M12 20h9"></path><path d="m16.5 3.5 4 4L8 20H4v-4Z"></path>'),
  chat: iconSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"></path>'),
  copy: iconSvg('<rect x="9" y="9" width="10" height="10" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'),
  save: iconSvg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"></path><path d="M17 21v-8H7v8"></path><path d="M7 3v5h8"></path>'),
  browse: iconSvg('<path d="M3 12h18"></path><path d="M12 3v18"></path>'),
  search: iconSvg('<circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>'),
  chevronDown: iconSvg('<path d="m6 9 6 6 6-6"></path>'),
  chevronRight: iconSvg('<path d="m9 6 6 6-6 6"></path>'),
  archive: iconSvg('<path d="M3 7h18"></path><path d="M5 7v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7"></path><path d="M9 11h6"></path><path d="M4 4h16v3H4z"></path>'),
  restore: iconSvg('<path d="M3 7h18"></path><path d="M5 7v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7"></path><path d="m9 14 3-3 3 3"></path><path d="M12 11v7"></path><path d="M4 4h16v3H4z"></path>'),
  trash: iconSvg('<path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path>'),
  package: iconSvg('<path d="m3 8.5 9-4.5 9 4.5"></path><path d="M21 8.5v7L12 20l-9-4.5v-7"></path><path d="M12 4v16"></path>'),
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
    this._artifactPreview = null;
    this._selectedArtifactId = null;
    this._previewToken = 0;
    this._sequence = 0;
    this._draft = "";
    this._searchQuery = "";
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
      projectId: null,
    };
    this._folderDraft = "";
    this._browseState = null;
    this._pollTimer = null;
    this._pollTick = 0;
    this._isLoading = false;
    this._error = "";
    this._renderedThreadId = null;
    this._renderedSequence = 0;
    this._renderedToolbarKey = "";
    this._forceMessageRebuild = true;
    this._pendingUploads = 0;
    this._suspendUiRefresh = false;
    this._queuedRender = false;
    this._collapsedProjects = {};
    this._collapsedSections = {
      direct: false,
      archived: true,
    };
  }

  connectedCallback() {
    this._installStaticUi();
    this._render();
  }

  disconnectedCallback() {
    this._stopPolling();
    this._revokePreviewUrl();
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
    this.shadowRoot.getElementById("new-direct-chat-button").innerHTML = `${icons.chat}<span>New chat</span>`;
    this.shadowRoot.getElementById("new-project-button").innerHTML = `${icons.plus}<span>New project</span>`;
    this.shadowRoot.getElementById("search-icon").innerHTML = icons.search;
    this.shadowRoot.getElementById("refresh-thread-button").innerHTML = icons.refresh;
    this.shadowRoot.getElementById("upload-file-button").innerHTML = icons.upload;
    this.shadowRoot.getElementById("upload-folder-button").innerHTML = icons.folderUpload;
    this.shadowRoot.getElementById("workspace-archive-button").innerHTML = icons.package;
    this.shadowRoot.getElementById("send-button").innerHTML = `${icons.send}<span>Send</span>`;

    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
    this.shadowRoot.addEventListener("change", (event) => this._handleChange(event));
    this.shadowRoot.addEventListener("paste", (event) => this._handlePaste(event));
    this.shadowRoot.addEventListener("focusin", (event) => this._handleFocusIn(event));
    this.shadowRoot.addEventListener("focusout", (event) => this._handleFocusOut(event));

    this.shadowRoot.getElementById("file-input").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length) {
        this._uploadFiles(files, { useRelativePaths: false });
      }
      event.target.value = "";
    });

    this.shadowRoot.getElementById("folder-input").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length) {
        this._uploadFiles(files, { useRelativePaths: true });
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
      case "new-direct-chat":
        this._openThreadFormForProject(null);
        break;
      case "toggle-project-form":
        this._openProjectFormForCreate();
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
      case "toggle-section":
        this._toggleSection(actionTarget.dataset.section || "");
        break;
      case "toggle-project-collapse":
        this._toggleProjectCollapse(actionTarget.dataset.projectId || "");
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
      case "archive-thread":
        this._archiveThread(actionTarget.dataset.threadId || "");
        break;
      case "restore-thread":
        this._restoreThread(actionTarget.dataset.threadId || "");
        break;
      case "delete-thread":
        this._deleteThread(actionTarget.dataset.threadId || "");
        break;
      case "send-prompt":
        this._sendPrompt();
        break;
      case "upload-file":
        this.shadowRoot.getElementById("file-input").click();
        break;
      case "upload-folder":
        this.shadowRoot.getElementById("folder-input").click();
        break;
      case "select-artifact":
        this._selectArtifact(actionTarget.dataset.artifactId || "");
        break;
      case "download-artifact":
        this._downloadArtifact(actionTarget.dataset.artifactId || "");
        break;
      case "create-workspace-archive":
        this._createWorkspaceArchive();
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
    if (target.id === "search-input") {
      this._searchQuery = target.value;
      this._render();
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

  _handlePaste(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || target.id !== "prompt-input") {
      return;
    }
    const files = this._clipboardFiles(event.clipboardData);
    if (!files.length) {
      return;
    }
    event.preventDefault();
    if (!this._selectedThreadId) {
      this._setError("Select a chat before pasting a screenshot.");
      return;
    }
    this._uploadFiles(files, { useRelativePaths: false });
  }

  _handleFocusIn(event) {
    const target = event.target;
    if (!this._isRefreshLockTarget(target)) {
      return;
    }
    this._suspendUiRefresh = true;
  }

  _handleFocusOut(event) {
    if (!this._isRefreshLockTarget(event.target)) {
      return;
    }
    window.setTimeout(() => {
      const activeElement = this.shadowRoot.activeElement;
      if (this._isRefreshLockTarget(activeElement)) {
        return;
      }
      this._suspendUiRefresh = false;
      if (this._queuedRender) {
        this._render(true);
      }
    }, 0);
  }

  _render(force = false) {
    if (!force && this._suspendUiRefresh) {
      this._queuedRender = true;
      return;
    }
    this._queuedRender = false;

    const activeThread = this._activeThread;
    const activeProject = this._activeProject();
    const contextName = activeProject?.kind === "direct" ? "Direct chat" : activeProject?.name || "Ready";

    this.shadowRoot.getElementById("panel-title").textContent = this._config?.panel_title || "Codex Bridge";
    this.shadowRoot.getElementById("thread-project-label").textContent = contextName;
    this.shadowRoot.getElementById("thread-title-label").textContent =
      activeThread?.title || (activeProject?.kind === "direct" ? "Select a chat" : activeProject?.name || "Select a chat");
    this.shadowRoot.getElementById("thread-path-label").textContent =
      activeThread?.workspace_path || activeProject?.root_path || "";
    this.shadowRoot.getElementById("thread-status-text").textContent =
      activeThread ? `Status: ${activeThread.status}` : "";
    this.shadowRoot.getElementById("attachment-meta").textContent = this._pendingUploads
      ? `Uploading ${this._pendingUploads} file${this._pendingUploads === 1 ? "" : "s"}`
      : activeThread
        ? `${activeThread.attachments.length} upload${activeThread.attachments.length === 1 ? "" : "s"} · paste screenshot`
        : "No chat selected";

    const errorStrip = this.shadowRoot.getElementById("error-strip");
    errorStrip.textContent = this._error;
    errorStrip.classList.toggle("visible", Boolean(this._error));

    this._renderProjectForm();
    this._renderThreadForm();
    this._renderDirectSection();
    this._renderProjectList();
    this._renderArchivedSection();
    this._renderToolbar();
    this._renderAttachmentChips();
    this._renderMessages();
    this._renderProgress();
    this._renderArtifacts();
    this._renderArtifactPreview();
    this._renderContext();
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
        <select class="field-select stable-select" id="project-model-select">${this._modelOptions(this._projectForm.defaultModel)}</select>
        <select class="field-select stable-select" id="project-thinking-select">${this._thinkingOptions(this._projectForm.defaultThinkingLevel)}</select>
      </div>
      <div class="browser-card">
        <span class="browser-label">Path browser</span>
        <div class="meta-line">${this._escapeHtml(this._browseState?.path || "No folder loaded yet")}</div>
        <div class="browser-actions">
          <button class="text-button" type="button" data-action="browse-current">Browse</button>
          <button class="text-button" type="button" data-action="browse-up">Up</button>
          <button class="text-button" type="button" data-action="browse-roots">Drives</button>
        </div>
        <div class="browse-list">${browseDirectories || `<div class="empty-note">Browse a path to list folders.</div>`}</div>
        <div class="browser-actions">
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

    const targetProject = this._threadForm.projectId
      ? this._projects.find((project) => project.project_id === this._threadForm.projectId) || null
      : this._directProject();
    const isDirect = !this._threadForm.projectId || targetProject?.kind === "direct";
    panel.innerHTML = `
      <div class="title-block">
        <span class="eyeline">${isDirect ? "New direct chat" : "New project chat"}</span>
        <span class="title">${targetProject ? this._escapeHtml(targetProject.name) : "Choose a target"}</span>
      </div>
      <input class="field" id="thread-title-input" type="text" placeholder="Chat title" value="${this._escapeHtml(this._threadForm.title)}" />
      <select class="field-select stable-select" id="thread-mode-select">
        ${MODE_OPTIONS.map(
          (option) =>
            `<option value="${option.value}" ${option.value === this._threadForm.mode ? "selected" : ""}>${this._escapeHtml(option.label)}</option>`
        ).join("")}
      </select>
      <div class="meta-line">${this._escapeHtml(targetProject?.root_path || "Pick a project or direct chat context first.")}</div>
      <div class="form-actions">
        <button class="send-button" type="button" data-action="save-thread">${icons.chat}<span>Create chat</span></button>
        <button class="text-button" type="button" data-action="cancel-thread-form">Close</button>
      </div>
    `;
  }

  _isRefreshLockTarget(target) {
    if (!(target instanceof HTMLElement)) {
      return false;
    }
    if (target.classList.contains("stable-select")) {
      return true;
    }
    if (!["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
      return false;
    }
    return Boolean(target.closest("#thread-form-panel, #project-form-panel"));
  }

  _clipboardFiles(clipboardData) {
    if (!clipboardData?.items?.length) {
      return [];
    }
    const files = [];
    for (const item of Array.from(clipboardData.items)) {
      if (item.kind !== "file") {
        continue;
      }
      const rawFile = item.getAsFile();
      if (!rawFile) {
        continue;
      }
      files.push(this._normalizeClipboardFile(rawFile, files.length));
    }
    return files;
  }

  _normalizeClipboardFile(file, index = 0) {
    if (file.name) {
      return file;
    }
    const extension = this._extensionFromMime(file.type);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const filename = `clipboard-${stamp}${index ? `-${index + 1}` : ""}.${extension}`;
    return new File([file], filename, {
      type: file.type || "application/octet-stream",
      lastModified: Date.now(),
    });
  }

  _extensionFromMime(mimeType) {
    const map = {
      "image/png": "png",
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/gif": "gif",
      "image/bmp": "bmp",
    };
    return map[mimeType] || "bin";
  }

  _renderDirectSection() {
    const section = this.shadowRoot.getElementById("direct-section");
    const directThreads = this._directThreads(false);
    const collapsed = Boolean(this._collapsedSections.direct);
    const directProject = this._directProject();
    const hasMatches = directThreads.length || !this._searchQuery.trim();

    if (!directProject && !hasMatches) {
      section.innerHTML = "";
      return;
    }

    section.innerHTML = `
      <div class="section-head ${collapsed ? "compact" : ""}">
        <button class="section-head-button" type="button" data-action="toggle-section" data-section="direct">
          <div class="section-title-line">
            ${collapsed ? icons.chevronRight : icons.chevronDown}
            ${icons.chat}
            <span class="section-name">Direct chats</span>
          </div>
        </button>
        <div class="project-actions">
          <button class="icon-button small" type="button" data-action="new-direct-chat" title="New direct chat" aria-label="New direct chat">${icons.plus}</button>
        </div>
      </div>
      ${collapsed ? "" : `
        <div class="chat-list">
          ${directThreads.length
            ? directThreads.map((thread) => this._threadRow(thread)).join("")
            : `<div class="empty-note">No direct chats yet.</div>`}
        </div>
      `}
    `;
  }

  _renderProjectList() {
    const section = this.shadowRoot.getElementById("project-section");
    const projects = this._projects.filter((project) => project.kind !== "direct");
    const visibleProjects = projects.filter((project) => this._projectIsVisible(project));

    if (!projects.length) {
      section.innerHTML = `
        <div class="section-head compact">
          <div class="section-title-line">
            ${icons.folder}
            <span class="section-name">Projects</span>
          </div>
        </div>
        <div class="empty-state"><div><div class="title">No projects yet</div><div class="empty-note">Create a project and point it at a real folder on the VM.</div></div></div>
      `;
      return;
    }

    section.innerHTML = `
      <div class="section-head compact">
        <div class="section-title-line">
          ${icons.folder}
          <span class="section-name">Projects</span>
        </div>
      </div>
      <div class="project-list">
        ${visibleProjects.length
          ? visibleProjects.map((project) => this._projectSection(project)).join("")
          : `<div class="empty-state"><div><div class="title">No matches</div><div class="empty-note">Try a broader search.</div></div></div>`}
      </div>
    `;
  }

  _projectSection(project) {
    const threads = this._projectThreads(project.project_id, false);
    const collapsed = Boolean(this._collapsedProjects[project.project_id]);
    const active = this._selectedProjectId === project.project_id || this._activeThread?.project_id === project.project_id;
    const chatCount = threads.length === 1 ? "1 chat" : `${threads.length} chats`;
    return `
      <section class="project-shell">
        <div class="project-head ${active ? "active" : ""}">
          <button class="project-button" type="button" data-action="select-project" data-project-id="${project.project_id}">
            <div class="project-meta">
              <div class="section-title-line">
                <span data-action="toggle-project-collapse" data-project-id="${project.project_id}">
                  ${collapsed ? icons.chevronRight : icons.chevronDown}
                </span>
                 ${icons.folder}
                 <span class="project-name">${this._escapeHtml(project.name)}</span>
               </div>
               <span class="row-meta">${this._escapeHtml(chatCount)}</span>
             </div>
           </button>
           <div class="project-actions">
             <button class="icon-button small" type="button" data-action="new-chat" data-project-id="${project.project_id}" title="New chat" aria-label="New chat">${icons.plus}</button>
             ${project.kind === "project" ? `<button class="icon-button small" type="button" data-action="edit-project" data-project-id="${project.project_id}" title="Edit project" aria-label="Edit project">${icons.edit}</button>` : ""}
          </div>
        </div>
        ${collapsed ? "" : `
          <div class="chat-list">
            ${threads.length ? threads.map((thread) => this._threadRow(thread)).join("") : `<div class="empty-note">No chats yet.</div>`}
          </div>
        `}
      </section>
    `;
  }

  _renderArchivedSection() {
    const section = this.shadowRoot.getElementById("archived-section");
    const archivedThreads = this._threads.filter((thread) => Boolean(thread.archived_at) && this._threadMatchesQuery(thread));
    const collapsed = Boolean(this._collapsedSections.archived);

    if (!archivedThreads.length) {
      section.innerHTML = "";
      return;
    }

    section.innerHTML = `
      <div class="section-head ${collapsed ? "compact" : ""}">
        <button class="section-head-button" type="button" data-action="toggle-section" data-section="archived">
          <div class="section-title-line">
            ${collapsed ? icons.chevronRight : icons.chevronDown}
            ${icons.archive}
            <span class="section-name">Archived</span>
          </div>
        </button>
      </div>
      ${collapsed ? "" : `
        <div class="chat-list">
          ${archivedThreads.map((thread) => this._threadRow(thread, { archived: true })).join("")}
        </div>
      `}
    `;
  }

  _threadRow(thread, { archived = false } = {}) {
    const statusClass = thread.status === "running" ? "running" : thread.status === "error" ? "error" : "idle";
    const meta = `${thread.effective_model} / ${thread.effective_thinking_level}`;
    const timestamp = this._timeAgo(thread.updated_at || thread.created_at);
    const selected = thread.thread_id === this._selectedThreadId;
    return `
      <div class="chat-row ${selected ? "selected" : ""} ${archived ? "archived" : ""}">
        <button class="chat-select ${selected ? "active" : ""}" type="button" data-action="select-thread" data-thread-id="${thread.thread_id}">
          <div class="title-block">
            <span class="thread-name">${this._escapeHtml(thread.title)}</span>
            <span class="row-meta">${this._escapeHtml(meta)}</span>
           </div>
           <span class="timestamp">${this._escapeHtml(timestamp)}</span>
         </button>
         <div class="row-actions">
           <span class="status-pill ${statusClass}" title="Status: ${this._escapeHtml(thread.status)}" aria-label="Status ${this._escapeHtml(thread.status)}"></span>
           <div class="thread-actions">
             ${archived
              ? `<button class="action-button small" type="button" data-action="restore-thread" data-thread-id="${thread.thread_id}" title="Restore chat" aria-label="Restore chat">${icons.restore}</button>`
              : `<button class="action-button small" type="button" data-action="archive-thread" data-thread-id="${thread.thread_id}" title="Archive chat" aria-label="Archive chat">${icons.archive}</button>`}
             <button class="action-button small" type="button" data-action="delete-thread" data-thread-id="${thread.thread_id}" title="Delete chat" aria-label="Delete chat">${icons.trash}</button>
           </div>
         </div>
       </div>
     `;
  }

  _renderToolbar() {
    const container = this.shadowRoot.getElementById("compact-toolbar");
    const thread = this._activeThread;
    const project = this._activeProject();
    const models = this._status?.models || ["gpt-5.4"];
    const thinkingLevels = this._status?.thinking_levels || ["medium"];
    const limits = this._status?.limits;
    const toolbarKey = JSON.stringify({
      threadId: thread?.thread_id || null,
      projectId: project?.project_id || null,
      modelOverride: thread?.model_override || null,
      thinkingOverride: thread?.thinking_override || null,
      effectiveModel: thread?.effective_model || null,
      effectiveThinking: thread?.effective_thinking_level || null,
      limits,
      models,
      thinkingLevels,
    });
    if (toolbarKey === this._renderedToolbarKey) {
      return;
    }

    const modelValue = thread?.model_override || "";
    const thinkingValue = thread?.thinking_override || "";
    container.innerHTML = `
      <div class="toolbar-card limits">
        <span class="setting-label">Limits</span>
        <div class="limit-pair">
          ${this._compactLimitCard("5h", limits?.primary)}
          ${this._compactLimitCard("Week", limits?.secondary)}
        </div>
        <span class="setting-foot">${this._escapeHtml(this._limitsFootnote(limits))}</span>
      </div>
      <div class="toolbar-card controls">
        <div class="mini-control">
          <span class="setting-label">Model</span>
          ${thread
            ? `
              <select class="compact-select stable-select" id="thread-model-select">
                <option value="">${this._escapeHtml(project?.default_model ? `Inherit (${project.default_model})` : "Inherit default")}</option>
                ${models.map(
                  (model) =>
                    `<option value="${this._escapeHtml(model)}" ${model === modelValue ? "selected" : ""}>${this._escapeHtml(model)}</option>`
                ).join("")}
              </select>
              <span class="setting-foot">Effective ${this._escapeHtml(thread.effective_model || project?.default_model || "gpt-5.4")}</span>
            `
            : `<span class="setting-foot">Select a chat.</span>`}
        </div>
        <div class="mini-control">
          <span class="setting-label">Thinking</span>
          ${thread
            ? `
              <select class="compact-select stable-select" id="thread-thinking-select">
                <option value="">${this._escapeHtml(project?.default_thinking_level ? `Inherit (${project.default_thinking_level})` : "Inherit default")}</option>
                ${thinkingLevels.map(
                  (level) =>
                    `<option value="${this._escapeHtml(level)}" ${level === thinkingValue ? "selected" : ""}>${this._escapeHtml(this._titleCase(level))}</option>`
                ).join("")}
              </select>
              <span class="setting-foot">Effective ${this._escapeHtml(thread.effective_thinking_level || project?.default_thinking_level || "medium")}</span>
            `
            : `<span class="setting-foot">Select a chat.</span>`}
        </div>
      </div>
    `;
    this._renderedToolbarKey = toolbarKey;
  }

  _compactLimitCard(label, windowInfo) {
    if (!windowInfo) {
      return `
        <div class="mini-limit">
          <div class="mini-limit-head">
            <span class="mini-limit-name">${this._escapeHtml(label)}</span>
            <span class="limit-value">--</span>
          </div>
          <span class="limit-subline">Unavailable</span>
        </div>
      `;
    }
    return `
      <div class="mini-limit">
        <div class="mini-limit-head">
          <span class="mini-limit-name">${this._escapeHtml(label)}</span>
          <span class="limit-value">${this._formatPercent(windowInfo.remaining_percent)}</span>
        </div>
        <span class="limit-subline">${this._escapeHtml(this._formatReset(windowInfo.resets_at))}</span>
      </div>
    `;
  }

  _limitsFootnote(limits) {
    if (!limits) {
      return "No limit snapshot yet.";
    }
    if (limits.blocked && limits.message) {
      return limits.message;
    }
    const plan = limits.plan_type ? this._titleCase(limits.plan_type) : "Codex";
    const updated = limits.updated_at ? this._timeAgo(limits.updated_at) : "now";
    return `${plan} usage snapshot ${updated}`;
  }

  _renderAttachmentChips() {
    const container = this.shadowRoot.getElementById("attachment-chip-list");
    const attachments = this._activeThread?.attachments || [];
    if (!attachments.length) {
      container.innerHTML = "";
      return;
    }

    const visible = attachments.slice(-6);
    container.innerHTML = `
      ${visible.map((attachment) => `
        <span class="attachment-chip">
          <strong>${this._escapeHtml(attachment.filename)}</strong>
          <span>${this._escapeHtml(attachment.relative_path || attachment.mime_type || "")}</span>
        </span>
      `).join("")}
      ${attachments.length > visible.length ? `<span class="attachment-chip">+${attachments.length - visible.length} more</span>` : ""}
    `;
  }

  _renderMessages() {
    const messageList = this.shadowRoot.getElementById("message-list");
    if (!this._selectedThreadId) {
      this._renderedThreadId = null;
      this._renderedSequence = 0;
      messageList.innerHTML = `<div class="empty-state"><div><div class="title">Start from Home Assistant</div><div class="empty-note">Choose a direct chat or a project chat and send your first prompt.</div></div></div>`;
      return;
    }

    const shouldRebuild = this._forceMessageRebuild || this._renderedThreadId !== this._selectedThreadId;
    if (shouldRebuild) {
      this._renderedThreadId = this._selectedThreadId;
      this._renderedSequence = 0;
      this._forceMessageRebuild = false;
      messageList.innerHTML = "";
    }

    const shouldStick = messageList.scrollHeight - messageList.clientHeight - messageList.scrollTop < 80;
    const eventsToRender =
      this._renderedSequence === 0
        ? this._events
        : this._events.filter((item) => item.sequence > this._renderedSequence);

    if (!eventsToRender.length && !messageList.innerHTML) {
      messageList.innerHTML = `<div class="empty-state"><div><div class="title">Chat is ready</div><div class="empty-note">Send the first prompt when you are ready.</div></div></div>`;
      return;
    }

    if (eventsToRender.length && messageList.querySelector(".empty-state")) {
      messageList.innerHTML = "";
    }

    for (const event of eventsToRender) {
      const html = this._renderEvent(event);
      if (!html) {
        this._renderedSequence = event.sequence;
        continue;
      }
      messageList.insertAdjacentHTML("beforeend", html);
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
    if (event.event_type === "attachment.added") {
      return `<div class="event-row">Uploaded ${this._escapeHtml(event.payload.relative_path || event.payload.filename || "file")}</div>`;
    }
    if (event.event_type === "artifact.added") {
      return `<div class="event-row">Artifact ready: ${this._escapeHtml(event.payload.relative_path || event.payload.filename || "artifact")}</div>`;
    }
    if (event.event_type === "thread.updated") {
      return `<div class="event-row">Chat settings updated</div>`;
    }
    if (event.event_type === "thread.archived") {
      return `<div class="event-row">Chat archived</div>`;
    }
    if (event.event_type === "thread.restored") {
      return `<div class="event-row">Chat restored</div>`;
    }
    return "";
  }

  _renderMessage(role, text, key, canCopy) {
    const icon = role === "user" ? icons.user : icons.bot;
    const head = canCopy
      ? `
        <div class="message-head">
          <span class="row-meta">Assistant</span>
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
          ${head}
          <pre class="bubble-text">${this._escapeHtml(text || "")}</pre>
        </div>
      </article>
    `;
  }

  _renderProgress() {
    const container = this.shadowRoot.getElementById("progress-list");
    const items = this._progressItems();
    if (!items.length) {
      container.innerHTML = `<div class="empty-note">No progress yet.</div>`;
      return;
    }
    container.innerHTML = items
      .map(
        (item) => `
          <div class="progress-row">
            <span class="progress-dot ${this._escapeHtml(item.state)}"></span>
            <div class="title-block">
              <span class="thread-name">${this._escapeHtml(item.title)}</span>
              ${item.meta ? `<span class="row-meta">${this._escapeHtml(item.meta)}</span>` : ""}
            </div>
          </div>
        `
      )
      .join("");
  }

  _progressItems() {
    const items = [];
    if (this._config?.bridge_url) {
      items.push({
        title: "Bridge connected",
        meta: this._config.bridge_url,
        state: "complete",
      });
    }
    if (this._activeThread) {
      items.push({
        title: this._activeThread.status === "running" ? "Run in progress" : "Chat selected",
        meta: this._activeThread.title,
        state: this._activeThread.status === "running" ? "active" : this._activeThread.status === "error" ? "error" : "complete",
      });
    }
    if (this._pendingUploads) {
      items.push({
        title: "Uploading files",
        meta: `${this._pendingUploads} remaining`,
        state: "active",
      });
    } else if ((this._activeThread?.attachments || []).length) {
      items.push({
        title: "Attachments available",
        meta: `${this._activeThread.attachments.length} uploaded`,
        state: "complete",
      });
    }

    const notable = this._events
      .filter((event) =>
        ["run.failed", "run.completed", "artifact.added", "thread.updated", "thread.archived", "thread.restored"].includes(event.event_type)
      )
      .slice(-4)
      .reverse()
      .map((event) => this._progressItemFromEvent(event));

    return [...items, ...notable].slice(0, 8);
  }

  _progressItemFromEvent(event) {
    if (event.event_type === "run.failed") {
      return {
        title: "Run failed",
        meta: event.payload.error || "Unknown error",
        state: "error",
      };
    }
    if (event.event_type === "run.completed") {
      return {
        title: "Run completed",
        meta: this._timeAgo(event.timestamp),
        state: "complete",
      };
    }
    if (event.event_type === "artifact.added") {
      return {
        title: "Artifact ready",
        meta: event.payload.relative_path || event.payload.filename || "artifact",
        state: "complete",
      };
    }
    if (event.event_type === "thread.updated") {
      return {
        title: "Chat settings updated",
        meta: this._timeAgo(event.timestamp),
        state: "complete",
      };
    }
    if (event.event_type === "thread.archived") {
      return {
        title: "Chat archived",
        meta: this._timeAgo(event.timestamp),
        state: "complete",
      };
    }
    return {
      title: "Chat restored",
      meta: this._timeAgo(event.timestamp),
      state: "complete",
    };
  }

  _renderArtifacts() {
    const container = this.shadowRoot.getElementById("artifact-list");
    this._syncSelectedArtifact();

    if (!this._artifacts.length) {
      container.innerHTML = `<div class="empty-note">No files yet.</div>`;
      return;
    }

    container.innerHTML = this._artifacts
      .map((artifact) => {
        const active = artifact.artifact_id === this._selectedArtifactId;
        return `
          <div class="file-row ${active ? "active" : ""}">
            <button class="file-select ${active ? "active" : ""}" type="button" data-action="select-artifact" data-artifact-id="${artifact.artifact_id}">
              <div class="file-main">
                <span class="file-name">${this._escapeHtml(artifact.relative_path || artifact.filename)}</span>
                <span class="row-meta">${this._escapeHtml(artifact.mime_type)}${artifact.size_bytes ? ` / ${this._formatBytes(artifact.size_bytes)}` : ""}</span>
              </div>
            </button>
            <button class="download-button small" type="button" data-action="download-artifact" data-artifact-id="${artifact.artifact_id}" title="Download ${this._escapeHtml(artifact.filename)}" aria-label="Download ${this._escapeHtml(artifact.filename)}">
              ${icons.download}
            </button>
          </div>
        `;
      })
      .join("");
  }

  _renderArtifactPreview() {
    const container = this.shadowRoot.getElementById("artifact-preview");
    if (!this._selectedArtifactId) {
      container.innerHTML = `<div class="preview-empty"><div>Select an artifact to preview it here.</div></div>`;
      return;
    }
    if (!this._artifactPreview || this._artifactPreview.artifactId !== this._selectedArtifactId) {
      container.innerHTML = `<div class="preview-empty"><div>Loading preview...</div></div>`;
      return;
    }

    const preview = this._artifactPreview;
    if (preview.kind === "text") {
      container.innerHTML = `<pre>${this._escapeHtml(preview.text || "")}</pre>`;
      return;
    }
    if (preview.kind === "image") {
      container.innerHTML = `<img src="${this._escapeHtml(preview.url || "")}" alt="${this._escapeHtml(preview.filename || "artifact preview")}" />`;
      return;
    }
    if (preview.kind === "pdf") {
      container.innerHTML = `<iframe src="${this._escapeHtml(preview.url || "")}" title="${this._escapeHtml(preview.filename || "artifact preview")}"></iframe>`;
      return;
    }

    container.innerHTML = `
      <div class="preview-binary">
        <div>${this._escapeHtml(preview.filename || "Artifact preview unavailable")}</div>
        <div class="empty-note">${this._escapeHtml(preview.contentType || "Binary file")}</div>
      </div>
    `;
  }

  _renderContext() {
    const container = this.shadowRoot.getElementById("context-list");
    const thread = this._activeThread;
    const project = this._activeProject();
    const rows = [
      ["Workspace", thread?.workspace_path || project?.root_path || "Not selected"],
      ["Context", project?.kind === "direct" ? "Direct chats" : project?.name || "Not selected"],
      ["Mode", thread?.mode || "full-auto"],
      ["Model", thread?.effective_model || project?.default_model || "gpt-5.4"],
      ["Thinking", thread?.effective_thinking_level || project?.default_thinking_level || "medium"],
      ["Uploads", String(thread?.attachments?.length || 0)],
      ["Files", String(this._artifacts.length)],
    ];

    container.innerHTML = rows
      .map(
        ([label, value]) => `
          <div class="context-row">
            <span class="label-text">${this._escapeHtml(label)}</span>
            <span class="row-meta">${this._escapeHtml(value)}</span>
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
    if (!this._selectedProjectId) {
      this._selectedProjectId = this._directProject()?.project_id || this._projects[0]?.project_id || null;
    }
  }

  async _loadThreads() {
    this._threads = await this._callWS("list_threads", {
      include_archived: true,
    });
    if (this._selectedThreadId && !this._threads.some((thread) => thread.thread_id === this._selectedThreadId)) {
      this._selectedThreadId = null;
    }
    if (!this._selectedThreadId) {
      const firstActive = this._threads.find((thread) => !thread.archived_at);
      this._selectedThreadId = firstActive?.thread_id || null;
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
      this._activeThread = null;
      this._events = [];
      this._artifacts = [];
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      this._render();
      return;
    }

    try {
      const threadId = this._selectedThreadId;
      const [thread, events, artifacts, status] = await Promise.all([
        this._callWS("get_thread", { thread_id: threadId }),
        this._callWS("get_events", { thread_id: threadId, after: 0 }),
        this._callWS("list_artifacts", { thread_id: threadId }),
        this._callWS("get_status"),
      ]);
      this._activeThread = thread;
      this._selectedProjectId = thread.project_id;
      this._events = events;
      this._sequence = this._events.length ? this._events[this._events.length - 1].sequence : 0;
      this._artifacts = artifacts;
      this._status = status;
      this._forceMessageRebuild = true;
      this._syncThreadListStatus();
      this._syncSelectedArtifact();
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
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
    if (!project || project.kind !== "project") {
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

  _openThreadFormForProject(projectId) {
    this._showThreadForm = true;
    this._showProjectForm = false;
    this._threadForm = {
      title: "",
      mode: "full-auto",
      projectId,
    };
    this._selectedProjectId = projectId || this._directProject()?.project_id || this._selectedProjectId;
    this._render();
  }

  _toggleSection(section) {
    if (!section) {
      return;
    }
    this._collapsedSections[section] = !this._collapsedSections[section];
    this._render();
  }

  _toggleProjectCollapse(projectId) {
    if (!projectId) {
      return;
    }
    this._collapsedProjects[projectId] = !this._collapsedProjects[projectId];
    this._render();
  }

  _selectProject(projectId) {
    this._selectedProjectId = projectId;
    const visibleThread = this._threads.find((thread) => thread.project_id === projectId && !thread.archived_at);
    if (visibleThread && visibleThread.thread_id !== this._selectedThreadId) {
      this._selectThread(visibleThread.thread_id);
      return;
    }
    if (!visibleThread) {
      this._selectedThreadId = null;
      this._activeThread = null;
      this._events = [];
      this._artifacts = [];
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      this._forceMessageRebuild = true;
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
    this._selectedArtifactId = null;
    this._revokePreviewUrl();
    this._artifactPreview = null;
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
      this._browseState = await this._callWS("browse_paths", { path });
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
      if (!title) {
        return;
      }
      const payload = {
        title,
        mode: this._threadForm.mode,
      };
      if (this._threadForm.projectId) {
        payload.project_id = this._threadForm.projectId;
      }
      const thread = await this._callWS("create_thread", payload);
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

  async _uploadFiles(files, { useRelativePaths }) {
    if (!this._selectedThreadId || !files.length) {
      return;
    }
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      this._pendingUploads = files.length;
      this._render();
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file, file.name);
        const relativePath = useRelativePaths
          ? file.webkitRelativePath || file.relativePath || file.name
          : null;
        if (relativePath) {
          formData.append("relative_path", relativePath);
        }
        const response = await fetch(`/api/codex_bridge/threads/${this._selectedThreadId}/attachments`, {
          method: "POST",
          headers,
          body: formData,
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.message || "Upload failed");
        }
        this._pendingUploads -= 1;
        this._render();
      }
      this._clearError();
      await this._refreshActiveThread();
    } catch (error) {
      this._setError(error);
    } finally {
      this._pendingUploads = 0;
      this._render();
    }
  }

  async _archiveThread(threadId) {
    if (!threadId) {
      return;
    }
    try {
      const archived = await this._callWS("archive_thread", { thread_id: threadId });
      this._threads = this._threads.map((thread) => (thread.thread_id === threadId ? archived : thread));
      if (this._selectedThreadId === threadId) {
        const replacement = this._threads.find((thread) => !thread.archived_at && thread.thread_id !== threadId);
        this._selectedThreadId = replacement?.thread_id || null;
        if (this._selectedThreadId) {
          await this._refreshActiveThread();
        } else {
          this._activeThread = null;
          this._events = [];
          this._artifacts = [];
          this._selectedArtifactId = null;
          this._revokePreviewUrl();
          this._artifactPreview = null;
          this._forceMessageRebuild = true;
        }
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _restoreThread(threadId) {
    if (!threadId) {
      return;
    }
    try {
      const restored = await this._callWS("restore_thread", { thread_id: threadId });
      this._threads = this._threads.map((thread) => (thread.thread_id === threadId ? restored : thread));
      this._selectedThreadId = threadId;
      this._selectedProjectId = restored.project_id;
      await this._refreshActiveThread();
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _deleteThread(threadId) {
    if (!threadId || !window.confirm("Delete this chat? Project files will be left in place.")) {
      return;
    }
    try {
      await this._callWS("delete_thread", { thread_id: threadId });
      this._threads = this._threads.filter((thread) => thread.thread_id !== threadId);
      if (this._selectedThreadId === threadId) {
        const replacement = this._threads.find((thread) => !thread.archived_at) || null;
        this._selectedThreadId = replacement?.thread_id || null;
        this._selectedProjectId = replacement?.project_id || this._directProject()?.project_id || null;
        if (replacement) {
          await this._refreshActiveThread();
        } else {
          this._activeThread = null;
          this._events = [];
          this._artifacts = [];
          this._selectedArtifactId = null;
          this._revokePreviewUrl();
          this._artifactPreview = null;
          this._forceMessageRebuild = true;
        }
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _createWorkspaceArchive() {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      const artifact = await this._callWS("create_workspace_archive", {
        thread_id: this._selectedThreadId,
      });
      this._artifacts = await this._callWS("list_artifacts", { thread_id: this._selectedThreadId });
      this._selectedArtifactId = artifact.artifact_id;
      this._artifactPreview = null;
      await this._loadArtifactPreview(artifact.artifact_id);
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  async _selectArtifact(artifactId) {
    if (!artifactId || artifactId === this._selectedArtifactId) {
      return;
    }
    this._selectedArtifactId = artifactId;
    this._artifactPreview = null;
    this._render();
    await this._loadArtifactPreview(artifactId);
  }

  async _loadArtifactPreview(artifactId) {
    if (!this._selectedThreadId || !artifactId) {
      return;
    }
    const artifact = this._artifacts.find((item) => item.artifact_id === artifactId);
    if (!artifact) {
      return;
    }
    const previewToken = ++this._previewToken;
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(`/api/codex_bridge/threads/${this._selectedThreadId}/artifacts/${artifactId}`, {
        headers,
      });
      if (!response.ok) {
        throw new Error("Preview failed");
      }
      const blob = await response.blob();
      if (previewToken !== this._previewToken || artifactId !== this._selectedArtifactId) {
        return;
      }

      this._revokePreviewUrl();
      const descriptor = this._previewDescriptor(artifact, blob);
      if (descriptor.kind === "text") {
        descriptor.text = await blob.text();
      } else if (descriptor.kind === "image" || descriptor.kind === "pdf") {
        descriptor.url = URL.createObjectURL(blob);
      }
      this._artifactPreview = descriptor;
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }

  _previewDescriptor(artifact, blob) {
    const contentType = blob.type || artifact.mime_type || "application/octet-stream";
    const extension = (artifact.filename.split(".").pop() || "").toLowerCase();
    if (contentType.startsWith("image/")) {
      return {
        artifactId: artifact.artifact_id,
        filename: artifact.filename,
        contentType,
        kind: "image",
        url: null,
      };
    }
    if (contentType === "application/pdf" || extension === "pdf") {
      return {
        artifactId: artifact.artifact_id,
        filename: artifact.filename,
        contentType,
        kind: "pdf",
        url: null,
      };
    }
    if (contentType.startsWith("text/") || PREVIEWABLE_TEXT_EXTENSIONS.has(extension)) {
      return {
        artifactId: artifact.artifact_id,
        filename: artifact.filename,
        contentType,
        kind: "text",
        text: "",
      };
    }
    return {
      artifactId: artifact.artifact_id,
      filename: artifact.filename,
      contentType,
      kind: "binary",
    };
  }

  _revokePreviewUrl() {
    if (this._artifactPreview?.url) {
      URL.revokeObjectURL(this._artifactPreview.url);
    }
  }

  async _downloadArtifact(artifactId) {
    if (!this._selectedThreadId || !artifactId) {
      return;
    }
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(`/api/codex_bridge/threads/${this._selectedThreadId}/artifacts/${artifactId}`, {
        headers,
      });
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
        const previousStatus = this._activeThread?.status;
        const shouldRefreshStatus = this._pollTick % 8 === 0;
        const [events, status, thread] = await Promise.all([
          this._callWS("get_events", {
            thread_id: this._selectedThreadId,
            after: this._sequence,
          }),
          shouldRefreshStatus ? this._callWS("get_status") : Promise.resolve(this._status),
          this._activeThread?.status === "running" || shouldRefreshStatus
            ? this._callWS("get_thread", { thread_id: this._selectedThreadId })
            : Promise.resolve(null),
        ]);
        if (events.length) {
          this._events = [...this._events, ...events];
          this._sequence = this._events[this._events.length - 1].sequence;
        }
        if (status) {
          this._status = status;
        }

        const hasNewEvents = this._sequence !== previousSequence;
        const shouldRefreshThread = Boolean(thread) || hasNewEvents;
        if (shouldRefreshThread) {
          this._activeThread = thread || (await this._callWS("get_thread", { thread_id: this._selectedThreadId }));
          this._syncThreadListStatus();
          if (
            this._activeThread.status !== "running" &&
            (hasNewEvents || previousStatus === "running" || shouldRefreshStatus)
          ) {
            this._artifacts = await this._callWS("list_artifacts", { thread_id: this._selectedThreadId });
            this._syncSelectedArtifact();
          }
          this._render();
        }
      } catch (error) {
        this._setError(error);
      }
    }, 1100);
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

  _syncSelectedArtifact() {
    if (!this._artifacts.length) {
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      return;
    }
    const stillExists = this._artifacts.some((artifact) => artifact.artifact_id === this._selectedArtifactId);
    if (stillExists) {
      return;
    }
    const previewCandidate = this._artifacts.find((artifact) => {
      const extension = (artifact.filename.split(".").pop() || "").toLowerCase();
      return artifact.mime_type.startsWith("image/") || artifact.mime_type === "application/pdf" || PREVIEWABLE_TEXT_EXTENSIONS.has(extension) || artifact.mime_type.startsWith("text/");
    }) || this._artifacts[0];
    this._selectedArtifactId = previewCandidate.artifact_id;
    this._artifactPreview = null;
    this._loadArtifactPreview(this._selectedArtifactId);
  }

  _activeProject() {
    if (this._activeThread) {
      return this._projects.find((project) => project.project_id === this._activeThread.project_id) || null;
    }
    return this._projects.find((project) => project.project_id === this._selectedProjectId) || null;
  }

  _directProject() {
    return this._projects.find((project) => project.kind === "direct") || null;
  }

  _directThreads(includeArchived) {
    return this._threads.filter(
      (thread) =>
        thread.project_kind === "direct" &&
        (includeArchived || !thread.archived_at) &&
        this._threadMatchesQuery(thread)
    );
  }

  _projectThreads(projectId, includeArchived) {
    return this._threads.filter(
      (thread) =>
        thread.project_id === projectId &&
        (includeArchived || !thread.archived_at) &&
        this._threadMatchesQuery(thread)
    );
  }

  _projectIsVisible(project) {
    if (project.kind === "direct") {
      return false;
    }
    const query = this._searchQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const haystack = `${project.name} ${project.root_path}`.toLowerCase();
    if (haystack.includes(query)) {
      return true;
    }
    return this._threads.some(
      (thread) =>
        thread.project_id === project.project_id &&
        !thread.archived_at &&
        this._threadMatchesQuery(thread)
    );
  }

  _threadMatchesQuery(thread) {
    const query = this._searchQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const haystack = `${thread.title} ${thread.workspace_path} ${thread.effective_model} ${thread.effective_thinking_level}`.toLowerCase();
    return haystack.includes(query);
  }

  _limitState() {
    return this._status?.limits || null;
  }

  _formatPercent(value) {
    if (typeof value !== "number") {
      return "--";
    }
    return `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;
  }

  _formatReset(epochSeconds) {
    if (!epochSeconds) {
      return "unknown";
    }
    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(new Date(epochSeconds * 1000));
    } catch (_error) {
      return "unknown";
    }
  }

  _formatBytes(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "";
    }
    if (value < 1024) {
      return `${value} B`;
    }
    const units = ["KB", "MB", "GB"];
    let size = value / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`;
  }

  _timeAgo(timestamp) {
    if (!timestamp) {
      return "";
    }
    const value = new Date(timestamp).getTime();
    if (Number.isNaN(value)) {
      return "";
    }
    const deltaMinutes = Math.max(0, Math.round((Date.now() - value) / 60000));
    if (deltaMinutes < 1) {
      return "now";
    }
    if (deltaMinutes < 60) {
      return `${deltaMinutes}m`;
    }
    const deltaHours = Math.round(deltaMinutes / 60);
    if (deltaHours < 24) {
      return `${deltaHours}h`;
    }
    const deltaDays = Math.round(deltaHours / 24);
    if (deltaDays < 7) {
      return `${deltaDays}d`;
    }
    const deltaWeeks = Math.round(deltaDays / 7);
    if (deltaWeeks < 5) {
      return `${deltaWeeks}w`;
    }
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
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
