import { sanitizeFilename } from "./safe-dom.js";

export const INLINE_IMAGE_MAX_BYTES = 8 * 1024 * 1024;
const MAX_PIXELS = 32 * 1024 * 1024;
const MAX_EDGE = 8192;
const MAX_CACHED_IMAGES = 12;
const MAX_CACHE_BYTES = 32 * 1024 * 1024;
const MAX_CACHE_PIXELS = 32 * 1024 * 1024;
const MIME = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const identifier = (value) => typeof value === "string" && /^[A-Za-z0-9_.:-]{1,200}$/.test(value);
const imageFilename = (value, fallback = "image") => sanitizeFilename(typeof value === "string" ? value.split(/[\\/]/).at(-1) : value, fallback);

export function isInlineImage(record) {
  return typeof record?.mime_type === "string" && MIME.has(record.mime_type.toLowerCase()) && Number.isSafeInteger(record?.size_bytes)
    && record.size_bytes > 0 && record.size_bytes <= INLINE_IMAGE_MAX_BYTES;
}

export function inlineImageEndpoint(threadId, kind, id) {
  if (!identifier(threadId) || !identifier(id) || !["artifact", "attachment"].includes(kind)) throw new Error("Invalid image reference");
  return `/api/codex_bridge/threads/${encodeURIComponent(threadId)}/${kind}s/${encodeURIComponent(id)}`;
}

// Check the container signature and dimensions before handing untrusted bytes to
// a browser decoder. SVG/HTML and oversized raster dimensions never get a URL.
export async function validateInlineImage(blob, expectedMime) {
  if (!MIME.has(expectedMime) || !blob || blob.size < 10 || blob.size > INLINE_IMAGE_MAX_BYTES) throw new Error("Image preview is not available");
  const bytes = new Uint8Array(await blob.slice(0, 65536).arrayBuffer());
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const ascii = (offset, length) => String.fromCharCode(...bytes.subarray(offset, offset + length));
  let mime, width, height;
  if (bytes.length >= 24 && bytes[0] === 137 && ascii(1, 7) === "PNG\r\n\x1a\n" && ascii(12, 4) === "IHDR") {
    mime = "image/png"; width = view.getUint32(16); height = view.getUint32(20);
  } else if (ascii(0, 6) === "GIF87a" || ascii(0, 6) === "GIF89a") {
    mime = "image/gif"; width = view.getUint16(6, true); height = view.getUint16(8, true);
  } else if (bytes.length >= 30 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "WEBP") {
    mime = "image/webp";
    if (ascii(12, 4) === "VP8X") {
      width = 1 + bytes[24] + (bytes[25] << 8) + (bytes[26] << 16);
      height = 1 + bytes[27] + (bytes[28] << 8) + (bytes[29] << 16);
    } else if (ascii(12, 4) === "VP8L" && bytes[20] === 47) {
      width = 1 + ((bytes[21] | bytes[22] << 8) & 0x3fff);
      height = 1 + ((bytes[22] >> 6 | bytes[23] << 2 | bytes[24] << 10) & 0x3fff);
    } else if (ascii(12, 4) === "VP8 " && bytes[23] === 157 && bytes[24] === 1 && bytes[25] === 42) {
      width = view.getUint16(26, true) & 0x3fff; height = view.getUint16(28, true) & 0x3fff;
    }
  } else if (bytes[0] === 255 && bytes[1] === 216) {
    mime = "image/jpeg";
    let position = 2;
    while (position + 8 < bytes.length) {
      if (bytes[position] !== 255) break;
      while (bytes[position] === 255) position += 1;
      const marker = bytes[position++];
      if (marker === 217 || marker === 218 || position + 2 > bytes.length) break;
      if (marker === 216 || marker === 1 || (marker >= 208 && marker <= 215)) continue;
      const length = view.getUint16(position);
      if (length < 2 || position + length > bytes.length) break;
      if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker) && length >= 8) {
        height = view.getUint16(position + 3); width = view.getUint16(position + 5); break;
      }
      position += length;
    }
  }
  if (mime !== expectedMime || !width || !height || width > MAX_EDGE || height > MAX_EDGE || width * height > MAX_PIXELS) throw new Error("Image preview is not available");
  return { blob: new Blob([blob], { type: mime }), width, height, mime };
}

