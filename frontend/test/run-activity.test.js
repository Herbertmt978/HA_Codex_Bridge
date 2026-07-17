import { describe, expect, it } from "vitest";

import { getRunActivityViewModel } from "../src/run-activity.js";

function event(sequence, event_type, payload = {}, run_id = "run-1") {
  return { event_id: `evt_${sequence}`, sequence, event_type, payload: { run_id, ...payload } };
}

describe("run activity view model", () => {
  it("projects a safe current plan step and bounded patch counts", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [
        event(1, "run.started"),
        event(2, "plan.updated", {
          plan: [
            { step: "Inspect the repository", status: "completed" },
            { step: "Implement the focused change", status: "inProgress" },
            { step: "Run tests", status: "pending" },
          ],
        }),
        event(3, "patch.updated", {
          changes: [
            { path: "src/new.js", kind: { type: "add" }, diff: "+++ new\n+added" },
            { path: "src/old.js", kind: { type: "delete" }, diff: "--- old\n-removed" },
            { path: "src/existing.js", kind: { type: "update" }, diff: "@@\n-old\n+new\n+another" },
          ],
        }),
      ],
    );

    expect(model).toMatchObject({
      state: "running",
      busy: true,
      action: "Implement the focused change",
      step: { index: 2, total: 3, label: "Implement the focused change" },
      files: { changed: 3, additions: 3, deletions: 2 },
    });
    expect(model.stages).toEqual([
      { index: 1, label: "Inspect the repository", status: "completed" },
      { index: 2, label: "Implement the focused change", status: "inProgress" },
      { index: 3, label: "Run tests", status: "pending" },
    ]);
    expect(model.actionHistory.map((item) => item.label)).toContain("Inspect the repository");
    expect(JSON.stringify(model)).not.toContain("private diff");
  });

  it("uses bounded text-only reasoning activity before a response", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [event(1, "reasoning.summary_delta", { delta: "  Compare the two files\nthen choose the safer path.  " })],
    );

    expect(model.action).toBe("Compare the two files then choose the safer path.");
    expect(model.assistant).toBe("idle");
  });

  it("falls back to allowlisted item labels and never reflects command metadata", () => {
    const hostile = {
      command: "cat /private/secret",
      cwd: "C:\\private",
      query: "secret query",
      args: ["secret"],
    };
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [event(1, "item.started", { item_type: "commandExecution", action_types: ["read", "listFiles"], ...hostile })],
    );

    expect(model.action).toBe("Reading files and listing files");
    expect(JSON.stringify(model)).not.toContain("secret");
    expect(JSON.stringify(model)).not.toContain("private");
  });

  it("clears the current activity when that same item completes", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [
        event(1, "item.started", { item_id: "command-1", item_type: "commandExecution", action_types: ["read"] }),
        event(2, "item.completed", { item_id: "command-1", item_type: "commandExecution" }),
      ],
    );

    expect(model.action).toBe("Working on the request");
  });

  it("shows allowlisted subagent operations and aggregate states without topology or content", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [event(1, "item.started", {
        item_type: "collabAgentToolCall",
        operation: "wait",
        agent_state_counts: { running: 2, completed: 1, "secret-agent": 99 },
        prompt: "private prompt",
        agentPath: "/private/workspace",
        agentThreadId: "private-thread",
      })],
    );

    expect(model.action).toBe("Waiting for sub-agents");
    expect(model.subagents).toMatchObject({ total: 3, active: 2, completed: 1, attention: 0, label: "2 active · 1 complete" });
    expect(JSON.stringify(model)).not.toMatch(/secret|private|workspace|thread/i);
  });

  it("only accepts subagent snapshots with the active run ID", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [
        event(1, "item.started", { item_type: "collabAgentToolCall", agent_state_counts: { running: 1 } }),
        event(2, "item.started", { item_type: "collabAgentToolCall", agent_state_counts: { completed: 9 } }, null),
        event(3, "item.started", { item_type: "collabAgentToolCall", agent_state_counts: { completed: 8 } }, "run-2"),
      ],
    );

    expect(model.subagents).toMatchObject({ total: 1, active: 1, completed: 0, sourceSequence: 1 });

    const onlyOtherRuns = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [
        event(1, "item.started", { item_type: "collabAgentToolCall", agent_state_counts: { completed: 9 } }, null),
        event(2, "item.started", { item_type: "collabAgentToolCall", agent_state_counts: { completed: 8 } }, "run-2"),
      ],
    );

    expect(onlyOtherRuns.subagents).toBeNull();
  });

  it.each(["completed", "cancelled", "failed"])("clears stale active subagents when a run is %s", (terminalState) => {
    const model = getRunActivityViewModel(
      { status: "idle", active_run_id: null },
      [
        event(1, "item.started", {
          item_type: "collabAgentToolCall",
          agent_state_counts: { pendingInit: 1, running: 2, completed: 3, errored: 4 },
        }),
        event(2, `run.${terminalState}`),
      ],
    );

    expect(model.subagents).toMatchObject({
      total: 7,
      active: 0,
      completed: 3,
      attention: 4,
      label: "3 complete \u00b7 4 need attention",
    });
  });

  it("retains active subagents while a run is in progress", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [event(1, "item.started", {
        item_type: "collabAgentToolCall",
        agent_state_counts: { pendingInit: 1, running: 2, completed: 3, errored: 4 },
      })],
    );

    expect(model.subagents).toMatchObject({
      total: 10,
      active: 3,
      completed: 3,
      attention: 4,
      label: "3 active \u00b7 3 complete \u00b7 4 need attention",
    });
  });

  it.each([
    ["queued", "queued", false],
    ["completed", "completed", true],
    ["failed", "failed", true],
    ["cancelled", "cancelled", true],
    ["interrupted", "interrupted", true],
  ])("projects %s terminal/queue state from events", (eventType, expectedState, terminal) => {
    const model = getRunActivityViewModel(
      { status: eventType === "queued" ? "queued" : "idle", active_run_id: null },
      [event(1, `run.${eventType}`)],
    );

    expect(model.state).toBe(expectedState);
    expect(model.terminal).toBe(terminal);
    expect(model.busy).toBe(eventType === "queued");
  });

  it("distinguishes streamed and completed assistant responses", () => {
    const streaming = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [event(1, "message.delta", { delta: "partial" })],
    );
    const complete = getRunActivityViewModel(
      { status: "idle", active_run_id: null },
      [event(1, "message.completed", { text: "final response" }), event(2, "run.completed")],
    );

    expect(streaming.assistantState).toBe("streaming");
    expect(complete.assistant).toBe("complete");
    expect(complete.state).toBe("completed");
  });

  it("does not resurrect orphaned activity from an idle thread snapshot", () => {
    const model = getRunActivityViewModel(
      { status: "idle", active_run_id: "run-old" },
      [
        event(1, "item.started", { item_type: "agentMessage" }, "run-old"),
        event(2, "message.delta", { text: "stale response" }, "run-old"),
      ],
    );

    expect(model).toMatchObject({
      state: "idle",
      busy: false,
      runId: "",
      action: "",
      assistantState: "idle",
    });
  });

  it("keeps terminal history while ignoring stale work from another run", () => {
    const model = getRunActivityViewModel(
      { status: "idle", active_run_id: "run-old" },
      [
        event(1, "run.completed", {}, "run-current"),
        event(2, "message.delta", { text: "stale response" }, "run-old"),
        event(3, "item.started", { item_type: "agentMessage" }, "run-old"),
      ],
    );

    expect(model).toMatchObject({
      state: "completed",
      busy: false,
      runId: "run-current",
      action: "Run completed",
      assistantState: "idle",
    });
  });

  it("keeps event-only activity working when a thread snapshot has no status", () => {
    const model = getRunActivityViewModel(
      { active_run_id: "run-activity" },
      [event(1, "run.started", {}, "run-activity")],
    );

    expect(model).toMatchObject({ state: "running", busy: true, runId: "run-activity" });
  });

  it("retains a terminal event that has no run identifier", () => {
    const model = getRunActivityViewModel(
      { status: "idle", active_run_id: "run-old" },
      [event(1, "run.completed", {}, null)],
    );

    expect(model).toMatchObject({ state: "completed", terminal: true, action: "Run completed" });
  });

  it("keeps a busy snapshot active when it has no current run identifier", () => {
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: null },
      [event(1, "run.completed", {}, "run-historical")],
    );

    expect(model).toMatchObject({ state: "running", busy: true, runId: "", action: "Working on the request" });
  });

  it("uses the current queued run instead of terminal history", () => {
    const model = getRunActivityViewModel(
      { status: "queued", active_run_id: null },
      [
        event(1, "run.completed", {}, "run-historical"),
        event(2, "run.queued", {}, "run-current"),
      ],
    );

    expect(model).toMatchObject({ state: "queued", busy: true, runId: "run-current", action: "Waiting in queue" });
  });

  it("never leaves a terminal run in streaming response state", () => {
    const model = getRunActivityViewModel(
      { status: "idle", active_run_id: null },
      [
        event(1, "run.completed", {}, "run-complete"),
        event(2, "message.delta", { text: "late stale delta" }, "run-complete"),
      ],
    );

    expect(model).toMatchObject({ state: "completed", busy: false, assistantState: "idle" });
  });

  it("joins reasoning summary chunks and keeps a terminal event when a later event has no run id", () => {
    const chunks = [
      event(1, "reasoning.summary_delta", { item_id: "reason-1", summary_index: 0, delta: "first " }),
      event(2, "reasoning.summary_delta", { item_id: "reason-1", summary_index: 0, delta: "second" }),
    ];
    const streaming = getRunActivityViewModel({ status: "running", active_run_id: "run-1" }, chunks);
    const model = getRunActivityViewModel({ status: "idle", active_run_id: null }, [
      event(3, "run.completed"),
      ...chunks,
      event(4, "thread.updated", {}, null),
    ]);

    expect(streaming.action).toBe("first second");
    expect(model.state).toBe("completed");
    expect(model.action).toBe("Run completed");
  });

  it("bounds oversized plans, history, events, and malformed patch changes", () => {
    const plan = Array.from({ length: 300 }, (_, index) => ({
      step: index === 127 ? "step-127" : index === 299 ? "last step" : `step-${index}`,
      status: index === 127 ? "inProgress" : "completed",
    }));
    const changes = Array.from({ length: 3000 }, (_, index) => ({
      path: `src/file-${index}.js`,
      kind: { type: index % 2 ? "update" : "add" },
      diff: "x".repeat(100000),
    }));
    const model = getRunActivityViewModel(
      { status: "running", active_run_id: "run-1" },
      [...Array.from({ length: 2500 }, (_, index) => event(index + 1, "reasoning.summary_delta", { delta: `reason-${index}` })),
        event(2501, "plan.updated", { plan }), event(2502, "patch.updated", { changes })],
    );

    expect(model.step?.total).toBe(128);
    expect(model.step?.label).toBe("step-127");
    expect(model.actionHistory.length).toBeLessThanOrEqual(8);
    expect(model.files.changed).toBeLessThanOrEqual(2048);
    expect(JSON.stringify(model).length).toBeLessThan(10000);
  });
});
