/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

const rows = [
  { kind: "interaction", thread_id: "waiting", project_id: "one", project_name: "One", chat_title: "Question", label: "Response needed" },
  { kind: "failure", thread_id: "failed", project_id: "two", project_name: "Two", chat_title: "Failure", label: "Run failed" },
  { kind: "review", thread_id: "archived", project_id: "two", project_name: "Two", chat_title: "Result", label: "Completed turn ready to review", archived: true },
];
function setup() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { capabilities: ["attention_inbox_v1"] };
  panel._hass = { user: { id: "alice" }, connection: {} };
  panel._callWS = vi.fn(async () => ({ items: rows, truncated: false }));
  return panel;
}
function select(panel, id, value) {
  const control = panel.shadowRoot.getElementById(id);
  control.value = value;
  control.dispatchEvent(new Event("change", { bubbles: true }));
}
const list = (panel) => panel.shadowRoot.getElementById("attention-inbox-items");
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

describe("cross-project operational attention", () => {
  it("filters by status and project without clearing authoritative rows or losing filter focus", async () => {
    const panel = setup();
    await panel._loadAttentionInbox();
    expect(list(panel).querySelectorAll("button")).toHaveLength(3);
    const control = panel.shadowRoot.getElementById("attention-kind");
    control.focus();
    select(panel, "attention-kind", "interaction");
    expect(panel.shadowRoot.activeElement).toBe(control);
    expect(list(panel).textContent).toContain("Question");
    expect(list(panel).textContent).not.toContain("Failure");
    select(panel, "attention-project", "two");
    expect(list(panel).textContent).toContain("No attention items match");
    await panel._loadAttentionInbox(true);
    expect(control.value).toBe("interaction");
    expect(panel._attentionData.items).toHaveLength(3);
    select(panel, "attention-kind", "all");
    expect(list(panel).querySelectorAll("button")).toHaveLength(2);
    expect(panel.shadowRoot.querySelector('label[for="attention-kind"]')).not.toBeNull();
    expect(panel.shadowRoot.querySelector('label[for="attention-project"]')).not.toBeNull();
  });

  it("navigates deliberately to an archived Assist chat and retains unresolved input through a read/refresh", async () => {
    const panel = setup();
    panel._projects = [{ project_id: "two", archived_at: "2026-09-27" }];
    panel._threads = [{ thread_id: "archived", project_id: "two", archived_at: "2026-09-27", schedule_eligible: false }];
    vi.spyOn(panel, "_refreshSelectedThreadAndStartPolling").mockResolvedValue(true);
    await panel._loadAttentionInbox();
    list(panel).querySelector('[data-thread-id="archived"]').click();
    await vi.waitFor(() => expect(panel._selectedThreadId).toBe("archived"));
    expect(panel._threadSelectionDeliberate).toBe(true);
    panel._callWS.mockImplementation(async (action) => action === "list_threads" ? panel._threads : { items: rows, truncated: false });
    await panel._loadThreads();
    await panel._loadAttentionInbox(true);
    expect(panel._selectedThreadId).toBe("archived");
    expect(list(panel).textContent).toContain("Response needed");
    expect(panel._callWS.mock.calls.every(([action]) => ["attention_inbox", "list_threads", "update_thread"].includes(action))).toBe(true);
  });

  it("throttles ordinary refresh but allows retry and explicit refresh after resolution", async () => {
    const panel = setup();
    await panel._loadAttentionInbox();
    await panel._loadAttentionInbox();
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    panel._callWS.mockRejectedValueOnce(new Error("private detail"));
    await panel._loadAttentionInbox(true);
    expect(list(panel).textContent).toContain("Refresh to try again");
    expect(list(panel).textContent).not.toContain("private detail");
    panel._callWS.mockResolvedValue({ items: [], truncated: false });
    await panel._loadAttentionInbox(true);
    expect(list(panel).textContent).toContain("No current attention items");
  });

  it.each(["user", "connection"])("clears old rows and rejects an in-flight reply when %s changes", async (change) => {
    const panel = setup();
    await panel._loadAttentionInbox();
    let resolve;
    panel._callWS.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const oldLoad = panel._loadAttentionInbox(true);
    vi.spyOn(panel, "_render").mockImplementation(() => {});
    panel.hass = { ...panel._hass, ...(change === "user" ? { user: { id: "bob" } } : { connection: {} }) };
    expect(panel._attentionData).toBeNull();
    expect(list(panel).textContent).not.toContain("Question");
    await panel._loadAttentionInbox();
    resolve({ items: [{ ...rows[0], chat_title: "Stale response" }], truncated: false });
    await oldLoad;
    expect(list(panel).textContent).not.toContain("Stale response");
    expect(panel._attentionLoading).toBe(false);
    expect(panel._callWS).toHaveBeenCalledTimes(3);
  });

  it("shows bounded status and hides unsupported inboxes without a request", async () => {
    const panel = setup();
    panel._callWS.mockResolvedValue({ items: rows, truncated: true });
    await panel._loadAttentionInbox();
    expect(list(panel).textContent).toContain("bounded view");
    panel._config.capabilities = [];
    panel._renderAttentionInbox();
    await panel._loadAttentionInbox(true);
    expect(panel.shadowRoot.getElementById("attention-inbox").hidden).toBe(true);
    expect(panel._callWS).toHaveBeenCalledTimes(1);
  });
});
