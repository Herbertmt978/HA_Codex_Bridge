import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import terminalCss from "@xterm/xterm/css/xterm.css?inline";

export { terminalCss };

// The panel connects only through HA's administrator WebSocket API. Session
// output is ephemeral; it is never added to the chat or browser storage.
export class WorkspaceTerminalView {
  constructor(host, call, status) {
    this.host = host;
    this.call = call;
    this.status = status;
    this.epoch = 0;
    this.session = null;
    this.term = null;
    this.pendingBytes = 0;
    this.queue = Promise.resolve();
  }

  async open(threadId) {
    await this.close();
    const epoch = ++this.epoch;
    this.status("Starting workspace terminal", true);
    try {
      const snapshot = await this.call("open", { thread_id: threadId, cols: 80, rows: 15 });
      if (epoch !== this.epoch) {
        await this.call("close", { thread_id: threadId, session_id: snapshot.session_id });
        return;
      }
      this.session = { thread_id: threadId, session_id: snapshot.session_id };
      this.cursor = 0;
      this.inputSequence = 0;
      this.term = new Terminal({
        cursorBlink: true, fontSize: 13, scrollback: 2000, screenReaderMode: true,
        allowProposedApi: false, disableStdin: true,
        theme: { background: "#171717", foreground: "#eeeeee" },
        linkHandler: { activate: () => {}, hover: () => {}, leave: () => {} },
      });
      this.fit = new FitAddon();
      this.term.loadAddon(this.fit);
      this.term.open(this.host);
      this.term.parser.registerOscHandler(52, () => true);
      this.term.attachCustomKeyEventHandler((event) => {
        if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "m") {
          this.host.parentElement.querySelector('[data-action="close-terminal"]')?.focus();
          return false;
        }
        return true;
      });
      this.term.onData((data) => this._input(data, epoch));
      this._apply(snapshot);
      this.observer = new ResizeObserver(() => {
        window.clearTimeout(this.resizeTimer);
        this.resizeTimer = window.setTimeout(() => this.resize(), 150);
      });
      this.observer.observe(this.host);
      this.resize();
      this.term.focus();
      this._schedule(epoch, 100);
    } catch {
      if (epoch === this.epoch) {
        await this.close();
        this.status("Could not open the terminal. Close any active Codex turn and use an editable chat.", false);
      }
    }
  }

  _apply(snapshot) {
    if (!this.term) return;
    if (snapshot.truncated) {
      this.term.reset();
      this.term.writeln("[Earlier terminal output was discarded while disconnected.]");
    }
    for (const chunk of snapshot.chunks || []) {
      if (chunk.sequence <= this.cursor) continue;
      this.term.write(Uint8Array.from(atob(chunk.data), (character) => character.charCodeAt(0)));
      this.cursor = chunk.sequence;
    }
    const wasRunning = this.running;
    this.running = snapshot.state === "running";
    this.term.options.disableStdin = !this.running;
    this.status(snapshot.message || "Workspace terminal", snapshot.state !== "closed");
    if (this.running && !wasRunning) this.resize();
  }

  _schedule(epoch, delay) {
    this.timer = window.setTimeout(() => void this._poll(epoch), delay);
  }

  async _poll(epoch) {
    if (epoch !== this.epoch || !this.session) return;
    try {
      const snapshot = await this.call("read", { ...this.session, after: this.cursor });
      if (epoch !== this.epoch) return;
      this._apply(snapshot);
      if (snapshot.state === "closed" && !snapshot.has_more) return;
      this._schedule(epoch, snapshot.has_more ? 30 : 500);
    } catch {
      if (epoch !== this.epoch) return;
      await this.close();
      this.status("Terminal disconnected. Open a new terminal to continue.", false);
    }
  }

  _input(data, epoch) {
    if (!this.running || !this.session || epoch !== this.epoch) return;
    const size = new TextEncoder().encode(data).length;
    if (size > 16 * 1024 || this.pendingBytes + size > 64 * 1024) {
      this.status("Paste is too large. Paste fewer than 16 KB at a time.", true);
      return;
    }
    this.pendingBytes += size;
    this.queue = this.queue.then(async () => {
      if (epoch !== this.epoch || !this.session) return;
      await this.call("write", { ...this.session, data, sequence: ++this.inputSequence });
    }).catch(async () => {
      if (epoch !== this.epoch) return;
      await this.close();
      this.status("Terminal input could not be confirmed. It has been closed to avoid repeating a command.", false);
    }).finally(() => { this.pendingBytes -= size; });
  }

  resize() {
    if (!this.term || !this.host.clientWidth || !this.host.clientHeight) return;
    const dimensions = this.fit.proposeDimensions();
    if (!dimensions) return;
    const cols = Math.max(20, Math.min(300, dimensions.cols));
    const rows = Math.max(2, Math.min(100, dimensions.rows));
    this.term.resize(cols, rows);
    if (this.running && this.session) {
      void this.call("resize", { ...this.session, cols, rows }).catch(() => {});
    }
  }

  async close() {
    ++this.epoch;
    window.clearTimeout(this.timer);
    window.clearTimeout(this.resizeTimer);
    this.observer?.disconnect();
    this.term?.dispose();
    this.term = null;
    this.running = false;
    this.host.replaceChildren();
    const session = this.session;
    this.session = null;
    this.status("Terminal closed", false);
    if (session) {
      try { await this.call("close", session); } catch { /* The server's idle lease also expires. */ }
    }
  }
}
