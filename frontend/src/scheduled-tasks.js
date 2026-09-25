import { selection } from "./selection.js";
import { modelChoices, reasoningChoices } from "./model-choices.js";
import { HOST_MODE, HOST_LABEL } from "./host-access.js";

const WEEKDAYS = [
  ["MO", "Monday"], ["TU", "Tuesday"], ["WE", "Wednesday"],
  ["TH", "Thursday"], ["FR", "Friday"], ["SA", "Saturday"], ["SU", "Sunday"],
];
const REPEATS = [["daily", "Daily"], ["weekdays", "Weekdays"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["interval", "Every…"], ["once", "Once"]];

function localParts(instant, timezone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(instant)).map(({ type, value }) => [type, value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}`, second: parts.second };
}

/** Resolve wall time in HA's zone, never the browser's possibly different zone. */
export function scheduleInstantCandidates(date, time, timezone) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "") || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time || "")) {
    throw new Error("Choose a valid date and time.");
  }
  const wall = Date.parse(`${date}T${time}:00Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 10) !== date) throw new Error("Choose a valid date.");
  const candidates = new Set();
  // Both sides of a clock change are needed to detect gaps and repeated times.
  for (const hours of [-36, 0, 36]) {
    const sample = wall + hours * 3_600_000;
    const parts = localParts(sample, timezone);
    const offset = Date.parse(`${parts.date}T${parts.time}:${parts.second}Z`) - sample;
    const candidate = wall - offset;
    const resolved = localParts(candidate, timezone);
    if (resolved.date === date && resolved.time === time) candidates.add(candidate);
  }
  if (!candidates.size) throw new Error("That time does not exist when the clocks change. Choose another time.");
  return [...candidates].sort((a, b) => a - b).map((value) => new Date(value).toISOString());
}

export function scheduleInstant(date, time, timezone) {
  return scheduleInstantCandidates(date, time, timezone)[0];
}

