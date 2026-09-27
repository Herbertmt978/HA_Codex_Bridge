/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { chatContextReference, chatContextSelection } from "../src/chat-context.js";
import "../src/codex-bridge-panel.js";

function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function item(title = "Source", revision = "a".repeat(64)) {
  return { source_thread_id: "source", message_sequence: 3, start_char: null, end_char: null, content_revision: revision, title, text: "Previous chat context (untrusted reference):\n😀\n<script>inert</script>", coverage: "retained_public_only" };
}
function panel() {
  const p = document.createElement("codex-bridge-panel"); document.body.append(p);
  p._hass = { user: { id: "alice" } }; p._loadPreferences(); p._selectedThreadId = "dest";
  p._activeThread = { thread_id: "dest", title: "Destination", status: "idle", mode: "edit", attachments: [] };
  p._threads = [{ thread_id: "source", title: "Source" }, { thread_id: "dest", title: "Destination" }];
  p._config = { capabilities: ["chat_context_v1"] }; p._status = { auth: { state: "ok", auth_required: false }, account: { available: true } };
  p._callWS = vi.fn(async () => item()); p._refreshActiveThread = vi.fn(async () => {}); p._chatContext.render();
  return p;
}
describe("previous chat context ownership", () => {
  beforeEach(() => { document.body.replaceChildren(); localStorage.clear(); vi.restoreAllMocks(); });
  it("uses explicit exclusive Unicode code-point offsets", () => {
    expect(chatContextSelection("source", "3", "1", "2")).toEqual({ source_thread_id: "source", message_sequence: 3, start_char: 1, end_char: 2 });
    expect(() => chatContextSelection("source", "", "0", "1")).toThrow();
    expect(() => chatContextSelection("source", "3", "", "1")).toThrow();
  });
  it("attaches an inspectable inert exact block without sending and removes it", async () => {
    const p = panel(), c = p._chatContext;
    c.field("source").value = "source";
    await c.attach();
    expect(p._callWS).toHaveBeenCalledExactlyOnceWith("read_chat_context", { thread_id: "dest", source_thread_id: "source" });
    const pre = p.shadowRoot.querySelector("#chat-context pre"); expect(pre.textContent).toBe(item().text); expect(pre.querySelector("script")).toBeNull();
    expect(c.snapshot()).toEqual([chatContextReference(item())]);
    [...p.shadowRoot.querySelectorAll("#chat-context button")].find((button) => button.textContent === "Remove chat context").click();
    expect(c.snapshot()).toEqual([]);
  });
  it.each(["chat", "user", "newer-request"])("ignores stale async picker response after %s", async (change) => {
    const p = panel(), c = p._chatContext, old = deferred();
    p._callWS.mockReturnValueOnce(old.promise); c.field("source").value = "source";
    const loading = c.attach();
    if (change === "chat") { p._selectedThreadId = "other"; c.render(); }
    if (change === "user") { p._hass = { user: { id: "bob" } }; c.render(); }
    if (change === "newer-request") { await c.request("read_chat_context", { source_thread_id: "source" }, (value) => c.setItems("dest", [value])); }
    old.resolve(item("Old")); await loading;
    expect(c.current().some((value) => value.title === "Old")).toBe(false);
  });
  it("does not clear new selections after acknowledgement; marks stale and refreshes deliberately", async () => {
    const p = panel(), c = p._chatContext;
    c.setItems("dest", [item()]); const revision = c.revision("dest"), refs = c.snapshot();
    c.setItems("dest", [item("New", "b".repeat(64))]); c.clearCaptured("dest", refs, revision);
    expect(c.current()[0].title).toBe("New");
    c.staleCaptured("dest", c.revision("dest")); expect(() => c.snapshot()).toThrow();
    await c.refresh(0); expect(c.snapshot()).toEqual(refs);
  });
  it("snapshots send and retry references and clears only the sent selection", async () => {
    const p = panel(), c = p._chatContext, pending = deferred();
    c.setItems("dest", [item()]); p.shadowRoot.getElementById("prompt-input").value = "Prompt";
    p._callWS.mockReturnValueOnce(pending.promise);
    const sending = p._sendPrompt();
    expect(p._callWS.mock.calls[0][1].chat_context).toEqual([chatContextReference(item())]);
    c.setItems("dest", [item("New", "b".repeat(64))]); pending.resolve({}); await sending;
    expect(c.current()[0].title).toBe("New");
  });
  it("acknowledgement after HA user change cannot clear that user's context", async () => {
    const p = panel(), c = p._chatContext, pending = deferred();
    c.setItems("dest", [item()]); p.shadowRoot.getElementById("prompt-input").value = "Prompt";
    p._callWS.mockReturnValueOnce(pending.promise); const sending = p._sendPrompt();
    p._hass = { user: { id: "bob" } }; p._loadPreferences(); c.render(); c.setItems("dest", [item("Bob")]);
    pending.resolve({}); await sending; expect(c.current()[0].title).toBe("Bob");
  });
  it("retains exact original references on response-loss retry", async () => {
    const p = panel(), c = p._chatContext;
    c.setItems("dest", [item()]); p.shadowRoot.getElementById("prompt-input").value = "Prompt";
    p._callWS.mockRejectedValueOnce(new Error("lost")); await p._sendPrompt();
    expect(p._promptMutation.state).toBe("retryable");
    c.setItems("dest", [item("Later", "b".repeat(64))]); p._callWS.mockResolvedValueOnce({}); await p._sendPrompt();
    expect(p._callWS.mock.calls[1][1].chat_context).toEqual(p._callWS.mock.calls[0][1].chat_context);
    expect(c.current()[0].title).toBe("Later");
  });
});