export async function fetchInlineImage(url, { token = "", signal, fetchImpl = fetch } = {}) {
  if (!/^\/api\/codex_bridge\/threads\/[A-Za-z0-9_.:%-]+\/(?:artifacts|attachments)\/[A-Za-z0-9_.:%-]+$/.test(url)) throw new Error("Invalid image endpoint");
  const response = await fetchImpl(url, { headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), Range: `bytes=0-${INLINE_IMAGE_MAX_BYTES - 1}` }, signal, redirect: "error", credentials: "same-origin" });
  if (!response.ok) throw new Error("Image could not be loaded through Home Assistant");
  const length = response.headers.get("Content-Length");
  if (length && (!/^\d+$/.test(length) || Number(length) > INLINE_IMAGE_MAX_BYTES)) {
    await response.body?.cancel(); throw new Error("Image is too large to preview");
  }
  // A truncated Range must not turn a larger file into an apparently bounded image.
  const range = response.headers.get("Content-Range");
  if (range && (!/^bytes 0-\d+\/\d+$/.test(range) || Number(range.split("/")[1]) > INLINE_IMAGE_MAX_BYTES)) {
    await response.body?.cancel(); throw new Error("Image is too large to preview");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Image response is not available");
  const chunks = []; let size = 0;
  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > INLINE_IMAGE_MAX_BYTES) throw new Error("Image is too large to preview");
      chunks.push(value);
    }
  } catch (error) { await reader.cancel(); throw error; }
  finally { reader.releaseLock(); }
  return new Blob(chunks);
}

function element(tag, className, text = "") {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}
function button(label, click) {
  const node = element("button", "inline-image-action", label); node.type = "button"; node.setAttribute("aria-label", label); node.addEventListener("click", click); return node;
}

