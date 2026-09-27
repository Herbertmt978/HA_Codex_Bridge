import { assistantMarkdownMaxLength } from "./markdown.js";

const PLAN_ITEM_TYPE = "plan";

function stringId(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function itemKey(runId, itemId) {
  return `${runId.length}:${runId}${itemId}`;
}

function eventIdentity(event, index) {
  if (Number.isSafeInteger(event?.sequence)) return `sequence:${event.sequence}`;
  if (typeof event?.cursor === "string" || Number.isSafeInteger(event?.cursor)) return `cursor:${event.cursor}`;
  return `index:${index}`;
}

function orderedEvents(events) {
  if (!Array.isArray(events)) return [];
  const ordered = events.map((event, index) => ({ event, index }));
  if (ordered.every(({ event }) => Number.isSafeInteger(event?.sequence))
    && ordered.some(({ event }, index) => index > 0 && event.sequence < ordered[index - 1].event.sequence)) {
    ordered.sort((left, right) => left.event.sequence - right.event.sequence || left.index - right.index);
  }

  return ordered.map(({ event }) => event);
}

function uniqueEvents(ordered) {
  const seen = new Set();
  return ordered.filter((event, index) => {
    const identity = eventIdentity(event, index);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function projectPlanContent(events) {
  const states = new Map();
  const completed = new Map();
  const endedRuns = new Set();
  const ordered = orderedEvents(events);

  for (const event of uniqueEvents(ordered)) {
    const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
    const runId = stringId(payload.run_id);
    const itemId = stringId(payload.item_id);
    const key = runId && itemId ? itemKey(runId, itemId) : null;

    if (["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event?.event_type) && runId) {
      endedRuns.add(runId);
    }
    if (event?.event_type === "item.started" && payload.item_type === PLAN_ITEM_TYPE && key) {
      states.set(key, { runId, itemId, text: "" });
      endedRuns.delete(runId);
      continue;
    }
    if (event?.event_type === "plan.delta" && key && typeof payload.delta === "string") {
      const state = states.get(key);
      if (state && !endedRuns.has(runId) && state.text.length < assistantMarkdownMaxLength) {
        state.text += payload.delta.slice(0, assistantMarkdownMaxLength - state.text.length);
      }
      continue;
    }
    if (event?.event_type === "item.completed" && payload.item_type === PLAN_ITEM_TYPE && key) {
      const state = states.get(key);
      states.delete(key);
      if (state && !endedRuns.has(runId) && state.text.length > 0) completed.set(event, {
        run_id: runId,
        item_id: itemId,
        role: "assistant",
        text: state.text,
        plan: true,
      });
    }
  }

  return { ordered, completed, states, endedRuns };
}

/** Add transcript messages for native Plan items acknowledged as completed. */
export function projectCompletedPlanMessages(events = []) {
  const { ordered, completed } = projectPlanContent(events);
  const projected = [];
  for (const event of ordered) {
    projected.push(event);
    const payload = completed.get(event);
    if (payload) projected.push({ ...event, event_type: "message.completed", payload });
  }
  return projected;
}

/** Read text for one currently active native Plan item without projecting it as a response. */
export function activePlanText(events = [], runId, itemId) {
  const wantedRunId = stringId(runId);
  const wantedItemId = stringId(itemId);
  if (!wantedRunId || !wantedItemId) return "";
  const { states, endedRuns } = projectPlanContent(events);
  const key = itemKey(wantedRunId, wantedItemId);
  const state = states.get(key);
  return state && !endedRuns.has(wantedRunId) ? state.text : "";
}