export function scheduleFormValues(automation = {}, timezone = "UTC", now = Date.now()) {
  const schedule = automation.schedule || {};
  const zone = schedule.timezone || timezone;
  const local = localParts(schedule.at || schedule.start_at || schedule.anchor_at || now, zone);
  const date = new Date(`${local.date}T12:00:00Z`);
  const weekday = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"][date.getUTCDay()];
  const values = {
    title: automation.name || "", prompt: automation.prompt || "",
    repeat: automation.schedule ? schedule.kind === "rrule" ? "custom" : schedule.kind : "daily",
    date: local.date, time: automation.schedule ? local.time : "09:00", weekday,
    month_day: String(date.getUTCDate()), interval_count: "1", interval_unit: "hours",
    target_kind: automation.target?.kind || "standalone", mode: automation.mode || "observe",
    model: automation.model || "", thinking: automation.thinking || "", timezone: zone,
    notification_policy: automation.notifications?.policy || "off",
    notification_persistent: automation.notifications?.persistent === true,
    notification_preview: automation.notifications?.preview === true,
  };
  for (const target of automation.notifications?.mobile_targets || []) values[`mobile_target:${target}`] = true;
  if (schedule.kind === "interval") {
    const unit = schedule.seconds % 3600 === 0 ? "hours" : schedule.seconds % 60 === 0 ? "minutes" : "seconds";
    values.interval_unit = unit;
    values.interval_count = String(schedule.seconds / ({ hours: 3600, minutes: 60, seconds: 1 }[unit]));
  }
  if (schedule.kind === "rrule") {
    const rule = schedule.rule || "";
    // Only recognise the rules this editor can round-trip. Keep all other saved
    // rules intact, including COUNT, UNTIL, intervals and multiple run times.
    const fields = rule.startsWith("RRULE:") ? rule.slice(6).split(";").map((part) => part.split("=")) : [];
    const pairs = Object.fromEntries(fields);
    const allowed = new Set(["FREQ", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE", "BYSECOND"]);
    const simple = fields.length && fields.every(([key, value]) => allowed.has(key) && value) && new Set(fields.map(([key]) => key)).size === fields.length
      && (!pairs.BYHOUR || pairs.BYHOUR === String(Number(local.time.slice(0, 2))))
      && (!pairs.BYMINUTE || pairs.BYMINUTE === String(Number(local.time.slice(3))))
      && (!pairs.BYSECOND || pairs.BYSECOND === "0");
    if (simple) {
      if (pairs.FREQ === "DAILY" && !pairs.BYDAY && !pairs.BYMONTHDAY) values.repeat = "daily";
      if (pairs.FREQ === "WEEKLY" && !pairs.BYMONTHDAY) {
        if (pairs.BYDAY === "MO,TU,WE,TH,FR") values.repeat = "weekdays";
        else if (!pairs.BYDAY || WEEKDAYS.some(([day]) => day === pairs.BYDAY)) {
          values.repeat = "weekly";
          values.weekday = pairs.BYDAY || weekday;
        }
      }
      if (pairs.FREQ === "MONTHLY" && !pairs.BYDAY && (!pairs.BYMONTHDAY || /^(?:[1-9]|[12]\d|3[01])$/.test(pairs.BYMONTHDAY))) {
        values.repeat = "monthly";
        values.month_day = pairs.BYMONTHDAY || values.month_day;
      }
    }
  }
  return values;
}

const SCHEDULE_FIELDS = ["repeat", "date", "time", "weekday", "month_day", "interval_count", "interval_unit"];

export function buildSchedule(values, { editing = null, timezone = "UTC", now = Date.now() } = {}) {
  const zone = editing?.schedule?.timezone || timezone;
  if (editing?.schedule) {
    const original = scheduleFormValues(editing, zone, now);
    if (SCHEDULE_FIELDS.every((key) => String(values[key] ?? original[key]) === String(original[key]))) return { ...editing.schedule };
  }
  if (values.repeat === "custom") {
    if (!editing?.schedule) throw new Error("Choose a repeat frequency.");
    return { ...editing.schedule };
  }
  const at = scheduleInstant(values.date, values.time, zone);
  if (values.repeat === "once") {
    if (Date.parse(at) <= now) throw new Error("Choose a time in the future for a one-off task.");
    return { kind: "once", at };
  }
  if (values.repeat === "interval") {
    const count = Number(values.interval_count);
    const seconds = count * ({ hours: 3600, minutes: 60, seconds: 1 }[values.interval_unit] || 0);
    if (!Number.isInteger(count) || !Number.isInteger(seconds) || seconds < 60 || seconds > 31_536_000) throw new Error("Choose an interval from one minute to 365 days.");
    return { kind: "interval", seconds, anchor_at: at };
  }
  let rule;
  if (values.repeat === "daily") rule = "FREQ=DAILY";
  else if (values.repeat === "weekdays") rule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR";
  else if (values.repeat === "weekly" && WEEKDAYS.some(([day]) => day === values.weekday)) rule = `FREQ=WEEKLY;BYDAY=${values.weekday}`;
  else if (values.repeat === "monthly" && /^(?:[1-9]|[12]\d|3[01])$/.test(String(values.month_day))) rule = `FREQ=MONTHLY;BYMONTHDAY=${values.month_day}`;
  else throw new Error("Choose a repeat frequency and its day.");
  const [hour, minute] = values.time.split(":").map(Number);
  return { kind: "rrule", rule: `RRULE:${rule};BYHOUR=${hour};BYMINUTE=${minute};BYSECOND=0`, start_at: at, timezone: zone };
}

export function buildAutomationPayload(values = {}, context = {}) {
  const name = String(values.title || "").trim();
  const prompt = String(values.prompt || "").trim();
  if (!name || !prompt) throw new Error("Add a title and describe what Codex should do.");
  const { editing, projectId, threadId } = context;
  const kind = values.target_kind || "standalone";
  const target = editing?.target?.kind === kind ? { ...editing.target }
    : kind === "continue_thread" ? { kind, thread_id: threadId } : { kind: "standalone", project_id: projectId };
  if (!(target.thread_id || target.project_id)) throw new Error("Select a chat or workspace before creating a scheduled task.");
  const payload = { name, prompt, target, schedule: buildSchedule(values, context), mode: values.mode || "observe", model: values.model || null, thinking: values.thinking || null };
  if (context.notificationsSupported) {
    const policy = values.notification_policy || "off";
    const mobileTargets = (context.mobileTargets || []).filter((target) => values[`mobile_target:${target}`] === true);
    if (mobileTargets.length > 8) throw new Error("Choose no more than eight phone notification destinations.");
    const persistent = values.notification_persistent === true;
    if (policy !== "off" && !persistent && !mobileTargets.length) throw new Error("Choose a Home Assistant or phone notification destination.");
    payload.notifications = { policy, persistent, mobile_targets: mobileTargets, preview: mobileTargets.length > 0 && values.notification_preview === true };
  }
  if (payload.mode === HOST_MODE) {
    if (!context.hostAccessGrant || context.hostUnattendedApproved !== true) throw new Error("Review and acknowledge host access for this scheduled task.");
    payload.host_access_grant = context.hostAccessGrant;
    payload.host_unattended_approved = true;
  }
  return payload;
}

export function buildAutomationUpdatePayload(values = {}, context = {}) {
  if (!Number.isInteger(context.editing?.revision)) throw new Error("Reload this task before saving changes.");
  return { expected_revision: context.editing.revision, ...buildAutomationPayload(values, context) };
}

export function scheduleSummary(schedule, timezone = "UTC") {
  if (!schedule || typeof schedule !== "object") return "Not scheduled";
  const values = scheduleFormValues({ schedule }, timezone);
  const zone = values.timezone;
  if (values.repeat === "custom") return `Custom schedule · ${zone}`;
  if (values.repeat === "once") return new Intl.DateTimeFormat("en-GB", { timeZone: zone, dateStyle: "medium", timeStyle: "short" }).format(new Date(schedule.at)) + ` · ${zone}`;
  if (values.repeat === "interval") return `Every ${values.interval_count} ${values.interval_unit} · ${zone}`;
  const repeat = values.repeat === "weekly" ? `Every ${WEEKDAYS.find(([day]) => day === values.weekday)?.[1]}`
    : values.repeat === "monthly" ? `Monthly on day ${values.month_day}`
      : values.repeat === "weekdays" ? "Every weekday" : "Daily";
  return `${repeat} at ${values.time} · ${zone}`;
}

function element(doc, tag, className, value) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function field(doc, name, label, value, options = null, type = "text") {
  const row = element(doc, options ? "div" : "label", "schedule-row");
  row.append(element(doc, "span", "schedule-row-label", label));
  if (options) {
    const picker = selection(doc, { name, label, value, options });
    picker.querySelector("select").dataset.desktopField = name;
    row.append(picker);
    return row;
  }
  const control = element(doc, type === "textarea" ? "textarea" : "input");
  control.name = name;
  control.dataset.desktopField = name;
  control.setAttribute("aria-label", label);
  if (type !== "textarea") control.type = type;
  control.value = String(value ?? "");
  row.append(control);
  return row;
}

function staticRow(doc, label, value) {
  const row = element(doc, "div", "schedule-row");
  row.append(element(doc, "span", "schedule-row-label", label), element(doc, "span", "schedule-row-value", value));
  return row;
}

function checkRow(doc, name, label, checked) {
  const row = element(doc, "label", "schedule-row schedule-check-row");
  row.append(element(doc, "span", "schedule-row-label", label));
  const control = element(doc, "input");
  control.type = "checkbox"; control.name = name; control.dataset.desktopField = name;
  control.setAttribute("aria-label", label); control.checked = checked === true;
  row.append(control);
  return row;
}

export function refreshScheduleForm(form) {
  const values = Object.fromEntries([...form.querySelectorAll("[data-desktop-field]")].map((control) => [control.name, control.value]));
  for (const row of form.querySelectorAll("[data-repeat-for]")) {
    row.hidden = !row.dataset.repeatFor.split(" ").includes(values.repeat);
    for (const control of row.querySelectorAll("input,select")) control.required = !row.hidden;
  }
  const custom = values.repeat === "custom";
  const time = form.querySelector('[name="time"]');
  time.closest("label").hidden = custom;
  const preview = form.querySelector(".schedule-preview");
  const context = { timezone: form.dataset.timezone };
  try {
    preview.textContent = custom ? "The saved custom timing will be kept. Choose another repeat option to replace it."
      : scheduleSummary(buildSchedule(values, context), context.timezone);
  } catch (error) { preview.textContent = error.message; }
  const monthly = form.querySelector(".schedule-month-note");
  monthly.hidden = values.repeat !== "monthly" || Number(values.month_day) < 29;
  const previewChoice = form.querySelector('[name="notification_preview"]');
  if (previewChoice) {
    const hasPhone = [...form.querySelectorAll('input[name^="mobile_target:"]')].some((control) => control.checked);
    previewChoice.disabled = !hasPhone;
    if (!hasPhone) previewChoice.checked = false;
  }
}

export function renderScheduleForm(doc, state, timezone, context = {}) {
  const editing = state.editingAutomation;
  const initial = scheduleFormValues(editing || {}, timezone);
  const values = { ...initial, ...(state.formDraft || {}) };
  const form = element(doc, "form", "schedule-editor");
  form.dataset.desktopForm = "schedule";
  form.dataset.timezone = initial.timezone;
  const header = element(doc, "div", "schedule-editor-header");
  header.append(element(doc, "span", "", editing ? "Edit scheduled task" : "New scheduled task"));
  const close = element(doc, "button", "schedule-close", "×");
  close.type = "button"; close.dataset.desktopAction = "close-form"; close.setAttribute("aria-label", "Close scheduled task"); header.append(close); form.append(header);
  const title = field(doc, "title", "Scheduled task title", values.title);
  title.className = "schedule-title";
  const titleControl = title.querySelector("input"); titleControl.placeholder = "Scheduled task title"; titleControl.required = true; titleControl.maxLength = 160;
  const prompt = field(doc, "prompt", "Task instructions", values.prompt, null, "textarea");
  prompt.className = "schedule-prompt";
  const promptControl = prompt.querySelector("textarea"); promptControl.placeholder = "Describe what Codex should do"; promptControl.required = true; promptControl.rows = 3;
  form.append(title, prompt);
  const addGroup = (label) => {
    const group = element(doc, "fieldset", "schedule-group"); group.append(element(doc, "legend", "", label));
    const card = element(doc, "div", "schedule-card"); group.append(card); form.append(group); return card;
  };
  const details = addGroup("Details");
  details.append(staticRow(doc, "Runs on", "Home Assistant"));
  const savedThread = editing?.target?.kind === "continue_thread";
  details.append(field(doc, "target_kind", "Runs in", values.target_kind, [
    ["standalone", context.projectName ? `New chat · ${context.projectName}` : "New chat for this task", !context.projectId && editing?.target?.kind !== "standalone"],
    ["continue_thread", savedThread ? "Original chat for this task" : "Current chat", !context.threadId && !savedThread],
  ]));
  if (context.assistChatSelected && !savedThread) details.append(element(doc, "p", "desktop-note", "Assist conversations cannot run scheduled tasks. Choose a new chat for this task or open a regular chat."));
  const frequency = addGroup("Frequency");
  const repeats = initial.repeat === "custom" ? [...REPEATS, ["custom", "Keep custom schedule"]] : REPEATS;
  frequency.append(field(doc, "repeat", "Repeat", values.repeat, repeats));
  const conditional = (row, repeatsFor) => { row.dataset.repeatFor = repeatsFor; frequency.append(row); };
  conditional(field(doc, "weekday", "Day", values.weekday, WEEKDAYS), "weekly");
  const month = field(doc, "month_day", "Day of month", values.month_day, null, "number");
  month.querySelector("input").min = "1"; month.querySelector("input").max = "31"; conditional(month, "monthly");
  const interval = field(doc, "interval_count", "Every", values.interval_count, null, "number");
  interval.querySelector("input").min = "1"; interval.querySelector("input").max = "31536000"; conditional(interval, "interval");
  conditional(field(doc, "interval_unit", "Unit", values.interval_unit, [["minutes", "Minutes"], ["hours", "Hours"], ["seconds", "Seconds"]]), "interval");
  conditional(field(doc, "date", "Date / starts on", values.date, null, "date"), "once interval");
  frequency.append(field(doc, "time", "Time", values.time, null, "time"));
  frequency.append(staticRow(doc, "Results", "Chat and run history"));
  if (context.notificationsSupported) {
    const notifications = addGroup("Notifications");
    notifications.append(field(doc, "notification_policy", "When to notify", values.notification_policy, [
      ["off", "Off"], ["attention", "Needs attention or failed"], ["all", "All outcomes"],
    ]));
    notifications.append(checkRow(doc, "notification_persistent", "Home Assistant notification (visible to all HA users)", values.notification_persistent));
    for (const target of context.mobileTargets || []) {
      const display = target.replace(/^mobile_app_/, "").replaceAll("_", " ");
      const available = context.mobileAvailable?.includes(target);
      const label = `Phone · ${display}${available ? "" : " (unavailable)"}`;
      const row = checkRow(doc, `mobile_target:${target}`, label, values[`mobile_target:${target}`]);
      if (!available && !editing?.notifications?.mobile_targets?.includes(target)) row.querySelector("input").disabled = true;
      notifications.append(row);
    }
    if (!context.mobileTargets?.length) notifications.append(element(doc, "p", "desktop-note", "No Companion App phone notification services are available."));
    notifications.append(checkRow(doc, "notification_preview", "Include a brief answer preview on selected phones", values.notification_preview));
    notifications.append(element(doc, "p", "desktop-note", "Home Assistant notifications contain a generic update only. Phone notices go only to selected devices. Chat links require an administrator sign-in."));
  }
  form.append(element(doc, "p", "schedule-preview"));
  if (context.proposalsSupported) {
    const nextRuns = element(doc, "p", "schedule-next-runs", state.nextRuns?.length
      ? `Next runs: ${state.nextRuns.join(" · ")}` : "Checking the next run times…");
    nextRuns.setAttribute("aria-live", "polite");
    form.append(nextRuns);
  }
  form.append(element(doc, "p", "schedule-month-note", "Months without this date are skipped."));
  const advanced = element(doc, "details", "schedule-advanced");
  advanced.append(element(doc, "summary", "", "Advanced"));
  const advancedCard = element(doc, "div", "schedule-card");
  const modes = [["observe", "Observe"], ["edit", "Edit workspace"], ["full-auto", "Full auto · workspace"]];
  if (context.hostAccessSupported) modes.push([HOST_MODE, HOST_LABEL]);
  advancedCard.append(field(doc, "mode", "Permissions", values.mode, modes));
  if (values.mode === HOST_MODE) advancedCard.append(element(doc, "p", "desktop-note", "This task has host root access, including files, credentials, services and the network."));
  const modelContext = () => ({ ...context, defaultModel: form.querySelector('[name="target_kind"]').value === "continue_thread" ? context.threadModel || context.defaultModel : context.defaultModel });
  const model = field(doc, "model", "Model", values.model, modelChoices(modelContext(), values.model));
  const thinking = field(doc, "thinking", "Reasoning", values.thinking, reasoningChoices(modelContext(), values.model, values.thinking));
  model.querySelector("select").addEventListener("change", () => {
    const choices = reasoningChoices(modelContext(), model.querySelector("select").value);
    const selected = thinking.querySelector("select").value;
    const supported = choices.some(([key]) => key === selected) ? selected : "";
    thinking.querySelector(".panel-selection").setOptions(choices, supported);
    thinking.querySelector("select").dispatchEvent(new doc.defaultView.Event("change", { bubbles: true }));
  });
  form.querySelector('[name="target_kind"]').addEventListener("change", () => {
    const selectedModel = model.querySelector("select").value;
    const selectedThinking = thinking.querySelector("select").value;
    model.querySelector(".panel-selection").setOptions(modelChoices(modelContext(), selectedModel), selectedModel);
    thinking.querySelector(".panel-selection").setOptions(reasoningChoices(modelContext(), selectedModel, selectedThinking), selectedThinking);
  });
  advancedCard.append(model, thinking);
  advanced.append(advancedCard, element(doc, "p", "desktop-note", "Unattended tasks cannot answer approval requests. Observe is the default.")); form.append(advanced);
  const error = element(doc, "p", "schedule-error", state.formError || ""); error.setAttribute("role", "alert"); form.append(error);
  const actions = element(doc, "div", "schedule-actions");
  const cancel = element(doc, "button", "", "Cancel"); cancel.type = "button"; cancel.dataset.desktopAction = "close-form";
  const submit = element(doc, "button", "schedule-submit", editing ? "Save changes" : "Create task"); submit.type = "button"; submit.dataset.desktopAction = editing ? "submit-schedule-update" : "submit-schedule";
  actions.append(cancel, submit); form.append(actions); refreshScheduleForm(form);
  return form;
}
