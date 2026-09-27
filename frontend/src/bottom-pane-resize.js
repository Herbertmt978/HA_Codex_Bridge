/** Visit-scoped sizing for the existing preview/terminal pane. */
export const bottomPaneResizeStyles = `
  .bottom-panel { position:relative; display:flex; flex-direction:column; }
  #bottom-preview { flex:1 1 0; min-height:0; overflow:auto; }
  #bottom-terminal { display:flex; flex-direction:column; flex:1 1 0; min-height:0; overflow:auto; }
  #bottom-terminal[hidden], #bottom-preview[hidden] { display:none; }
  #bottom-terminal > .terminal-note, #bottom-terminal > .terminal-tools { flex:0 0 auto; }
  #terminal-host { height:auto; flex:1 0 80px; min-height:80px; }
  .bottom-pane-resizer { position:sticky; top:0; z-index:3; height:8px; flex:0 0 8px; min-height:8px; margin:0; cursor:ns-resize; touch-action:none; outline-offset:-2px; }
  .bottom-pane-resizer::after { content:""; position:absolute; top:2px; left:calc(50% - 22px); width:44px; height:3px; border-radius:3px; background:var(--muted-color); opacity:0; }
  .bottom-pane-resizer:hover::after, .bottom-pane-resizer:focus-visible::after, .bottom-panel-resizing .bottom-pane-resizer::after { opacity:1; }
  .bottom-panel-header { top:8px; flex:0 0 auto; }
  .bottom-panel > .row-actions { flex:0 0 auto; }
  .bottom-pane-drag-shield { position:fixed; inset:0; z-index:200; cursor:ns-resize; touch-action:none; }
  .bottom-pane-drag-shield[hidden] { display:none; }
  .bottom-panel-resizing { user-select:none; }
  @media (pointer:coarse) { .bottom-pane-resizer::before { content:""; position:absolute; inset:-8px 0; } }
`;

export class BottomPaneResize {
  constructor(panel) {
    this.panel = panel;
    this.root = panel.shadowRoot;
    this.pane = this.root.getElementById('bottom-panel');
    this.handle = document.createElement('div');
    this.handle.className = 'bottom-pane-resizer';
    this.handle.tabIndex = 0;
    this.handle.setAttribute('role', 'separator');
    this.handle.setAttribute('aria-label', 'Resize file preview and terminal');
    this.handle.setAttribute('aria-orientation', 'horizontal');
    this.handle.setAttribute('aria-controls', 'bottom-panel');
    this.handle.title = 'Drag to resize. Arrow keys adjust; Home minimises, End maximises, Enter resets.';
    this.pane.prepend(this.handle);
    this.shield = document.createElement('div');
    this.shield.className = 'bottom-pane-drag-shield';
    this.shield.hidden = true;
    this.root.append(this.shield);
    this.handle.addEventListener('pointerdown', (event) => this.start(event));
    this.handle.addEventListener('pointermove', (event) => {
      if (this.drag?.id === event.pointerId) this.apply(this.drag.height + this.drag.y - event.clientY);
    });
    this.handle.addEventListener('pointerup', () => this.finish());
    this.handle.addEventListener('pointercancel', () => this.finish(true));
    this.handle.addEventListener('lostpointercapture', () => this.finish(true));
    this.handle.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.drag) { event.preventDefault(); this.finish(true); return; }
      const bounds = this.bounds();
      const current = this.panel._bottomPanelHeight || this.pane.getBoundingClientRect().height;
      const step = event.shiftKey ? 50 : 10;
      const next = { ArrowUp:current + step, ArrowDown:current - step, Home:bounds.min, End:bounds.max, Enter:bounds.default }[event.key];
      if (next !== undefined) { event.preventDefault(); this.apply(next); }
    });
    this.resize = () => { if (this.panel._bottomPanelOpen) this.apply(this.panel._bottomPanelHeight || this.bounds().default); };
    this.observer = typeof ResizeObserver === "function" ? new ResizeObserver(this.resize) : null;
  }
  bounds() {
    const main = this.pane.parentElement;
    const rect = main.getBoundingClientRect();
    const viewportBottom = (window.visualViewport?.height || window.innerHeight) + (window.visualViewport?.offsetTop || 0);
    const available = Math.max(0, Math.min(rect.bottom, viewportBottom) - Math.max(window.visualViewport?.offsetTop || 0, rect.top));
    const composer = this.root.querySelector('.composer-shell')?.getBoundingClientRect().height || 0;
    const header = this.root.querySelector('.main-header')?.getBoundingClientRect().height || 60;
    const min = Math.min(120, Math.max(64, available * .2));
    const max = Math.max(min, available - composer - header - 120);
    return { min:Math.round(min), max:Math.round(max), default:Math.round(Math.min(max, Math.max(min, available * .35))) };
  }
  apply(height) {
    const { min, max } = this.bounds();
    const value = Math.round(Math.min(max, Math.max(min, height)));
    this.panel._bottomPanelHeight = value;
    this.pane.style.flex = `0 0 ${value}px`;
    this.pane.style.minHeight = `${min}px`;
    this.pane.style.maxHeight = `${max}px`;
    this.handle.setAttribute('aria-valuemin', String(min));
    this.handle.setAttribute('aria-valuemax', String(max));
    this.handle.setAttribute('aria-valuenow', String(value));
    this.handle.setAttribute('aria-valuetext', `${value} pixels high`);
    this.panel._terminal?.resize();
  }
  start(event) {
    if (event.button !== 0 || !this.panel._bottomPanelOpen || this.drag) return;
    event.preventDefault();
    this.handle.focus({ preventScroll:true });
    this.drag = { id:event.pointerId, y:event.clientY, height:this.pane.getBoundingClientRect().height };
    this.handle.setPointerCapture(event.pointerId);
    this.shield.hidden = false;
    this.root.querySelector('.shell').classList.add('bottom-panel-resizing');
  }
  finish(cancel = false) {
    const drag = this.drag;
    if (!drag) return;
    this.drag = null;
    this.shield.hidden = true;
    this.root.querySelector('.shell').classList.remove('bottom-panel-resizing');
    if (this.handle.hasPointerCapture(drag.id)) this.handle.releasePointerCapture(drag.id);
    if (cancel) this.apply(drag.height);
  }
  connect() {
    window.addEventListener('resize', this.resize);
    window.visualViewport?.addEventListener('resize', this.resize);
    window.visualViewport?.addEventListener('scroll', this.resize);
    this.observer?.observe(this.pane.parentElement);
    this.observer?.observe(this.root.querySelector('.composer-shell'));
  }
  disconnect() {
    this.finish(true);
    this.observer?.disconnect();
    window.removeEventListener('resize', this.resize);
    window.visualViewport?.removeEventListener('resize', this.resize);
    window.visualViewport?.removeEventListener('scroll', this.resize);
  }
}
