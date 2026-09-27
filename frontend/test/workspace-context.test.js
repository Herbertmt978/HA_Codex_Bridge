/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { contextSelection } from "../src/workspace-context.js";
import "../src/codex-bridge-panel.js";

function panelFixture() {
  const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", capabilities: ["workspace_context_v1", "web_search_v1"] };
  panel._selectedThreadId = "alpha";
  panel._activeThread = { thread_id: "alpha", title: "Context", status: "idle", mode: "edit", attachments: [] };
  panel._render = vi.fn(() => panel._workspaceContext.render());
  panel._refreshActiveThread = vi.fn(async () => {});
  panel._workspaceContext.render();
  return panel;
}
const ready = { status: "ready", path: "file.txt", start_line: 1, end_line: 300, total_lines: 300, text: "<script>untrusted()</script>\r\n", content_revision: "a".repeat(64) };
function field(panel, name, value) { panel.shadowRoot.querySelector(`[data-context-field="${name}"]`).value = value; }
function attachment(text = ready.text) { return { path: "file.txt", start_line: null, end_line: null, text, content_revision: ready.content_revision, stale: false }; }
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

describe("workspace context", () => {
  it("requires inclusive complete safe integer ranges and preserves whole-file intent", () => {
    expect(contextSelection("file.txt", "", "")).toEqual({ path: "file.txt", start_line: null, end_line: null });
    expect(contextSelection("file.txt", "2", "201").end_line).toBe(201);
    for (const [first, last] of [["2", ""], ["0", "1"], ["3", "2"], ["1", "201"], ["1.1", "2"], ["1", "9007199254740992"]]) expect(() => contextSelection("file.txt", first, last)).toThrow();
  });
  it("attaches inspectable inert text and retains null endpoints for 300-line whole files", async () => {
    const panel = panelFixture(); panel._callWS = vi.fn(async () => ready);
    field(panel, "path", "file.txt"); await panel._workspaceContext.attach();
    const root = panel.shadowRoot.getElementById("workspace-context");
    expect(root.querySelector("pre").textContent).toBe(ready.text);
    expect(root.querySelector("script")).toBeNull();
    expect(root.querySelector("[data-context-items] summary").textContent).toContain("Whole file");
    expect(root.textContent).not.toContain(ready.content_revision);
    expect(panel._workspaceContext.snapshot()[0].start_line).toBeNull();
    [...root.querySelectorAll("button")].find((el) => el.textContent === "Remove context").click();
    expect(panel._workspaceContext.current()).toEqual([]);
  });
  it("labels exact selected lines as excerpts and sends no client-supplied text", async () => {
    const panel = panelFixture(); panel._callWS = vi.fn(async () => ({ ...ready, start_line: 2, end_line: 3, text: "two\nthree\n" }));
    field(panel, "path", "file.txt"); field(panel, "start", "2"); field(panel, "end", "3");
    await panel._workspaceContext.attach();
    expect(panel.shadowRoot.querySelector("[data-context-items] summary").textContent).toContain("Lines 2–3 (excerpt)");
    expect(panel._workspaceContext.snapshot()[0]).toEqual({ path: "file.txt", start_line: 2, end_line: 3, content_revision: ready.content_revision });
  });
  it("enforces count and UTF-8 aggregate limits without replacing existing context", async () => {
    const panel = panelFixture(); panel._callWS = vi.fn(async () => ready); field(panel, "path", "file.txt");
    panel._workspaceContext.items.set("alpha", Array.from({ length: 8 }, () => attachment()));
    await panel._workspaceContext.attach(); expect(panel._callWS).not.toHaveBeenCalled();
    panel._workspaceContext.items.set("alpha", [attachment("é".repeat(48 * 1024))]);
    await panel._workspaceContext.attach(); expect(panel._workspaceContext.current()).toHaveLength(1);
    expect(panel.shadowRoot.querySelector("[data-context-status]").textContent).toContain("size limit");
  });
  it("discards a late preview when the selected chat changes", async () => {
    const panel = panelFixture(); let resolve;
    panel._callWS = vi.fn(() => new Promise((done) => { resolve = done; })); field(panel, "path", "file.txt");
    const pending = panel._workspaceContext.attach();
    panel._selectedThreadId = "beta"; panel._workspaceContext.render(); resolve(ready); await pending;
    expect(panel._workspaceContext.items.size).toBe(0);
    expect(panel._workspaceContext.busy).toBe(false);
  });
  it("clears every context selection and ignores late reads on HA-user change", async () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    let resolve;
    panel._callWS = vi.fn(() => new Promise((done) => { resolve = done; })); field(panel, "path", "file.txt");
    const pending = panel._workspaceContext.attach();
    panel._hass = { user: { id: "different-user" } }; panel._loadPreferences(); panel._workspaceContext.render();
    resolve(ready); await pending;
    expect(panel._workspaceContext.items.size).toBe(0);
  });
  it("waits for the reviewed preview before allowing a prompt to send", async () => {
    const panel = panelFixture(); let resolve;
    panel._callWS = vi.fn(() => new Promise((done) => { resolve = done; })); field(panel, "path", "file.txt");
    const pending = panel._workspaceContext.attach(); panel.shadowRoot.getElementById("prompt-input").value = "Read this";
    await panel._sendPrompt(); expect(panel._callWS).toHaveBeenCalledTimes(1);
    expect(panel._promptMutationForThread("alpha")).toBeNull(); resolve(ready); await pending;
    expect(panel._workspaceContext.current()).toHaveLength(1);
  });
  it("preserves exact immutable refs/settings on uncertain retries and clears only after success", async () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    panel.shadowRoot.getElementById("prompt-input").value = "Read this";
    panel._callWS = vi.fn().mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce({});
    await panel._sendPrompt();
    const first = structuredClone(panel._callWS.mock.calls[0][1]);
    panel._workspaceContext.current()[0].content_revision = "b".repeat(64);
    await panel._sendPrompt();
    expect(panel._callWS.mock.calls[1][1]).toEqual(first);
    expect(first.workspace_context[0]).not.toHaveProperty("text");
    expect(panel._workspaceContext.current()[0].content_revision).toBe("b".repeat(64));
  });
  it("settles only the captured selection and preserves a newer context item", async () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    panel.shadowRoot.getElementById("prompt-input").value = "Read this"; let resolve;
    panel._callWS = vi.fn(() => new Promise((done) => { resolve = done; }));
    const pending = panel._sendPrompt();
    panel._workspaceContext.items.set("alpha", [attachment(), { ...attachment("new context"), path: "new.txt" }]);
    resolve({}); await pending;
    expect(panel._workspaceContext.current()).toHaveLength(2);
  });
  it("clears unchanged captured context on event settlement without disturbing another chat", () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    panel._workspaceContext.items.set("beta", [attachment("other chat")]);
    panel._workspaceContext.clearCaptured("alpha", panel._workspaceContext.snapshot(), panel._workspaceContext.revision("alpha"));
    expect(panel._workspaceContext.current()).toEqual([]);
    expect(panel._workspaceContext.items.get("beta")[0].text).toBe("other chat");
  });
  it("preserves remove-and-readd identical references after an older acknowledgement", async () => {
    const panel = panelFixture(); panel._workspaceContext.setItems("alpha", [attachment()]);
    panel.shadowRoot.getElementById("prompt-input").value = "Read this"; let resolve;
    panel._callWS = vi.fn(() => new Promise((done) => { resolve = done; }));
    const pending = panel._sendPrompt();
    panel._workspaceContext.setItems("alpha", []);
    panel._workspaceContext.setItems("alpha", [attachment()]);
    resolve({}); await pending;
    expect(panel._workspaceContext.current()).toHaveLength(1);
  });
  it("treats definite stale rejection as actionable and keeps prompt/excerpt until explicit refresh", async () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    panel.shadowRoot.getElementById("prompt-input").value = "Keep my draft";
    panel._bridgeErrorCode = () => "stale_context";
    panel._callWS = vi.fn(async () => { throw new Error("stale"); });
    await panel._sendPrompt();
    expect(panel._promptMutationForThread("alpha")).toBeNull();
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("Keep my draft");
    expect(panel._workspaceContext.current()[0].text).toBe(ready.text);
    expect(() => panel._workspaceContext.snapshot()).toThrow(/Refresh/);
    panel._callWS = vi.fn(async () => ({ ...ready, text: "explicitly refreshed\n", content_revision: "b".repeat(64) }));
    await panel._workspaceContext.refresh(0);
    expect(panel._workspaceContext.current()[0].text).toBe("explicitly refreshed\n");
    expect(panel._workspaceContext.snapshot()[0].content_revision).toBe("b".repeat(64));
  });
  it("hides unavailable/Assist controls and omits unsupported context", async () => {
    const panel = panelFixture(); panel._workspaceContext.items.set("alpha", [attachment()]);
    panel._config.capabilities = []; panel._workspaceContext.render();
    expect(panel.shadowRoot.getElementById("workspace-context").hidden).toBe(true);
    panel.shadowRoot.getElementById("prompt-input").value = "No context support"; panel._callWS = vi.fn(async () => ({}));
    await panel._sendPrompt(); expect(panel._callWS.mock.calls[0][1]).not.toHaveProperty("workspace_context");
  });
});