/** Owns private blob URLs only for the selected conversation. Never embeds a provider URL. */
export class InlineImageController {
  constructor({ root, token = () => "", fetchImpl = fetch } = {}) {
    this.root = root; this.token = token; this.fetchImpl = fetchImpl;
    this.threadId = ""; this.records = new Map(); this.targets = new Map(); this.pending = []; this.pendingRevision = 0;
    this.downloadTimers = new Map(); this.generation = 0; this.activeLoads = 0; this.loadQueue = [];
    this.observer = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) { this.observer.unobserve(entry.target); void this.load(this.targets.get(entry.target)); }
    }, { root: null, rootMargin: "160px" }) : null;
    this.outside = (event) => { if (this.menu && !event.composedPath().includes(this.menu)) this.closeMenu(); };
    root?.addEventListener("pointerdown", this.outside);
    this.resize = () => { this.placeMenu(); this.placeModal(); };
    window.addEventListener("resize", this.resize);
    window.visualViewport?.addEventListener("resize", this.resize);
    window.visualViewport?.addEventListener("scroll", this.resize);
  }

  setThread(threadId) {
    if (threadId === this.threadId) return;
    this.clear(); this.threadId = threadId || "";
  }

  card(kind, record, { compact = false } = {}) {
    const id = kind === "attachment" ? record?.attachment_id : kind === "local" ? record?.id : record?.artifact_id;
    if (!isInlineImage(record) || !identifier(id) || !this.threadId) return null;
    const key = `${kind}:${id}`;
    let state = this.records.get(key);
    if (!state) {
      state = { key, kind, record, status: "idle", nodes: new Set(), controller: new AbortController() };
      this.records.set(key, state);
    }
    const card = element("figure", `inline-image-card${compact ? " compact" : ""}`);
    card.dataset.inlineImageKey = key;
    const open = button(`Preview ${imageFilename(record.filename)}`, () => this.open(state, open));
    open.className = "inline-image-thumbnail";
    const placeholder = element("span", "inline-image-placeholder", "Image preview");
    open.append(placeholder);
    const caption = element("figcaption", "inline-image-caption", imageFilename(record.filename));
    const actions = element("div", "inline-image-actions");
    const options = button("Image actions", () => this.openMenu(state, options));
    options.textContent = "⋯";
    options.setAttribute("aria-haspopup", "menu"); options.setAttribute("aria-label", `Actions for ${caption.textContent}`);
    actions.append(options);
    const status = element("span", "inline-image-status"); status.setAttribute("role", "status");
    card.append(open, caption, actions, status);
    const node = { card, open, placeholder, status };
    state.nodes.add(node); this.targets.set(card, state);
    card.addEventListener("contextmenu", (event) => { event.preventDefault(); event.stopPropagation(); this.openMenu(state, open, { x: event.clientX, y: event.clientY }); });
    card.addEventListener("keydown", (event) => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); event.stopPropagation(); this.openMenu(state, open); } });
    this.paint(state);
    if (this.observer) this.observer.observe(card); else queueMicrotask(() => { if (card.isConnected) void this.load(state); });
    this.pruneTargets();
    return card;
  }

  pruneTargets() {
    clearTimeout(this.pruneTimer);
    this.pruneTimer = setTimeout(() => {
      for (const [target, state] of this.targets) if (!target.isConnected) {
        this.observer?.unobserve(target); this.targets.delete(target);
        for (const node of state.nodes) if (node.card === target) state.nodes.delete(node);
      }
      for (const state of this.records.values()) for (const node of state.nodes) if (!node.card.isConnected) state.nodes.delete(node);
    }, 0);
  }

  bindExisting(kind, record, thumbnail, container) {
    const card = this.card(kind, record);
    if (!card) return;
    const state = this.records.get(card.dataset.inlineImageKey);
    const status = card.querySelector(".inline-image-status");
    container.append(card.querySelector(".inline-image-actions"), status);
    state.nodes.add({ card: container, open: thumbnail, status });
    thumbnail.addEventListener("contextmenu", (event) => { event.preventDefault(); event.stopPropagation(); this.openMenu(state, thumbnail, { x: event.clientX, y: event.clientY }); });
    thumbnail.addEventListener("keydown", (event) => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); event.stopPropagation(); this.openMenu(state, thumbnail); } });
  }

  paint(state) {
    for (const node of state.nodes) {
      if (state.url) {
        let image = node.open.querySelector("img");
        if (!image) { image = element("img", "inline-image-raster"); image.alt = imageFilename(state.record.filename, "Image"); image.draggable = false; node.open.replaceChildren(image); }
        image.src = state.url; image.width = state.width; image.height = state.height;
      }
      node.status.textContent = state.notice || (state.status === "loading" ? "Loading image…" : state.status === "error" ? "Preview unavailable. Use Image actions to retry." : "");
      node.open.setAttribute("aria-busy", String(state.status === "loading"));
    }
  }

  load(state) {
    if (!state || this.records.get(state.key) !== state) return Promise.resolve(null);
    if (state.blob) { this.records.delete(state.key); this.records.set(state.key, state); return Promise.resolve(state); }
    if (state.promise) return state.promise;
    const generation = this.generation; const threadId = this.threadId;
    state.status = "loading"; state.notice = ""; this.paint(state);
    state.promise = (async () => {
      await new Promise((resolve) => {
        if (this.activeLoads < 2) { this.activeLoads += 1; resolve(); }
        else this.loadQueue.push(resolve);
      });
      try {
        if (generation !== this.generation || state.controller.signal.aborted) return null;
        const blob = state.kind === "local" ? state.record.file : await fetchInlineImage(inlineImageEndpoint(threadId, state.kind, state.kind === "attachment" ? state.record.attachment_id : state.record.artifact_id), { token: this.token(), signal: state.controller.signal, fetchImpl: this.fetchImpl });
        const validated = await validateInlineImage(blob, state.record.mime_type.toLowerCase());
        if (generation !== this.generation || state.controller.signal.aborted || this.records.get(state.key) !== state) return null;
        const url = URL.createObjectURL(validated.blob); state.candidateUrl = url;
        try {
          const image = new Image(); image.src = url;
          await image.decode();
          if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth > MAX_EDGE || image.naturalHeight > MAX_EDGE || image.naturalWidth * image.naturalHeight > MAX_PIXELS) throw new Error("Image preview is not available");
          if (generation !== this.generation || state.controller.signal.aborted || this.records.get(state.key) !== state) { URL.revokeObjectURL(url); return null; }
        } catch (error) { URL.revokeObjectURL(url); throw error; }
        finally { state.candidateUrl = null; }
        Object.assign(state, validated, { url, status: "ready" });
        this.trim(state); this.paint(state); return state;
      } catch (error) {
        if (generation === this.generation && !state.controller.signal.aborted) { state.status = "error"; this.paint(state); }
        if (error?.name === "AbortError") return null;
        return null;
      } finally {
        state.promise = null;
        const next = this.loadQueue.shift(); if (next) next(); else this.activeLoads -= 1;
      }
    })();
    return state.promise;
  }

  trim(current) {
    let cached = [...this.records.values()].filter((state) => state.blob);
    let bytes = cached.reduce((total, state) => total + state.blob.size, 0);
    let pixels = cached.reduce((total, state) => total + state.width * state.height, 0);
    for (const state of cached) {
      if (cached.length <= MAX_CACHED_IMAGES && bytes <= MAX_CACHE_BYTES && pixels <= MAX_CACHE_PIXELS) break;
      if (state === current || this.modalState === state) continue;
      bytes -= state.blob.size; pixels -= state.width * state.height; cached = cached.filter((item) => item !== state);
      URL.revokeObjectURL(state.url); state.url = null; state.blob = null; state.status = "idle";
      for (const node of state.nodes) node.open.replaceChildren(element("span", "inline-image-placeholder", "Open image to reload preview"));
    }
  }

  setPending(files) {
    this.clearPending();
    this.pending = files.slice(0, 6).map((file, index) => ({ id: `pending-${index}`, filename: file.name, mime_type: file.type, size_bytes: file.size, file })).filter(isInlineImage);
  }
  clearPending() {
    for (const [key, state] of this.records) if (state.kind === "local") { state.controller.abort(); if (state.url) URL.revokeObjectURL(state.url); if (state.candidateUrl) URL.revokeObjectURL(state.candidateUrl); this.records.delete(key); }
    this.pending = []; this.pendingRevision += 1;
  }
  renderPending(container) {
    for (const record of this.pending) {
      const card = this.card("local", record, { compact: true });
      if (card) { container.append(card); void this.load(this.records.get(card.dataset.inlineImageKey)); }
    }
  }

  openMenu(state, trigger, position) {
    this.closeMenu();
    const menu = element("div", "inline-image-menu"); menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", "Image actions");
    const items = [button("Open image", () => { this.closeMenu(); void this.open(state, trigger); }), button("Copy image", () => { this.closeMenu(); void this.copy(state); }), button("Download image", () => { this.closeMenu(); void this.download(state); })];
    for (const item of items) { item.setAttribute("role", "menuitem"); menu.append(item); }
    (this.modal || this.root).append(menu); this.menu = menu; this.menuTrigger = trigger;
    this.menuPosition = position;
    this.placeMenu();
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape" || event.key === "Tab") { if (event.key === "Escape") event.preventDefault(); event.stopPropagation(); this.closeMenu(); }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation(); const index = items.indexOf(this.root.activeElement);
        items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
      }
    });
    items[0].focus();
  }
  closeMenu() { if (!this.menu) return; this.menu.remove(); this.menu = null; this.menuTrigger?.focus(); this.menuTrigger = null; }

  viewport() {
    const viewport = window.visualViewport;
    return { left: viewport?.offsetLeft || 0, top: viewport?.offsetTop || 0, width: viewport?.width || window.innerWidth, height: viewport?.height || window.innerHeight };
  }
  placeMenu() {
    if (!this.menu || !this.menuTrigger) return;
    const viewport = this.viewport(); const bounds = this.menuTrigger.getBoundingClientRect();
    this.menu.style.maxWidth = `${Math.max(40, viewport.width - 16)}px`;
    this.menu.style.maxHeight = `${Math.max(40, viewport.height - 16)}px`; this.menu.style.overflow = "auto";
    const rect = this.menu.getBoundingClientRect();
    this.menu.style.left = `${Math.max(viewport.left + 8, Math.min(this.menuPosition?.x ?? bounds.left, viewport.left + viewport.width - rect.width - 8))}px`;
    this.menu.style.top = `${Math.max(viewport.top + 8, Math.min(this.menuPosition?.y ?? bounds.bottom, viewport.top + viewport.height - rect.height - 8))}px`;
  }
  placeModal() {
    if (!this.modal) return;
    const viewport = this.viewport();
    this.modal.style.inset = "auto"; this.modal.style.margin = "0";
    this.modal.style.width = `${Math.max(40, Math.min(1000, viewport.width - 32))}px`;
    this.modal.style.maxHeight = `${Math.max(40, viewport.height - 32)}px`;
    const controls = this.modal.querySelector(".inline-image-dialog-controls");
    const image = this.modal.querySelector(".inline-image-full");
    image.style.maxHeight = `${Math.max(40, viewport.height - controls.scrollHeight - 96)}px`;
    const rect = this.modal.getBoundingClientRect();
    this.modal.style.left = `${viewport.left + Math.max(16, (viewport.width - rect.width) / 2)}px`;
    this.modal.style.top = `${viewport.top + Math.max(16, (viewport.height - rect.height) / 2)}px`;
  }

  async open(state, trigger) {
    const generation = this.generation; const loaded = await this.load(state);
    if (!loaded || generation !== this.generation) return;
    this.closeModal();
    const modal = element("dialog", "inline-image-dialog"); modal.setAttribute("aria-label", `Image preview: ${imageFilename(state.record.filename)}`);
    const controls = element("div", "inline-image-dialog-controls");
    const close = button("Close", () => this.closeModal());
    controls.append(element("strong", "inline-image-dialog-name", imageFilename(state.record.filename)), button("Copy image", () => this.copy(state)), button("Download", () => this.download(state)), close);
    const image = element("img", "inline-image-full"); image.src = loaded.url; image.alt = imageFilename(state.record.filename);
    image.addEventListener("load", () => this.placeModal());
    const status = element("p", "inline-image-status"); status.setAttribute("role", "status");
    modal.append(controls, image, status); this.root.append(modal);
    this.modal = modal; this.modalState = state; this.modalTrigger = trigger;
    modal.addEventListener("cancel", (event) => { event.preventDefault(); this.closeModal(); });
    modal.addEventListener("contextmenu", (event) => { if (event.target === image) { event.preventDefault(); this.openMenu(state, close, { x: event.clientX, y: event.clientY }); } });
    modal.showModal(); this.placeModal(); close.focus();
  }
  closeModal() { if (!this.modal) return; this.closeMenu(); this.modal.close(); this.modal.remove(); this.modal = null; this.modalState = null; this.modalTrigger?.focus(); this.modalTrigger = null; }
  notice(state, text) { state.notice = text; this.paint(state); if (this.modalState === state) { this.modal.querySelector(".inline-image-status").textContent = text; this.placeModal(); } }

  async copy(state) {
    const generation = this.generation;
    if (!globalThis.isSecureContext || !navigator.clipboard?.write || typeof ClipboardItem !== "function") { this.notice(state, "Copy image needs a secure browser connection. Download the image instead."); return; }
    try {
      // Start write in the user's activation; Safari can await the promised PNG.
      const png = (async () => {
        const loaded = await this.load(state);
        if (!loaded || generation !== this.generation) throw new Error("Image changed");
        if (loaded.mime === "image/png") return loaded.blob;
        const image = new Image(); image.src = loaded.url; await image.decode();
        if (generation !== this.generation || image.naturalWidth > MAX_EDGE || image.naturalHeight > MAX_EDGE || image.naturalWidth * image.naturalHeight > MAX_PIXELS) throw new Error("Image changed");
        const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const context = canvas.getContext("2d"); if (!context) throw new Error("Copy unavailable"); context.drawImage(image, 0, 0);
        return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Copy unavailable")), "image/png"));
      })();
      // Observe rejection even if a browser rejects write without reading the item.
      void png.catch(() => {});
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      if (generation === this.generation) this.notice(state, "Image copied.");
    } catch { if (generation === this.generation) this.notice(state, "Image could not be copied. Check clipboard permission or download it instead."); }
  }

  async download(state) {
    const generation = this.generation; const loaded = await this.load(state);
    if (!loaded || generation !== this.generation) return;
    // Separate hand-off URL survives cache eviction while the browser saves it.
    const url = URL.createObjectURL(loaded.blob); const anchor = element("a", ""); anchor.href = url; anchor.download = imageFilename(state.record.filename);
    this.root.append(anchor); anchor.click(); anchor.remove();
    this.downloadTimers.set(url, setTimeout(() => { URL.revokeObjectURL(url); this.downloadTimers.delete(url); }, 60000));
  }

  clear() {
    this.generation += 1; this.closeModal(); this.closeMenu(); this.observer?.disconnect(); clearTimeout(this.pruneTimer);
    for (const state of this.records.values()) { state.controller.abort(); if (state.url) URL.revokeObjectURL(state.url); if (state.candidateUrl) URL.revokeObjectURL(state.candidateUrl); }
    for (const [url, timer] of this.downloadTimers) { clearTimeout(timer); URL.revokeObjectURL(url); }
    this.downloadTimers.clear(); this.records.clear(); this.targets.clear(); this.pending = []; this.pendingRevision += 1;
  }
  dispose() {
    this.clear(); this.root?.removeEventListener("pointerdown", this.outside);
    window.removeEventListener("resize", this.resize);
    window.visualViewport?.removeEventListener("resize", this.resize);
    window.visualViewport?.removeEventListener("scroll", this.resize);
  }
}

