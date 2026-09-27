/** Presentation only: move existing controls, never duplicate their state owners. */
export const compactComposerStyles = `
  .compact-composer-bar { grid-column:1 / -1; grid-row:2; display:flex; align-items:center; gap:6px; min-width:0; }
  .compact-composer-bar .icon-button { flex:0 0 auto; width:32px; min-width:32px; height:32px; border:0; background:transparent; }
  .compact-composer-bar .compact-model-button { margin-left:auto; min-width:0; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .compact-composer-bar .compact-context-summary[hidden] { display:none; }
  .compact-composer-bar .compact-context-summary { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:20%; }
  .compact-composer-bar .compact-options-button { width:auto; max-width:28%; gap:4px; }
  .compact-options-summary { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-control-size); }
  .compact-options-summary:empty { display:none; }
  .compact-composer-bar .send-button { flex:0 0 auto; width:32px; min-width:32px; height:32px; padding:6px; border-radius:50%; background:var(--text-color); color:var(--surface-bg); }
  .composer-shell .attachment-toolbar, .composer-shell > #composer-diagnostics, .composer-shell .composer-actions { display:none !important; }
  .composer-shell .composer-status:empty { display:none; }
  .compact-surface { position:fixed; z-index:75; width:min(420px,calc(100vw - 16px)); max-height:calc(100dvh - 16px); overflow:auto; padding:12px; border:1px solid var(--border-color); border-radius:14px; background:var(--surface-bg); color:var(--text-color); box-shadow:0 8px 28px color-mix(in srgb,var(--text-color) 16%,transparent); }
  .compact-surface[hidden] { display:none !important; }
  .compact-surface-header { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .compact-surface-header h2 { flex:1; margin:0; font-size:var(--font-body-size); }
  .compact-surface-header button { min-height:32px; }
  .compact-surface-body { min-width:0; }
  .compact-surface .compact-surface-link { display:flex; align-items:center; gap:10px; width:100%; min-height:44px; border:0; background:transparent; padding:8px 10px; text-align:left; }
  .compact-surface .compact-surface-link[hidden] { display:none; }
  .compact-surface .compact-surface-link:hover { background:var(--surface-muted); }
  .compact-surface .row-actions, .compact-surface #collaboration-controls, .compact-surface #compact-toolbar { display:flex; flex-wrap:wrap; gap:8px; }
  .compact-surface #collaboration-controls > .composer-utility, .compact-surface #compact-toolbar > .composer-utility { flex:1 1 100%; justify-content:space-between; min-width:0; padding:6px 0; border:0; }
  .compact-surface input { max-width:100%; min-width:0; }
  .compact-surface #workspace-context, .compact-surface #chat-context { padding:0; max-height:none; overflow:visible; }
  .compact-surface #goal-controls { margin:0; }
  .compact-surface .attachment-chips { position:static; max-height:180px; overflow:auto; }
  .compact-surface .row-meta { overflow-wrap:anywhere; }
  .compact-surface #conversation-search { padding:0; border:0; }
  .message:has(.message-actions) { grid-template-columns:minmax(0,1fr) 32px; column-gap:4px; }
  .message:has(.message-actions) > .bubble { grid-column:1; grid-row:1; }
  .message:has(.message-actions) > .message-time { grid-column:1 / -1; grid-row:1; }
  .message:has(.message-time):has(.message-actions) > .bubble, .message:has(.message-time) > .message-actions { grid-row:2; }
  .message-actions { grid-column:2; grid-row:1; margin-top:0; justify-content:flex-end; }

  .message-actions-menu { position:fixed; z-index:100; width:200px; max-height:calc(100dvh - 16px); overflow:auto; padding:6px; border:1px solid var(--border-color); border-radius:12px; background:var(--surface-bg); box-shadow:0 8px 24px #0003; }
  .message-actions-menu[hidden] { display:none; }
  .message-selection-help { padding:4px 8px; margin:0; }
  .message-actions-menu .composer-limits-button { width:100%; height:auto; min-height:36px; justify-content:flex-start; padding:8px; }
  .message-actions-trigger { font-size:22px; }
  .message-actions > .composer-limits-button { display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px; padding:6px; }
  .message-actions .composer-limits-button svg { width:18px; height:18px; }
  @media (max-width:880px) {
    .composer-shell { grid-template-columns:minmax(0,1fr); }
    .compact-composer-bar { gap:2px; }
    .compact-composer-bar .icon-button, .compact-composer-bar .send-button { width:44px; min-width:44px; height:44px; }
    .compact-composer-bar .compact-options-button { width:auto; }
    .compact-composer-bar .compact-model-button { min-height:44px; max-width:none; flex:1; padding:6px 2px; }
    .compact-composer-bar .compact-context-summary { max-width:48px; padding:6px 2px; }
    .compact-surface .compact-select, .compact-surface button, .compact-surface input, .compact-surface summary { min-height:44px; }
    .message:has(.message-actions) { grid-template-columns:minmax(0,1fr) 44px; }
    .message-actions > .composer-limits-button { width:44px; height:44px; }
    .message-actions-menu .composer-limits-button { min-height:44px; }
  }
`;

