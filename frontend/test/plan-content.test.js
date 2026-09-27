import { describe, expect, it } from "vitest";

import { activePlanText, projectCompletedPlanMessages } from "../src/plan-content.js";
import { projectTranscriptMessages } from "../src/conversation-timeline.js";

const event = (sequence, event_type, payload = {}, metadata = {}) => ({ sequence, event_type, payload, ...metadata });

describe("native Plan content projection", () => {
  it("projects only acknowledged plan text at item completion and preserves event routing metadata", () => {
    const input = [
      event(1, "message.created", { run_id: "run-a", role: "user", text: "Make a plan" }),
      event(2, "run.started", { run_id: "run-a" }),
      event(3, "item.started", { item_type: "plan", item_id: "plan-a", run_id: "run-a" }),
      event(4, "plan.delta", { delta: "# Plan\n", item_id: "plan-a", run_id: "run-a", byte_offset: 0, chunk_index: 0 }),
      event(5, "plan.delta", { delta: "<script>keep as text</script>", item_id: "plan-a", run_id: "run-a", byte_offset: 0, chunk_index: 0 }),
      event(6, "item.completed", { item_type: "plan", item_id: "plan-a", run_id: "run-a" }, { scope: "thread", thread_id: "thread-a", cursor: "cursor-6" }),
      event(7, "run.completed", { run_id: "run-a" }),
    ];
    const original = structuredClone(input);

    const projected = projectCompletedPlanMessages(input);
    const transcript = projectTranscriptMessages(projected);
    const planMessage = transcript.find((item) => item.event_type === "message.completed");

    expect(planMessage).toEqual({
      ...input[5],
      event_type: "message.completed",
      payload: { run_id: "run-a", item_id: "plan-a", role: "assistant", text: "# Plan\n<script>keep as text</script>", plan: true },
    });
    expect(transcript).toContainEqual(input[5]);
    expect(input).toEqual(original);
  });

  it("joins globally ordered zero-offset chunks faithfully and isolates runs and items", () => {
    const input = [
      event(1, "item.started", { item_type: "plan", item_id: "same", run_id: "run-a" }),
      event(2, "item.started", { item_type: "plan", item_id: "same", run_id: "run-b" }),
      event(3, "plan.delta", { delta: "first ", item_id: "same", run_id: "run-a", byte_offset: 0, chunk_index: 0 }),
      event(4, "plan.delta", { delta: "other ", item_id: "same", run_id: "run-b", byte_offset: 0, chunk_index: 0 }),
      event(5, "plan.delta", { delta: "second", item_id: "same", run_id: "run-a", byte_offset: 0, chunk_index: 0 }),
      event(6, "item.completed", { item_type: "plan", item_id: "same", run_id: "run-b" }),
      event(7, "item.completed", { item_type: "plan", item_id: "same", run_id: "run-a" }),
    ];

    expect(projectCompletedPlanMessages(input).filter((item) => item.payload?.plan).map((item) => item.payload.text)).toEqual(["other ", "first second"]);
  });

  it("does not project incomplete, cancelled, empty, malformed, or progress-only plans", () => {
    const input = [
      event(1, "item.started", { item_type: "plan", item_id: "incomplete", run_id: "run-a" }),
      event(2, "plan.delta", { delta: "unfinished", item_id: "incomplete", run_id: "run-a" }),
      event(3, "item.started", { item_type: "plan", item_id: "cancelled", run_id: "run-b" }),
      event(4, "plan.delta", { delta: "partial", item_id: "cancelled", run_id: "run-b" }),
      event(5, "run.cancelled", { run_id: "run-b" }),
      event(6, "item.completed", { item_type: "plan", item_id: "cancelled", run_id: "run-b" }),
      event(7, "item.started", { item_type: "plan", item_id: "empty", run_id: "run-c" }),
      event(8, "item.completed", { item_type: "plan", item_id: "empty", run_id: "run-c" }),
      event(9, "plan.updated", { run_id: "run-d", plan: [{ step: "Progress only", status: "completed" }] }),
      event(10, "item.completed", { item_type: "plan", item_id: "missing-start", run_id: "run-d" }),
      event(11, "item.started", { item_type: "plan", item_id: "bad-id", run_id: "" }),
      event(12, "plan.delta", { delta: "bad", item_id: "bad-id", run_id: "" }),
      event(13, "item.completed", { item_type: "plan", item_id: "bad-id", run_id: "" }),
    ];

    expect(projectCompletedPlanMessages(input)).toEqual(input);
    expect(activePlanText(input, "run-a", "incomplete")).toBe("unfinished");
    expect(activePlanText(input, "run-b", "cancelled")).toBe("");
    expect(activePlanText([null, { event_type: "plan.delta", payload: { delta: "stray" } }], "run-a", "incomplete")).toBe("");
  });

  it("bounds text and suppresses duplicate retained events without using chunk offsets", () => {
    const start = event(1, "item.started", { item_type: "plan", item_id: "bounded", run_id: "run-a" });
    const firstDelta = event(2, "plan.delta", { delta: "x".repeat(120_000), item_id: "bounded", run_id: "run-a", byte_offset: 0, chunk_index: 0 });
    const duplicateDelta = structuredClone(firstDelta);
    const secondDelta = event(3, "plan.delta", { delta: "y".repeat(120_000), item_id: "bounded", run_id: "run-a", byte_offset: 0, chunk_index: 0 });
    const completed = event(4, "item.completed", { item_type: "plan", item_id: "bounded", run_id: "run-a" });
    const duplicateCompleted = structuredClone(completed);

    const projected = projectCompletedPlanMessages([start, firstDelta, duplicateDelta, secondDelta, completed, duplicateCompleted]);
    const messages = projected.filter((item) => item.event_type === "message.completed");

    expect(messages).toHaveLength(1);
    expect(messages[0].payload.text).toHaveLength(200_000);
    expect(messages[0].payload.text).toBe("x".repeat(120_000) + "y".repeat(80_000));
  });

  it("does not turn ordinary assistant responses or non-plan items into plan messages", () => {
    const input = [
      event(1, "message.completed", { run_id: "run-a", role: "assistant", text: "Ordinary response" }),
      event(2, "item.started", { item_type: "commandExecution", item_id: "cmd", run_id: "run-a" }),
      event(3, "plan.delta", { delta: "unowned", item_id: "cmd", run_id: "run-a" }),
      event(4, "item.completed", { item_type: "commandExecution", item_id: "cmd", run_id: "run-a" }),
    ];
    expect(projectCompletedPlanMessages(input)).toEqual(input);
  });
});
