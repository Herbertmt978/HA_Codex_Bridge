import { describe, expect, it } from "vitest";
import { proposeAutomationEditDescription, proposeScheduleDescription } from "../src/schedule-language.js";
import { buildAutomationPayload } from "../src/scheduled-tasks.js";

const now = Date.parse("2026-09-19T12:00:00Z");
const options = { timezone: "Europe/London", now };

describe("conversational schedule proposal", () => {
  it.each([
    ["Daily at 09:00, summarise yesterday's events", "daily", "09:00", "rrule"],
    ["Every weekday at 9 am, check the heating", "weekdays", "09:00", "rrule"],
    ["Every Monday at 2 pm, report energy use", "weekly", "14:00", "rrule"],
    ["Monthly on the 15th at 09:00, prepare the budget", "monthly", "09:00", "rrule"],
    ["Every 2 hours starting tomorrow at 9 am, check the system", "interval", "09:00", "interval"],
    ["On 24 September 2026 at 09:00, prepare a report", "once", "09:00", "once"],
  ])("proposes the existing editor values for %s", (description, repeat, time, kind) => {
    const values = proposeScheduleDescription(description, options);
    expect(values).toMatchObject({ repeat, time, timezone: "Europe/London", mode: "observe", model: "", thinking: "" });
    expect(buildAutomationPayload(values, { ...options, projectId: "p1" }).schedule.kind).toBe(kind);
  });

  it("leaves chat continuation and privileged permissions for explicit review", () => {
    const values = proposeScheduleDescription("Daily at 09:00, ask Codex to inspect Home Assistant", options);
    expect(values.target_kind).toBe("standalone");
    expect(values.mode).toBe("observe");
    expect(values.prompt).toBe("inspect Home Assistant");
    expect(buildAutomationPayload(values, { ...options, projectId: "p1" })).not.toHaveProperty("host_access_grant");
  });

  it("understands a task-first weekday request and an explicit current-chat destination", () => {
    const taskFirst = proposeScheduleDescription("Remind me to check the heating every weekday at 9 am", options);
    expect(taskFirst).toMatchObject({ repeat: "weekdays", prompt: "check the heating", title: "check the heating" });
    const currentChat = proposeScheduleDescription("Daily at 09:00 in this chat, report changes", options);
    expect(currentChat.target_kind).toBe("continue_thread");
    expect(buildAutomationPayload(currentChat, { ...options, threadId: "t1" }).target).toEqual({ kind: "continue_thread", thread_id: "t1" });
  });

  it.each([
    ["Daily at 9, check the heating", /09:00 or 9 am/],
    ["On 09/24/2026 at 09:00, check the heating", /numeric day\/month order/],
    ["On 29 March 2026 at 01:30, check the heating", /clocks change/],
    ["On 25 October 2026 at 01:30, check the heating", /occurs twice/],
    ["Tomorrow at 9 am, ", /what Codex should do/],
  ])("asks for clarification rather than creating %s", (description, message) => {
    expect(() => proposeScheduleDescription(description, { ...options, now: Date.parse("2026-01-01T00:00:00Z") })).toThrow(message);
  });
});

describe("conversational automation edit proposal", () => {
  it.each([
    ["Rename this task to Morning report", { name: "Morning report" }],
    ["please rename to Morning report", { name: "Morning report" }],
    ["Set the title to Weekly check", { name: "Weekly check" }],
    ["Please set title to Weekly check", { name: "Weekly check" }],
    ["Set instructions to Check the new files", { prompt: "Check the new files" }],
    ["please change the instructions to Review the latest report", { prompt: "Review the latest report" }],
  ])("parses %s into exactly the requested field", (description, expected) => {
    expect(proposeAutomationEditDescription(description)).toEqual(expected);
  });

  it("preserves multiline instruction text apart from surrounding whitespace", () => {
    expect(proposeAutomationEditDescription("Set instructions to  Summarise the report.\nThen list actions.  "))
      .toEqual({ prompt: "Summarise the report.\nThen list actions." });
  });

  it("returns hostile instruction content as inert prompt data only", () => {
    const result = proposeAutomationEditDescription("Set instructions to <script>alert('x')</script>");
    expect(result).toEqual({ prompt: "<script>alert('x')</script>" });
    expect(Object.keys(result)).toEqual(["prompt"]);
  });

  it.each([
    ["", /Rename this task/],
    ["Pause this task", /Rename this task/],
    ["Rename this task to", /new title/],
    ["Set the instructions to   ", /new instructions/],
    [`Set the title to ${"a".repeat(161)}`, /160 characters/],
    [`Set the instructions to ${"a".repeat(4000)}`, /4,000 characters/],
  ])("rejects ambiguous, unsupported or invalid request %s", (description, message) => {
    expect(() => proposeAutomationEditDescription(description)).toThrow(message);
  });

  it("accepts a title at the Bridge's 160-character limit", () => {
    expect(proposeAutomationEditDescription(`Set the title to ${"a".repeat(160)}`))
      .toEqual({ name: "a".repeat(160) });
  });
});