export class CompactComposer {
  constructor(panel, icons) {
    this.panel = panel;
    this.icons = icons;
    this.root = panel.shadowRoot;
    this.surfaces = new Map();
    this.openPage = null;
    this.trigger = null;
    this.threadId = panel._selectedThreadId;
    const shell = this.root.querySelector('.composer-shell');
    this.bar = document.createElement('div');
    this.bar.className = 'compact-composer-bar';
    this.bar.setAttribute('role', 'group');
    this.bar.setAttribute('aria-label', 'Message toolbar');
    shell.append(this.bar);
    const plus = this.root.getElementById('add-menu-button');
    this.bar.append(plus);
    this.options = this.button('Turn options', 'options', 'settings', true);
    this.options.classList.add('compact-options-button');
    this.optionSummary = document.createElement('span'); this.optionSummary.className = 'compact-options-summary'; this.options.append(this.optionSummary);
    this.context = this.button('Selected context', 'add');
    this.context.classList.add('compact-context-summary');
    this.model = this.button('Model and thinking', 'models');
    this.model.classList.add('compact-model-button');
    this.bar.append(this.options, this.context, this.model);
    for (const id of ['context-usage-button', 'dictation-button', 'stop-run-button', 'send-button']) this.bar.append(this.root.getElementById(id));
    this.surface('options', 'Turn options', ['collaboration-controls', 'elapsed-limit-note', 'draft-recovery-controls']);
    this.surface('models', 'Model, thinking and allowance', ['compact-toolbar']);
    this.surface('files', 'Files and workspace context', ['upload-file-button', 'upload-folder-button', 'attachment-meta', 'attachment-chip-list', 'workspace-context']);
    this.surface('previous', 'Previous chat context', ['chat-context']);
    this.surface('review', 'Repository review', ['git-composer-review', 'git-context']);
    this.surface('goal', 'Goal — manual continuation', ['goal-controls']);
    this.surface('search', 'Find in chat', ['conversation-search']);
    const add = this.surface('add', 'Add to chat', []);
    for (const [label, page] of [['Files and workspace context', 'files'], ['Previous chat context', 'previous'], ['Repository review', 'review']]) add.append(this.button(label, page));
    for (const id of ['schedule-message-button', 'add-plugins-button']) add.append(this.root.getElementById(id));
    this.root.getElementById('add-menu').hidden = true;
    this.root.addEventListener('change', () => queueMicrotask(() => this.sync()));
    this.outside = (event) => {
      const path = event.composedPath();
      if (!path.some((node) => node?.classList?.contains('message-actions'))) this.panel._openMessageActions?.();
      if (!this.openPage) return;
      if (!path.includes(this.surfaces.get(this.openPage)) && !path.includes(this.trigger)) this.close();
    };
    this.resize = () => this.place();
    this.key = (event) => {
      if (event.key === 'Tab' && !this.panel._openMessageActions) for (const actions of this.root.querySelectorAll('.message-actions')) actions._captureMenuSelection?.();
      if (!this.openPage) return;
      const surface = this.surfaces.get(this.openPage);
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.close(true); }
      const target = event.composedPath()[0];
      if (!surface.contains(target)) return;
      if (event.key === 'Tab') {
        const controls = [...surface.querySelectorAll('button,input,select,textarea,summary')].filter((node) => !node.disabled && node.getClientRects().length);
        if ((event.shiftKey && target === controls[0]) || (!event.shiftKey && target === controls.at(-1))) { event.preventDefault(); event.stopPropagation(); this.close(true); }
      }
    };
    this.observer = new MutationObserver(() => this.sync());
    for (const id of ['compact-toolbar', 'workspace-context', 'chat-context', 'goal-controls', 'attachment-chip-list']) this.observer.observe(this.root.getElementById(id), { childList:true, subtree:true });
  }

  connect() {
    for (const id of ['compact-toolbar', 'workspace-context', 'chat-context', 'goal-controls', 'attachment-chip-list']) this.observer.observe(this.root.getElementById(id), { childList:true, subtree:true });
    document.addEventListener('pointerdown', this.outside); window.addEventListener('resize', this.resize);
    document.addEventListener('keydown', this.key, true);
  }
  disconnect() { this.panel._openMessageActions?.(); this.close(); this.observer.disconnect(); document.removeEventListener('pointerdown', this.outside); document.removeEventListener('keydown', this.key, true); window.removeEventListener('resize', this.resize); }
  button(label, page, icon = '', compact = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = compact ? 'icon-button' : 'composer-limits-button compact-surface-link';
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', `compact-surface-${page}`);
    if (icon) this.panel._setTrustedButtonContent(button, this.icons[icon]);
    else button.textContent = label;
    button.addEventListener('click', () => this.open(page, button));
    return button;
  }
  surface(page, label, ids) {
    const surface = document.createElement('section');
    surface.id = `compact-surface-${page}`;
    surface.className = 'compact-surface';
    surface.hidden = true;
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-label', label);
    const header = document.createElement('div'); header.className = 'compact-surface-header';
    const title = document.createElement('h2'); title.textContent = label;
    const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Close'; close.setAttribute('aria-label', `Close ${label}`);
    close.addEventListener('click', () => this.close(true)); header.append(title, close);
    const body = document.createElement('div'); body.className = 'compact-surface-body';
    for (const id of ids) { const node = this.root.getElementById(id); if (node) body.append(node); }
    surface.append(header, body); this.root.append(surface); this.surfaces.set(page, surface);
    return body;
  }
  canOpen(page) {
    const capabilities = this.panel._config?.capabilities || [];
    if (page === 'search') return capabilities.includes('conversation_search_v1');
    if (page === 'review') return capabilities.includes('git_context_v1') || capabilities.includes('git_review_v1');
    return true;
  }
  open(page, trigger) {
    if (!this.surfaces.has(page) || !this.canOpen(page)) return;
    this.sync();
    if (this.openPage === page) { this.close(true); return; }
    const returnTo = this.openPage === 'add' ? this.trigger : trigger;
    this.close(); this.panel._chatContextMenu.close(); this.panel._setAddMenuOpen(false);
    this.openPage = page; this.trigger = returnTo || this.root.getElementById('chat-menu-button');
    if (page === 'search') this.panel._renderConversationSearch();
    if (page === 'review') void this.panel._loadGitContext(true);
    this.trigger?.setAttribute('aria-expanded', 'true');
    const surface = this.surfaces.get(page); surface.hidden = false;
    for (const details of surface.querySelectorAll('#workspace-context, #chat-context, #goal-controls > details')) details.open = true;
    this.sync(); this.place();
    surface.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
  }
  close(focus = false) {
    const search = this.openPage === 'search';
    if (this.openPage) this.surfaces.get(this.openPage).hidden = true;
    this.trigger?.setAttribute('aria-expanded', 'false');
    if (focus && this.trigger?.isConnected) this.trigger.focus({preventScroll:true});
    this.openPage = null; this.trigger = null;
    if (search) this.panel._renderConversationSearch();
  }
  place() {
    if (!this.openPage) return;
    const surface = this.surfaces.get(this.openPage);
    const view = window.visualViewport;
    const left = view?.offsetLeft || 0, top = view?.offsetTop || 0;
    const width = view?.width || window.innerWidth, height = view?.height || window.innerHeight;
    const anchor = this.trigger?.getBoundingClientRect() || this.bar.getBoundingClientRect();
    surface.style.maxHeight = `${Math.max(44,height-16)}px`;
    const bounds = surface.getBoundingClientRect();
    surface.style.left = `${Math.max(left+8,Math.min(anchor.left,left+width-bounds.width-8))}px`;
    surface.style.top = `${Math.max(top+8,Math.min(anchor.top-bounds.height-8,top+height-bounds.height-8))}px`;
  }
  sync() {
    const p = this.panel;
    if (this.openPage && !this.canOpen(this.openPage)) this.close(true);
    for (const link of this.surfaces.get('add').querySelectorAll('[aria-controls="compact-surface-review"]')) link.hidden = !this.canOpen('review');
    if (this.threadId !== p._selectedThreadId || p._activeDestination !== 'chats') { this.close(); this.threadId = p._selectedThreadId; }
    const choices = [];
    if (p._collaborationMode === 'plan') choices.push('Plan');
    if (p._followUpMode === 'steer') choices.push('Steer');
    const duration = this.root.getElementById('elapsed-time-limit');
    if (duration?.value) choices.push(duration.selectedOptions[0]?.textContent || 'Time limit');
    const search = this.root.getElementById('web-search-mode');
    if (search?.value && search.value !== 'configured') choices.push(`Search ${search.selectedOptions[0]?.textContent}`);
    const state = choices.length ? `Turn options: ${choices.join(', ')}` : 'Turn options';
    this.optionSummary.textContent = choices.join(' · ');
    this.options.setAttribute('aria-label', state); this.options.title = state; this.options.dataset.active = String(choices.length > 0);
    const workspace = p._workspaceContext.current(), chats = p._chatContext.current();
    const count = workspace.length + chats.length + (p._activeThread?.attachments?.length || 0);
    this.context.hidden = !count;
    const stale = [...workspace,...chats].some((item) => item.stale);
    this.context.textContent = `${stale ? 'Review ' : ''}${count} context`;
    this.context.setAttribute('aria-label', `Inspect selected context: ${count} items${stale ? ', changed context requires review' : ''}`);
    const model = this.root.getElementById('thread-model-select'), thinking = this.root.getElementById('thread-thinking-select');
    const clean = (select) => (select?.selectedOptions[0]?.textContent || '').replace(/^Inherit \((.*)\)$/, '$1');
    const text = [clean(model), clean(thinking)].filter(Boolean).join(' · ') || 'Model and thinking';
    this.model.textContent = text; this.model.setAttribute('aria-label', `Model and thinking: ${text}. Open allowance and settings`);
    const goal = p._goalControls?.view?.goal;
    const header = this.root.getElementById('chat-menu-button');
    header?.setAttribute('aria-label', goal ? `Chat actions. Goal ${goal.status}` : 'Chat actions');
    if (this.openPage && !this.root.activeElement && (document.activeElement === document.body || document.activeElement === p)) {
      this.surfaces.get(this.openPage).querySelector('button:not(:disabled)')?.focus({preventScroll:true});
    }
    this.place();
  }
}
