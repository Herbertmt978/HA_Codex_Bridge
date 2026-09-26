import { describe, expect, it } from "vitest";
import { scheduleRunHistory } from "../src/schedule-run-history.js";

describe("schedule run history", () => {
  it.each([
    ["queued", "Queued"], ["running", "Running"], ["completed", "Completed"],
    ["failed", "Failed"], ["cancelled", "Cancelled"], ["blocked", "Needs attention"],
    ["interrupted_restart", "Interrupted"], ["skipped_overlap", "Skipped · already running"],
    ["skipped_capacity", "Skipped · at capacity"], ["skipped_misfire", "Skipped · missed window"],
    ["skipped_paused", "Skipped · paused"],
  ])("explains the public %s outcome without copying private fields", (status, label) => {
    const result = scheduleRunHistory([{ status, error: "private-token", prompt: "private-prompt", automation_run_id: "private-id" }]).rows[0];
    expect(result.status).toBe(label);
    expect(result.explanation).not.toBe("");
    expect(JSON.stringify(result)).not.toContain("private-");
    expect(result.started_at).toBe("—");
  });

  it("uses HA's zone independently of the browser, including clock changes", () => {
    const history = scheduleRunHistory([
      { due_at: "2026-09-25T08:00:00Z" },
      { due_at: "2026-10-25T00:30:00Z" },
      { due_at: "2026-10-25T01:30:00Z" },
    ], "Europe/London");
    expect(history.timezone).toBe("Europe/London");
    expect(history.rows[0].due_at).toBe("25 Sept 2026, 09:00 BST");
    expect(history.rows[1].due_at).toBe("25 Oct 2026, 01:30 BST");
    expect(history.rows[2].due_at).toBe("25 Oct 2026, 01:30 GMT");
  });

  it.each(["failed", "interrupted_restart"])("describes %s on managed and external deployments", (status) => {
    const explanation = scheduleRunHistory([{ status }]).rows[0].explanation;
    expect(explanation).not.toContain("App");
    if (status === "interrupted_restart") expect(explanation).not.toContain("restarted");
    else expect(explanation).toContain("Bridge");
  });

  it.each(["new_private_status", "__proto__", null, { toString: null }])("never displays or evaluates unknown status values", (status) => {
    const row = scheduleRunHistory([{ status }]).rows[0];
    expect(row.status).toBe("Status unavailable");
    expect(row.explanation).toContain("Refresh");
  });

  it.each(["private timestamp", "2026-09-25T09:00:00", "2026-02-30T00:00:00Z", {}, 1])("does not guess or echo malformed instants", (due_at) => {
    expect(scheduleRunHistory([{ due_at }]).rows[0].due_at).toBe("Unavailable");
  });

  it.each([undefined, null, "", "private-invalid-zone"])("marks unavailable HA timezone explicitly and formats a safe UTC fallback", (timezone) => {
    const history = scheduleRunHistory([{ due_at: "2026-09-25T08:00:00Z" }], timezone);
    expect(history.timezoneUnavailable).toBe(true);
    expect(history.timezone).toBe("UTC");
    expect(history.rows[0].due_at).toBe("25 Sept 2026, 08:00 UTC");
  });
});
