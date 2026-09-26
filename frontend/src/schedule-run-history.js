/** Public run outcomes have fixed copy; provider errors never become table text. */
const OUTCOMES = Object.freeze({
  queued: ["Queued", "Waiting for Codex to start this run.", "is-attention"],
  running: ["Running", "Codex is working on this task.", "is-attention"],
  completed: ["Completed", "This task finished. Its response is in the task's chat.", "is-positive"],
  failed: ["Failed", "This run could not finish. Check its chat and the App connection.", "is-negative"],
  cancelled: ["Cancelled", "This run was cancelled.", ""],
  blocked: ["Needs attention", "This run was blocked. Review its chat and permissions before trying again.", "is-attention"],
  interrupted_restart: ["Interrupted", "The App restarted before this run completed.", "is-attention"],
  skipped_overlap: ["Skipped · already running", "An earlier run of this task was still active. No second run started.", "is-attention"],
  skipped_capacity: ["Skipped · at capacity", "Codex had no capacity for this run. No work started.", "is-attention"],
  skipped_misfire: ["Skipped · missed window", "Home Assistant reached this occurrence after its allowed start window. No catch-up run started.", "is-attention"],
  skipped_paused: ["Skipped · paused", "The task was paused when this run was requested. No work started.", ""],
});
const UNKNOWN = ["Status unavailable", "Refresh the run history to check this run.", ""];
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/u;

function formatInstant(value, formatter) {
  if (value == null || value === "") return "—";
  if (typeof value !== "string" || !INSTANT.test(value)) return "Unavailable";
  const instant = new Date(value);
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(instant.getTime()) || !Number.isFinite(date.getTime())
    || date.toISOString().slice(0, 10) !== value.slice(0, 10)) return "Unavailable";
  return formatter.format(instant);
}

/** Display absolute run instants in HA's zone, never the browser's local zone. */
export function scheduleRunHistory(runs, timezone) {
  const options = { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short" };
  let formatter;
  let timezoneUnavailable = false;
  try {
    if (typeof timezone !== "string" || !timezone || timezone.length > 100) throw new RangeError();
    formatter = new Intl.DateTimeFormat("en-GB", { ...options, timeZone: timezone });
  } catch {
    timezoneUnavailable = true;
    formatter = new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" });
  }
  return {
    timezone: formatter.resolvedOptions().timeZone,
    timezoneUnavailable,
    rows: runs.map((run) => {
      const [status, explanation, tone] = typeof run.status === "string" && Object.hasOwn(OUTCOMES, run.status) ? OUTCOMES[run.status] : UNKNOWN;
      return { status, explanation, tone,
        due_at: formatInstant(run.due_at, formatter),
        started_at: formatInstant(run.started_at, formatter),
        completed_at: formatInstant(run.completed_at, formatter) };
    }),
  };
}
