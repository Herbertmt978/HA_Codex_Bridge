import { describe, expect, it } from "vitest";

import { MAX_CONVERSATION_TURNS, projectConversationTurns } from "../src/conversation-timeline.js";

const event = (sequence, event_type, payload = {}) => ({ sequence, event_type, payload });

describe("conversation timeline projection", () => {
  it("groups queued prompts and multiple assistant messages by run ID", () => {
    const turns = projectConversationTurns([
      event(1, "message.created", { run_id: "run-a", text: "First prompt" }),
      event(2, "run.queued", { run_id: "run-a" }),
      event(3, "message.created", { run_id: "run-b", text: "Second prompt" }),
      event(4, "message.completed", { run_id: "run-a", text: "Earlier answer" }),
      event(5, "message.completed", { run_id: "run-a", text: "Final answer" }),
      event(6, "message.completed", { run_id: "run-b", text: "Second answer" }),
      event(7, "run.started", { run_id: "run-a" }),
    ]);

    expect(turns).toEqual([
      expect.objectContaining({ key: "1", anchorSequence: 1, prompt: "First prompt", response: "Earlier answer · Final answer", queued: false }),
      expect.objectContaining({ key: "3", anchorSequence: 3, prompt: "Second prompt", response: "Second answer", queued: false }),
    ]);
  });

  it("keeps legacy events without run IDs usable and gives assistant-only history an anchor", () => {
    expect(projectConversationTurns([
      event(2, "message.created", { text: "Old prompt" }),
      event(4, "message.completed", { text: "Old response" }),
      event(8, "message.completed", { text: "Imported answer" }),
    ])).toEqual([
      expect.objectContaining({ key: "2", prompt: "Old prompt", response: "Old response · Imported answer" }),
    ]);
    expect(projectConversationTurns([
      event(8, "message.completed", { text: "Imported answer" }),
    ])).toEqual([expect.objectContaining({ key: "8", anchorSequence: 8, prompt: "", response: "Imported answer" })]);
  });

  it("bounds and truncates preview text without interpreting message content", () => {
    const hostile = "<script>not markup</script> " + "x".repeat(400);
    const textTurn = projectConversationTurns([
      event(1, "message.created", { run_id: "private-id", text: hostile }),
      event(2, "message.completed", { run_id: "private-id", text: hostile }),
    ]);
    const turns = projectConversationTurns(Array.from({ length: MAX_CONVERSATION_TURNS + 1 }, (_, index) =>
      event(index + 1, "message.created", { run_id: `run-${index}`, text: "later" })));

    expect(turns).toHaveLength(MAX_CONVERSATION_TURNS);
    expect(turns.at(-1).prompt).toBe("later");
    expect(textTurn[0].prompt).toHaveLength(120);
    expect(textTurn[0].prompt).toContain("<script>");
    expect(textTurn[0].response).toHaveLength(120);
    expect(textTurn[0].label).toHaveLength(120);
    expect(textTurn[0]).not.toHaveProperty("runId");
  });

  it("bounds repeated same-run steer prompts and handles legacy dequeue state", () => {
    const turns = projectConversationTurns([
      event(1, "message.created", { run_id: "active-run", text: "Start " + "a".repeat(100) }),
      event(2, "message.created", { run_id: "active-run", text: "Follow-up " + "b".repeat(100) }),
      event(3, "run.queued", { run_id: "active-run" }),
      event(4, "run.dequeued", { run_id: "active-run" }),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0].prompt).toHaveLength(120);
    expect(turns[0].label).toHaveLength(120);
    expect(turns[0].queued).toBe(false);
    expect(turns[0].prompt).toContain("Start");
  });

  it("tracks RuntimeBroker run.queued separately from message.created and clears it on run.started", () => {
    const queued = projectConversationTurns([
      event(1, "message.created", { run_id: "broker-run", text: "Queued prompt" }),
      event(2, "run.queued", { run_id: "broker-run" }),
    ]);
    const started = projectConversationTurns([
      event(1, "message.created", { run_id: "broker-run", text: "Queued prompt" }),
      event(2, "run.queued", { run_id: "broker-run" }),
      event(3, "run.started", { run_id: "broker-run" }),
    ]);

    expect(queued[0].queued).toBe(true);
    expect(queued[0].label).toContain("Queued");
    expect(started[0].queued).toBe(false);
  });

  it("ignores malformed and non-message events", () => {
    expect(projectConversationTurns([
      null,
      undefined,
      event(0, "message.created", { text: "bad sequence" }),
      event(1, "run.started", { run_id: "run" }),
      { event_type: "message.created", payload: { text: "bad shape" } },
    ])).toEqual([]);
  });

  it.each(["completed", "cancelled", "failed", "interrupted"])("does not keep an empty %s turn in progress or queued", (outcome) => {
    const [turn] = projectConversationTurns([
      event(1, "message.created", { run_id: "owned-run", text: "Work " + "x".repeat(200) }),
      event(2, "run.queued", { run_id: "owned-run" }),
      event(3, "run.queue_cleared", { run_id: "owned-run", reason: "private diagnostic" }),
      event(4, `run.${outcome}`, { run_id: "owned-run", reason: "private diagnostic" }),
    ]);
    expect(turn.pending).toBe(false);
    expect(turn.queued).toBe(false);
    expect(turn.outcomeLabel).toContain(outcome);
    expect(turn.label).not.toContain("response in progress");
    expect(turn.label).not.toContain("private diagnostic");
    expect(turn.label).toHaveLength(120);
  });

  it("clears legacy queued turns when the queue-cleared event has no run ID", () => {
    const turns = projectConversationTurns([
      event(1, "message.created", { run_id: "first", text: "First", queued: true }),
      event(2, "message.created", { run_id: "second", text: "Second", queued: true }),
      event(3, "run.queue_cleared", { queued_count: 2, reason: "private diagnostic" }),
    ]);
    expect(turns.every((turn) => !turn.queued && !turn.pending)).toBe(true);
    expect(turns.every((turn) => turn.outcomeLabel === "Queued turn cleared")).toBe(true);
  });
});
