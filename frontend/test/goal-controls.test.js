import { beforeEach, describe, expect, it, vi } from "vitest";
import { GoalControls } from "../src/goal-controls.js";
import { getSafeRunFailureMessage } from "../src/run-activity.js";

const view = (status = "paused", revision = 1) => ({
  revision, continuation: "manual_turns_only", history: [],
  goal: { objective: "Fix <script>alert(1)</script>", completion_criteria: ["Data retained", "Bad input rejected"], progress: "Reproduced", status },
});
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
let element;
beforeEach(() => {
  element = document.createElement("section");
  document.body.replaceChildren(element);
});
function setup(call, options = {}) {
  const controls = new GoalControls(call, () => "test-action-id");
  controls.setContext(element, { threadId: "chat-one", available: true, ...options });
  return controls;
}
function button(action) { return element.querySelector(`[data-goal-action="${action}"]`); }

describe("manual goal controls", () => {
  it("loads persisted text safely, labels manual continuation and uses labelled inputs", async () => {
    const call = vi.fn().mockResolvedValue(view());
    setup(call);
    await flush();
    expect(element.querySelector("script")).toBeNull();
    expect(element.querySelector('[name="goal-objective"]').value).toContain("<script>");
    expect(element.textContent).toContain("it starts no work");
    expect(element.textContent).toContain("use the existing Stop control");
    expect(element.querySelectorAll("label textarea")).toHaveLength(3);
    expect(call).toHaveBeenCalledWith("get_goal", { thread_id: "chat-one" });
    expect(button("complete").disabled).toBe(true);
  });

  it("requires explicit confirmation and sends only a goal mutation, never a prompt", async () => {
    const call = vi.fn().mockResolvedValueOnce(view("active")).mockResolvedValueOnce(view("completed", 2));
    const controls = setup(call);
    await flush();
    await controls.act("complete");
    expect(call).toHaveBeenCalledTimes(1);
    const confirm = element.querySelector('[name="goal-confirm"]');
    confirm.checked = true;
    confirm.dispatchEvent(new Event("change"));
    expect(button("complete").disabled).toBe(false);
    button("complete").click();
    await flush();
    expect(call).toHaveBeenLastCalledWith("goal_action", {
      thread_id: "chat-one", action: "complete", expected_revision: 1,
      client_request_id: "test-action-id", completion_confirmed: true,
    });
    expect(element.textContent).toContain("completed");
    expect(button("resume")).toBeNull();
    expect(call.mock.calls.every(([command]) => ["get_goal", "goal_action"].includes(command))).toBe(true);
  });

  it("resumes without model submission and preserves the same mutation on uncertain retry", async () => {
    const call = vi.fn().mockResolvedValueOnce(view()).mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(view("active", 2));
    setup(call);
    await flush();
    button("resume").click();
    await flush();
    const initial = structuredClone(call.mock.calls[1][1]);
    expect(button("retry").hidden).toBe(false);
    button("retry").click();
    await flush();
    expect(call.mock.calls[2][1]).toEqual(initial);
    expect(element.textContent).toContain("applies to manual turns");
  });

  it("keeps edited input when a request fails and refresh is explicit", async () => {
    const call = vi.fn().mockResolvedValueOnce(view()).mockRejectedValueOnce(new Error("conflict"));
    const controls = setup(call);
    await flush();
    const input = element.querySelector('[name="goal-objective"]');
    input.value = "User draft survives conflict";
    button("edit").click();
    await flush();
    controls.setContext(element, { threadId: "chat-one", available: true, eligible: true });
    expect(element.querySelector('[name="goal-objective"]').value).toBe("User draft survives conflict");
    expect(element.querySelector('[role="status"]').textContent).toContain("not confirmed");
  });

  it("makes unavailable/Assist controls truthful and sends no request", () => {
    const call = vi.fn();
    setup(call, { available: false });
    expect(element.textContent).toContain("unavailable");
    expect(call).not.toHaveBeenCalled();
    setup(call, { eligible: false });
    expect(element.textContent).toContain("ordinary, unarchived");
    expect(element.querySelectorAll("button")).toHaveLength(0);
    expect(call).not.toHaveBeenCalled();
  });

  it("rejects stale selected-chat responses", async () => {
    let resolveFirst;
    const call = vi.fn().mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ ...view(), goal: null, revision: 0 });
    const controls = setup(call);
    controls.setContext(element, { threadId: "chat-two", available: true, eligible: true });
    await flush();
    resolveFirst(view());
    await flush();
    expect(controls.threadId).toBe("chat-two");
    expect(element.querySelector('[name="goal-objective"]').value).toBe("");
    expect(button("create")).not.toBeNull();
  });

  it("shows a load failure and allows retry, without inventing an empty goal", async () => {
    const call = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(view());
    setup(call);
    await flush();
    expect(element.textContent).toContain("could not be loaded");
    expect(button("create")).toBeNull();
    button("refresh").click();
    await flush();
    expect(button("resume")).not.toBeNull();
  });

  it("rejects prior-owner readback after switching away and back on the same chat", async () => {
    let resolveFirst;
    const call = vi.fn().mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(view("paused", 2)).mockResolvedValueOnce(view("cancelled", 3));
    const controls = setup(call, { ownerId: "one" });
    controls.setContext(element, { threadId: "chat-one", ownerId: "two", available: true });
    controls.setContext(element, { threadId: "chat-one", ownerId: "one", available: true });
    await flush();
    resolveFirst(view("active", 1));
    await flush();
    expect(controls.view.revision).toBe(3);
    expect(controls.view.goal.status).toBe("cancelled");
    expect(call).toHaveBeenCalledTimes(3);
  });

  it("clears pending mutation and ignores its late reply on owner change or disconnect", async () => {
    let resolveAction;
    const call = vi.fn().mockResolvedValueOnce(view())
      .mockImplementationOnce(() => new Promise((resolve) => { resolveAction = resolve; }))
      .mockResolvedValueOnce(view("cancelled", 3));
    const controls = setup(call, { ownerId: "one" });
    await flush();
    const action = controls.act("resume");
    expect(controls.pending.action).toBe("resume");
    controls.invalidate();
    expect(controls.pending).toBeNull();
    expect(controls.view).toBeNull();
    expect(element.children).toHaveLength(0);
    controls.setContext(element, { threadId: "chat-one", ownerId: "two", available: true });
    await flush();
    resolveAction(view("active", 2));
    await action;
    expect(controls.view.goal.status).toBe("cancelled");
    expect(controls.pending).toBeNull();
  });

  it("uses a fixed public stale-goal explanation rather than runtime error text", () => {
    const result = getSafeRunFailureMessage({ event_type: "run.failed", payload: { failure_type: "stale_goal", error: "private output" } });
    expect(result).toContain("send the retained prompt again");
    expect(result).not.toContain("private output");
  });
});
