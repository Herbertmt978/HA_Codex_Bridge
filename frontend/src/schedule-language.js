import { buildSchedule, scheduleFormValues, scheduleInstantCandidates } from "./scheduled-tasks.js";

const DAYS = { monday: "MO", tuesday: "TU", wednesday: "WE", thursday: "TH", friday: "FR", saturday: "SA", sunday: "SU" };
const MONTHS = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 };
const TIME = "(?<hour>\\d{1,2})(?::(?<minute>[0-5]\\d))?\\s*(?<meridiem>am|pm)?";

function localDate(now, timezone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(now)).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function addDays(date, days) {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function parseTime(match) {
  const hour = Number(match.groups.hour);
  const minute = Number(match.groups.minute || 0);
  const meridiem = match.groups.meridiem?.toLowerCase();
  if (!meridiem && !match.groups.minute) throw new Error("Please give the time as 09:00 or 9 am so morning and evening are clear.");
  if (meridiem && (hour < 1 || hour > 12)) throw new Error("Choose a valid 12-hour time.");
  if (!meridiem && hour > 23) throw new Error("Choose a valid 24-hour time.");
  const resolved = meridiem ? hour % 12 + (meridiem === "pm" ? 12 : 0) : hour;
  return `${String(resolved).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function parseDate(raw, now, timezone) {
  const value = raw.toLowerCase().trim();
  if (value === "tomorrow") return addDays(localDate(now, timezone), 1);
  if (/^\d{4}-\d{2}-\d{2}$/u.test(value)) return value;
  if (/^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$/u.test(value)) {
    throw new Error("Use a date such as 24 September 2026 or 2026-09-24; numeric day/month order is ambiguous.");
  }
  const named = /^(?<day>\d{1,2})(?:st|nd|rd|th)?\s+(?<month>[a-z]+)\s+(?<year>\d{4})$/u.exec(value);
  const month = MONTHS[named?.groups.month];
  if (!named || !month) throw new Error("Include a clear date, for example 24 September 2026.");
  return `${named.groups.year}-${String(month).padStart(2, "0")}-${String(named.groups.day).padStart(2, "0")}`;
}

function taskText(value) {
  return value.trim().replace(/^(?:please\s+)?(?:ask codex to|remind me to|schedule(?: a task to)?)\s+/iu, "").replace(/[.!\s]+$/u, "").trim();
}

/** Interpret a bounded English scheduling request. It only prepares the existing editor. */
export function proposeScheduleDescription(description, { timezone = "UTC", now = Date.now() } = {}) {
  const input = String(description || "").trim();
  if (!input || input.length > 4000) throw new Error("Describe the task and when it should run, in 4,000 characters or fewer.");
  const separator = input.match(/(?:[,;]\s*|:\s+)/u);
  const trailing = separator ? null : new RegExp(`^(.+?)\\s+((?:daily|every day|every weekdays?|every (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\\s+at\\s+${TIME})$`, "iu").exec(input);
  if (!separator && !trailing) throw new Error("Include clear timing and instructions. For example: Every weekday at 9 am, summarise yesterday's events.");
  const when = (separator ? input.slice(0, separator.index) : trailing[2]).replace(/^please\s+/iu, "").trim();
  const prompt = taskText(separator ? input.slice(separator.index + separator[0].length) : trailing[1]);
  if (!prompt) throw new Error("Add what Codex should do after the timing.");
  const values = scheduleFormValues({}, timezone, now);
  const timePattern = new RegExp(`^(.+?)\\s+at\\s+${TIME}$`, "iu");
  const chatDestination = /\s+in (?:this|the current) chat$/iu.test(when);
  const match = timePattern.exec(when.replace(/\s+in (?:this|the current) chat$/iu, ""));
  if (!match) throw new Error("Include a clear time, for example 09:00 or 9 am, before the comma.");
  const rule = match[1].toLowerCase().trim();
  values.time = parseTime(match);
  values.prompt = prompt;
  values.title = prompt.length > 80 ? `${prompt.slice(0, 77).trimEnd()}…` : prompt;
  if (chatDestination) values.target_kind = "continue_thread";
  if (/^(?:daily|every day)$/u.test(rule)) values.repeat = "daily";
  else if (/^every weekdays?$/u.test(rule)) values.repeat = "weekdays";
  else {
    const day = /^(?:every|weekly on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$/u.exec(rule);
    const month = /^monthly on (?:the )?(\d{1,2})(?:st|nd|rd|th)?$/u.exec(rule);
    const interval = /^every (\d+) (minutes?|hours?) starting (.+)$/u.exec(rule);
    const once = /^(?:once\s+)?(?:on\s+)?(.+)$/u.exec(rule);
    if (day) { values.repeat = "weekly"; values.weekday = DAYS[day[1]]; }
    else if (month) { values.repeat = "monthly"; values.month_day = month[1]; }
    else if (interval) {
      values.repeat = "interval";
      values.interval_count = interval[1];
      values.interval_unit = interval[2].startsWith("hour") ? "hours" : "minutes";
      values.date = parseDate(interval[3], now, timezone);
    } else if (once && (rule.startsWith("on ") || rule.startsWith("once ") || rule === "tomorrow" || /^\d{4}-/u.test(rule))) {
      values.repeat = "once";
      values.date = parseDate(once[1], now, timezone);
    } else throw new Error("Choose daily, weekdays, a named weekday, monthly on a day, an interval with a start date, or a one-off date.");
  }
  if (["once", "interval"].includes(values.repeat) && scheduleInstantCandidates(values.date, values.time, timezone).length > 1) {
    throw new Error("That time occurs twice when the clocks change. Choose another time or use the task editor to review it.");
  }
  try { buildSchedule(values, { timezone, now }); }
  catch (error) { throw new Error(error.message, { cause: error }); }
  return values;
}