export const inlineImageCss = `
  .inline-image-card { position: relative; margin: 0; width: min(380px, 100%); display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
  .uploaded-image-message > .inline-image-card { justify-self: end; }
  .inline-image-thumbnail { grid-column: 1 / -1; display: block; width: 100%; height: 240px; padding: 0; overflow: hidden; border: 1px solid var(--border-color); border-radius: 14px; background: var(--surface-muted); color: var(--muted-color); cursor: zoom-in; }
  .inline-image-thumbnail:focus-visible, .inline-image-action:focus-visible { outline: 2px solid var(--accent-color); outline-offset: 3px; }
  .inline-image-raster { display: block; width: 100%; height: 100%; object-fit: contain; }
  .inline-image-caption { align-self: center; color: var(--muted-color); font-size: var(--font-caption-size); overflow-wrap: anywhere; }
  .inline-image-actions { display: flex; gap: 8px; justify-content: end; }
  .inline-image-actions .inline-image-action { min-width: 40px; font-size: 20px; padding: 3px 8px; }
  .inline-image-action { min-height: 36px; border: 0; border-radius: 8px; background: var(--surface-muted); color: var(--text-color); padding: 7px 12px; font: inherit; cursor: pointer; }
  .inline-image-action:hover { background: var(--hover-bg, var(--surface-muted)); }
  .inline-image-status { grid-column: 1 / -1; color: var(--muted-color); font-size: var(--font-caption-size); overflow-wrap: anywhere; }
  .inline-image-status:empty { display: none; }
  .inline-image-card.compact { width: 112px; gap: 4px; }
  .inline-image-card.compact .inline-image-thumbnail { height: 64px; }
  .inline-image-card.compact .inline-image-caption { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .inline-image-card.compact .inline-image-status { max-height: 32px; overflow: auto; }
  .inline-image-card.compact .inline-image-actions { position: absolute; top: 4px; right: 4px; }
  .inline-image-card.compact .inline-image-action { font-size: 20px; min-width: 40px; min-height: 40px; padding: 4px 6px; }
  .composer-shell:has(.attachment-chips .inline-image-card) { padding-top: 152px; }
  .composer-shell:has(.attachment-chips .inline-image-card) .attachment-chips { top: 12px; bottom: auto; max-height: 132px; overflow: auto; align-items: start; flex-wrap: nowrap; }
  .composer-shell .attachment-chips .inline-image-card { flex: 0 0 112px; }
  .inline-image-menu { position: fixed; z-index: 10010; display: grid; gap: 3px; min-width: 176px; max-width: calc(100vw - 16px); padding: 6px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--surface-bg); box-shadow: 0 8px 28px #0002; }
  .inline-image-menu .inline-image-action { text-align: left; background: transparent; }
  .inline-image-menu .inline-image-action:hover, .inline-image-menu .inline-image-action:focus-visible { background: var(--surface-muted); }
  .inline-image-dialog { position: fixed; inset: max(16px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(16px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); width: min(1000px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 16px; border: 1px solid var(--border-color); border-radius: 16px; color: var(--text-color); background: var(--surface-bg); }
  .inline-image-dialog::backdrop { background: #0009; }
  .inline-image-dialog-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
  .inline-image-dialog-name { margin-right: auto; overflow-wrap: anywhere; min-width: 0; }
  .inline-image-full { display: block; max-width: 100%; max-height: calc(100dvh - 160px); width: auto; height: auto; margin: auto; object-fit: contain; }
  @media (max-width: 600px) { .inline-image-card { width: min(300px, 100%); } .inline-image-thumbnail { height: 200px; } .inline-image-action { min-height: 44px; } .inline-image-dialog { padding: 10px; } .inline-image-full { max-height: calc(100dvh - 210px); } }
`;
