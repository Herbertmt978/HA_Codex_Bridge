import { describe, expect, it, vi } from "vitest";
import { ChildAgentsView } from "../src/child-agents.js";

const child = (fields = {}) => ({
  child_id: "a".repeat(32), revision: 2, label: "Reviewer", task: "Check tests",
  status: "running", stale: true, can_stop: false, can_follow_up: false,
  follow_up_reason: "Direct child follow-up is unavailable.", ...fields,
});
function setup() {
  const host = document.createElement("div");
  host.attachShadow({ mode: "open" });
  const container = document.createElement("div");
  host.shadowRoot.append(container);
  document.body.append(host);
  const panel = {
    _selectedThreadId: "parent", _config: { capabilities: ["subagents_v1"] },
    _hass: { user: { id: "owner-one" } }, _threadSelectionEpoch: 1,
    _activeThread: { status: "running" }, shadowRoot: host.shadowRoot, _callWS: vi.fn(),
    _renderActivityCenter: () => { container.replaceChildren(); view.render(container); },
  };
  const view = new ChildAgentsView(panel);
  panel._renderActivityCenter();
  return { view, panel, container };
}

describe("individual runtime children", () => {
  it("hides older-runtime and Assist controls without issuing requests", () => {
    const { view, panel, container } = setup();
    panel._config.capabilities = [];
    container.replaceChildren(); view.render(container);
    expect(container.textContent).toBe("");
    expect(panel._callWS).not.toHaveBeenCalled();
  });
  it("shows empty, stale, completed and public results as inert text", async () => {
    const { view, panel, container } = setup();
    panel._callWS.mockResolvedValue({ children: [child(), child({ child_id: "b".repeat(32), status: "completed", result: "<img src=x onerror=alert(1)>" })] });
    await view.load();
    expect(container.querySelectorAll("details")).toHaveLength(2);
    expect(container.textContent).toContain("Last known status");
    expect(container.textContent).toContain("Completed");
    expect(container.querySelector("img")).toBeNull();
    const followups = [...container.querySelectorAll("button")].filter((b) => b.textContent.includes("Follow-up"));
    expect(followups.every((b) => b.disabled && b.hasAttribute("aria-describedby"))).toBe(true);
    panel._callWS.mockResolvedValue({ children: [] });
    await view.load();
    expect(container.textContent).toContain("No retained children");
  });
  it("verifies and stops only the selected child with a reviewed revision", async () => {
    const { view, panel, container } = setup();
    panel._callWS.mockResolvedValueOnce({ children: [child(), child({ child_id: "b".repeat(32) })] })
      .mockResolvedValueOnce(child({ revision: 3, stale: false, can_stop: true }))
      .mockResolvedValueOnce({ outcome: "accepted", child: child({ revision: 5, stop_pending: true }) });
    await view.load();
    await view.action(view.rows[0], "refresh");
    await view.action(view.rows[0], "stop");
    expect(panel._callWS.mock.calls[2][1]).toMatchObject({
      thread_id: "parent", child_id: "a".repeat(32), action: "stop", revision: 3,
    });
    expect(panel._callWS.mock.calls[2][1].client_request_id).toBeTruthy();
    expect(container.textContent).toContain("Stop requested for this child");
    expect(view.rows[1].child_id).toBe("b".repeat(32));
  });
  it("drops late results after selecting another parent", async () => {
    const { view, panel } = setup();
    let finish;
    panel._callWS.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const request = view.load();
    panel._selectedThreadId = "other";
    view.sync();
    finish({ children: [child()] });
    await request;
    expect(view.rows).toEqual([]);
    expect(view.busy).toBe(false);
  });
  it("invalidates cached controls and in-flight results on reconnect", async () => {
    const { view, panel } = setup();
    panel._callWS.mockResolvedValueOnce({ children: [child({ stale: false, can_stop: true })] });
    await view.load();
    let finish;
    panel._callWS.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const request = view.action(view.rows[0], "refresh");
    view.invalidate();
    finish(child({ stale: false, can_stop: true }));
    await request;
    expect(view.rows[0].stale).toBe(true);
    expect(view.rows[0].can_stop).toBe(false);
  });
  it.each(["list", "refresh", "stop"])("drops late %s responses across HA owner away-and-back changes", async (operation) => {
    const { view, panel } = setup();
    panel._callWS.mockResolvedValueOnce({ children: [child({ stale: false, can_stop: true })] });
    await view.load();
    view.open.add(view.rows[0].child_id);
    let finish;
    panel._callWS.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const request = operation === "list" ? view.load() : view.action(view.rows[0], operation);
    panel._hass = { user: { id: "owner-two" } }; view.sync();
    expect(view.rows).toEqual([]);
    expect(view.open.size).toBe(0);
    panel._hass = { user: { id: "owner-one" } }; view.sync();
    finish(operation === "list" ? { children: [child()] }
      : operation === "stop" ? { outcome: "accepted", child: child() } : child());
    await request;
    expect(view.rows).toEqual([]);
    expect(view.loaded).toBe(false);
    expect(view.notice).toBe("");
  });
  it("clears on disconnect and rejects late responses across chat selection epochs", async () => {
    const { view, panel } = setup();
    let finish;
    panel._callWS.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const request = view.load();
    view.clear();
    panel._threadSelectionEpoch += 2;
    finish({ children: [child()] });
    await request;
    expect(view.rows).toEqual([]);
    expect(view.loaded).toBe(false);
  });
  it("shows failed and uncertain actions without an automatic retry", async () => {
    const { view, panel, container } = setup();
    panel._callWS.mockRejectedValueOnce(new Error("private service error"));
    await view.load();
    expect(container.textContent).toContain("could not be confirmed");
    expect(container.textContent).not.toContain("private service error");
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    panel._callWS.mockResolvedValueOnce({ children: [child({ stale: false, can_stop: true })] })
      .mockResolvedValueOnce({ outcome: "unknown", child: child({ stop_pending: true }) });
    await view.load();
    await view.action(view.rows[0], "stop");
    expect(container.textContent).toContain("stop outcome is unknown");
  });
});
