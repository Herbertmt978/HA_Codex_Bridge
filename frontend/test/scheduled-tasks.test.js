import { describe, expect, it } from "vitest";
import { buildAutomationPayload, buildAutomationUpdatePayload, buildSchedule, scheduleFormValues, scheduleInstant, scheduleSummary } from "../src/scheduled-tasks.js";

const now = Date.parse("2026-09-19T12:00:00Z");
const context = { projectId: "p1", threadId: "t1", timezone: "Europe/London", now };
const values = () => ({ ...scheduleFormValues({}, context.timezone, now), title: "Morning report", prompt: "Summarise the overnight events" });

describe("scheduled task form contract", () => {
  it("creates a daily task in the current workspace without implementation fields", () => {
    expect(buildAutomationPayload(values(), context)).toEqual({
      name: "Morning report", prompt: "Summarise the overnight events", target: { kind: "standalone", project_id: "p1" },
      schedule: { kind: "rrule", rule: "RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0", start_at: "2026-09-19T08:00:00.000Z", timezone: "Europe/London" },
      mode: "observe", model: null, thinking: null,
    });
  });

  it("continues the current chat only when selected", () => {
    expect(buildAutomationPayload({ ...values(), target_kind: "continue_thread" }, context).target).toEqual({ kind: "continue_thread", thread_id: "t1" });
    expect(() => buildAutomationPayload(values(), { ...context, projectId: null })).toThrow(/Select a chat or workspace/);
    expect(() => buildAutomationPayload({ ...values(), prompt: " " }, context)).toThrow(/title/);
  });

  it.each([
    ["weekdays", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
    ["weekly", "FREQ=WEEKLY;BYDAY=MO"],
    ["monthly", "FREQ=MONTHLY;BYMONTHDAY=15"],
  ])("converts %s into the existing timezone-aware schedule", (repeat, rule) => {
    const schedule = buildSchedule({ ...values(), repeat, weekday: "MO", month_day: "15" }, context);
    expect(schedule.rule).toBe(`RRULE:${rule};BYHOUR=9;BYMINUTE=0;BYSECOND=0`);
    expect(schedule.timezone).toBe("Europe/London");
    expect(scheduleSummary(schedule)).not.toMatch(/RRULE|FREQ=/);
  });

  it("validates once and interval timing", () => {
    expect(buildSchedule({ ...values(), repeat: "once", date: "2026-09-20" }, context)).toEqual({ kind: "once", at: "2026-09-20T08:00:00.000Z" });
    expect(() => buildSchedule({ ...values(), repeat: "once" }, context)).toThrow(/future/);
    expect(buildSchedule({ ...values(), repeat: "interval", interval_count: "2", interval_unit: "hours" }, context).seconds).toBe(7200);
    expect(() => buildSchedule({ ...values(), repeat: "interval", interval_count: "0", interval_unit: "minutes" }, context)).toThrow(/interval/);
    expect(() => buildSchedule({ ...values(), repeat: "interval", interval_count: "1.5", interval_unit: "minutes" }, context)).toThrow(/interval/);
  });

  it.each([
    { kind: "interval", seconds: 90, anchor_at: "2026-01-01T00:00:37Z" },
    { kind: "once", at: "2026-01-01T00:00:37Z" },
    { kind: "rrule", rule: "RRULE:FREQ=DAILY;COUNT=8;BYHOUR=9,17", start_at: "2026-01-01T09:00:00Z", timezone: "Europe/London" },
    { kind: "rrule", rule: "RRULE:FREQ=WEEKLY;BYDAY=FR;INTERVAL=2", start_at: "2026-01-01T12:00:00Z", timezone: "America/New_York" },
    { kind: "rrule", rule: "RRULE:FREQ=DAILY", start_at: "2026-01-01T12:00:17Z", timezone: "UTC" },
  ])("preserves an existing schedule exactly when editing its title", (schedule) => {
    const editing = { name: "Before", prompt: "Keep this", target: { kind: "continue_thread", thread_id: "original" }, revision: 7, mode: "edit", model: "gpt-6-astra", thinking: "high", schedule };
    const result = buildAutomationUpdatePayload({ ...scheduleFormValues(editing, context.timezone, now), title: "After" }, { ...context, editing });
    expect(result).toMatchObject({ name: "After", target: editing.target, mode: "edit", model: "gpt-6-astra", thinking: "high", expected_revision: 7, schedule });
  });

  it("changes an existing custom recurrence only when a new repeat is chosen", () => {
    const editing = { schedule: { kind: "rrule", rule: "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", start_at: "2026-01-01T12:00:00Z", timezone: "UTC" } };
    const fields = scheduleFormValues(editing);
    expect(fields.repeat).toBe("custom");
    expect(buildSchedule({ ...fields, repeat: "daily" }, { ...context, editing }).rule).toBe("RRULE:FREQ=DAILY;BYHOUR=12;BYMINUTE=0;BYSECOND=0");
  });
});

describe("Home Assistant wall-clock conversion", () => {
  it.each([
    ["2026-01-15", "09:00", "Europe/London", "2026-01-15T09:00:00.000Z"],
    ["2026-07-15", "09:00", "Europe/London", "2026-07-15T08:00:00.000Z"],
    ["2026-07-15", "09:00", "Asia/Kathmandu", "2026-07-15T03:15:00.000Z"],
    ["2026-07-15", "09:00", "America/New_York", "2026-07-15T13:00:00.000Z"],
    ["2026-10-25", "01:30", "Europe/London", "2026-10-25T00:30:00.000Z"],
  ])("resolves %s %s in %s independently of the browser", (date, time, zone, expected) => {
    expect(scheduleInstant(date, time, zone)).toBe(expected);
  });
  it("rejects a missing hour and invalid calendar dates", () => {
    expect(() => scheduleInstant("2026-03-29", "01:30", "Europe/London")).toThrow(/clocks change/);
    expect(() => scheduleInstant("2026-02-30", "09:00", "Europe/London")).toThrow(/valid date/);
    expect(() => scheduleInstant("2026-09-20", "25:00", "Europe/London")).toThrow(/valid date and time/);
  });
});
