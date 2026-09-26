import { Blob } from "node:buffer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { INLINE_IMAGE_MAX_BYTES, InlineImageController, fetchInlineImage, inlineImageEndpoint, isInlineImage, validateInlineImage } from "../src/inline-images.js";

function png(width = 2, height = 3) {
  const bytes = new Uint8Array(32);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]); bytes.set([73, 72, 68, 82], 12);
  const view = new DataView(bytes.buffer); view.setUint32(16, width); view.setUint32(20, height);
  return new Blob([bytes], { type: "image/png" });
}
const record = (id = "image-1") => ({ attachment_id: id, filename: "screen.png", mime_type: "image/png", size_bytes: 32, file: png() });
const response = (blob = png(), headers = {}) => new Response(blob, { status: 200, headers });
let controller, host, root;

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
  // jsdom has no raster decoder. Browser acceptance uses real PNG decoding.
  vi.stubGlobal("Image", class { constructor() { this.naturalWidth = 2; this.naturalHeight = 3; } decode() { return Promise.resolve(); } });
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:http://localhost/${Math.random()}`);
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  host = document.createElement("div"); root = host.attachShadow({ mode: "open" }); document.body.append(host);
});
afterEach(() => { controller?.dispose(); controller = null; host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("confined raster loading", () => {
  it("uses HA endpoints with encoded, checked IDs only", () => {
    expect(inlineImageEndpoint("chat:1", "attachment", "att-1")).toBe("/api/codex_bridge/threads/chat%3A1/attachments/att-1");
    for (const id of ["../file", "https://evil/image", "x?token=secret", ""]) expect(() => inlineImageEndpoint("chat", "artifact", id)).toThrow();
    expect(() => inlineImageEndpoint("chat", "provider", "image")).toThrow();
  });
  it("rejects missing size, SVG, unbounded bytes and non-image files", () => {
    expect(isInlineImage(record())).toBe(true);
    for (const patch of [{ mime_type: "image/svg+xml" }, { mime_type: 123 }, { size_bytes: null }, { size_bytes: 0 }, { size_bytes: INLINE_IMAGE_MAX_BYTES + 1 }, { mime_type: "text/html" }]) expect(isInlineImage({ ...record(), ...patch })).toBe(false);
  });
  it("checks raster signatures, MIME and dimensions before creating a blob URL", async () => {
    expect(await validateInlineImage(png(), "image/png")).toMatchObject({ width: 2, height: 3, mime: "image/png" });
    await expect(validateInlineImage(new Blob(["<svg onload=alert(1) />"]), "image/png")).rejects.toThrow();
    await expect(validateInlineImage(png(), "image/jpeg")).rejects.toThrow();
    for (const [width, height] of [[0, 3], [9000, 1], [8192, 8192]]) await expect(validateInlineImage(png(width, height), "image/png")).rejects.toThrow();
  });
  it("recognises JPEG, GIF and WebP container dimensions", async () => {
    const jpeg = new Uint8Array([255, 216, 255, 192, 0, 8, 8, 0, 3, 0, 2, 1, 0, 0]);
    const gif = new Uint8Array([71, 73, 70, 56, 57, 97, 2, 0, 3, 0, 0, 0]);
    const webp = new Uint8Array(32); webp.set([82, 73, 70, 70], 0); webp.set([87, 69, 66, 80, 86, 80, 56, 88], 8); webp[24] = 1; webp[27] = 2;
    for (const [bytes, mime] of [[jpeg, "image/jpeg"], [gif, "image/gif"], [webp, "image/webp"]]) expect(await validateInlineImage(new Blob([bytes]), mime)).toMatchObject({ width: 2, height: 3, mime });
  });
  it("authenticates only through HA, denies redirects and bounds streamed bytes", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(response());
    const signal = new AbortController().signal;
    const blob = await fetchInlineImage(inlineImageEndpoint("chat", "attachment", "att"), { token: "private-test", signal, fetchImpl });
    expect(blob.size).toBe(32);
    expect(fetchImpl).toHaveBeenCalledWith("/api/codex_bridge/threads/chat/attachments/att", expect.objectContaining({ redirect: "error", credentials: "same-origin", signal, headers: { Authorization: "Bearer private-test", Range: `bytes=0-${INLINE_IMAGE_MAX_BYTES - 1}` } }));
    await expect(fetchInlineImage("https://evil/image", { fetchImpl })).rejects.toThrow();
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
  it("rejects oversized metadata, truncated ranges, missing stream and oversized stream", async () => {
    for (const value of [response(png(), { "Content-Length": String(INLINE_IMAGE_MAX_BYTES + 1) }), response(png(), { "Content-Range": `bytes 0-31/${INLINE_IMAGE_MAX_BYTES + 1}` }), response(new Blob([new Uint8Array(INLINE_IMAGE_MAX_BYTES + 1)])), new Response(null)]) await expect(fetchInlineImage(inlineImageEndpoint("chat", "attachment", "att"), { fetchImpl: async () => value })).rejects.toThrow();
  });
});

describe("image cards, actions and ownership", () => {
  function setup(fetchImpl = vi.fn().mockImplementation(() => Promise.resolve(response()))) {
    controller = new InlineImageController({ root, fetchImpl }); controller.setThread("chat"); return fetchImpl;
  }
  it("renders attachment/local images with accessible actions and no internal path", async () => {
    setup(); const card = controller.card("attachment", { ...record(), relative_path: "private/resumable/opaque/path" }); root.append(card);
    const state = controller.records.get("attachment:image-1"); await controller.load(state);
    expect(card.querySelector("img").alt).toBe("screen.png");
    expect(card.textContent).not.toContain("resumable"); expect(card.querySelector("img").src).toMatch(/^blob:/);
    expect(card.querySelector("button").getAttribute("aria-label")).toBe("Preview screen.png");
    const actions = card.querySelector('[aria-haspopup="menu"]'); actions.click();
    expect(root.querySelectorAll('[role="menuitem"]')).toHaveLength(3);
    expect(root.activeElement.textContent).toBe("Open image");
    root.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    expect(root.activeElement.textContent).toBe("Copy image");
    root.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(root.querySelector('[role="menu"]')).toBeNull(); expect(root.activeElement).toBe(actions);
  });
  it("right-click opens the same bounded menu and Shift F10 works", () => {
    setup(); const card = controller.card("attachment", record()); root.append(card);
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 10000, clientY: 10000 }); card.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true); expect(root.querySelector('[role="menu"]')).not.toBeNull();
    controller.closeMenu(); card.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", shiftKey: true, bubbles: true }));
    expect(root.querySelector('[role="menu"]')).not.toBeNull();
  });
  it("does not create an object URL after selection changes or disconnection", async () => {
    let resolve;
    setup(() => new Promise((done) => { resolve = done; }));
    controller.card("attachment", record()); const state = controller.records.get("attachment:image-1"); const pending = controller.load(state);
    await Promise.resolve(); controller.setThread("other-chat"); resolve(response()); await pending;
    expect(URL.createObjectURL).not.toHaveBeenCalled(); expect(controller.records.size).toBe(0);
  });
  it("rejects decoder failures and revokes the candidate URL without a broken thumbnail", async () => {
    setup(); vi.stubGlobal("Image", class { decode() { return Promise.reject(new Error("Corrupt raster")); } });
    const card = controller.card("attachment", record()); root.append(card);
    const state = controller.records.get("attachment:image-1"); await controller.load(state);
    expect(state.status).toBe("error"); expect(card.querySelector("img")).toBeNull();
    expect(card.textContent).toContain("Preview unavailable"); expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
  });
  it("limits concurrent preview requests to two", async () => {
    const resolves = []; const fetchImpl = setup(vi.fn(() => new Promise((resolve) => resolves.push(resolve))));
    const loads = [];
    for (let index = 0; index < 5; index++) { controller.card("attachment", record(`id-${index}`)); loads.push(controller.load(controller.records.get(`attachment:id-${index}`))); }
    await Promise.resolve(); expect(fetchImpl).toHaveBeenCalledTimes(2);
    resolves[0](response()); resolves[1](response());
    await vi.waitFor(() => expect(resolves.length).toBe(4)); resolves[2](response()); resolves[3](response());
    await vi.waitFor(() => expect(resolves.length).toBe(5)); resolves[4](response()); await Promise.all(loads);
  });
  it("cleans pending images and private URLs on a thread switch", async () => {
    setup(); controller.setPending([Object.assign(png(), { name: "pending.png" })]); controller.renderPending(root);
    const state = controller.records.get("local:pending-0"); await controller.load(state);
    expect(root.querySelector(".compact img")).not.toBeNull(); controller.setThread("other");
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce(); expect(controller.pending).toHaveLength(0);
  });
  it("copies real PNG bytes and reports denied clipboard without claiming success", async () => {
    setup(); controller.card("attachment", record()); const state = controller.records.get("attachment:image-1"); await controller.load(state);
    vi.stubGlobal("isSecureContext", true); const items = [];
    vi.stubGlobal("ClipboardItem", class { constructor(value) { this.value = value; items.push(value); } });
    const write = vi.fn().mockResolvedValue(); Object.defineProperty(navigator, "clipboard", { value: { write }, configurable: true });
    await controller.copy(state); expect(write).toHaveBeenCalledOnce(); expect(await items[0]["image/png"]).toBeInstanceOf(Blob); expect(state.notice).toBe("Image copied.");
    write.mockRejectedValue(new DOMException("Permission denied", "NotAllowedError")); await controller.copy(state);
    expect(state.notice).toContain("could not be copied"); expect(state.notice).not.toBe("Image copied.");
  });
  it("provides a download fallback when insecure clipboard is unavailable", async () => {
    setup(); controller.card("attachment", record()); const state = controller.records.get("attachment:image-1");
    vi.stubGlobal("isSecureContext", false); await controller.copy(state);
    expect(state.notice).toContain("Download the image instead");
  });
  it("retains no more than twelve raster blobs and releases hand-off URLs", async () => {
    setup();
    for (let index = 0; index < 14; index++) { controller.card("attachment", record(`id-${index}`)); await controller.load(controller.records.get(`attachment:id-${index}`)); }
    expect([...controller.records.values()].filter((state) => state.blob)).toHaveLength(12);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await controller.download(controller.records.get("attachment:id-13")); expect(click).toHaveBeenCalledOnce();
    controller.dispose(); expect(controller.downloadTimers.size).toBe(0); expect(controller.records.size).toBe(0);
  });
});
