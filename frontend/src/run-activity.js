const MAX_ACTION_TEXT = 240;
const MAX_HISTORY_ITEMS = 8;
const MAX_EVENTS = 2000;
const MAX_PLAN_STEPS = 128;
const MAX_PATCH_CHANGES = 2048;

const BUSY_STATUSES = new Set(["starting", "running", "cancelling", "in_progress", "inprogress"]);
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);

const ITEM_LABELS = Object.freeze({
  agentMessage: "Preparing a response",
  commandExecution: "Running a command",
  contextCompaction: "Compacting context",
  collabAgentToolCall: "Delegating to an agent",
  dynamicToolCall: "Calling a tool",
  fileChange: "Applying file changes",
  imageGeneration: "Generating an image",
  imageView: "Viewing an image",
  mcpToolCall: "Calling an MCP tool",
  plan: "Planning the work",
  reasoning: "Thinking through the request",
  sleep: "Waiting",
  subAgentActivity: "Working with a sub-agent",
  webSearch: "Searching the web",
});

const COMMAND_ACTION_LABELS = Object.freeze({
  read: "Reading files",
  listFiles: "Listing files",
  search: "Searching files",
});

const WEB_ACTION_LABELS = Object.freeze({
  search: "Searching the web",
  openPage: "Opening a web page",
  findInPage: "Finding text in a page",
  other: "Using web search",
});

const FILE_CHANGE_LABELS = Object.freeze({
  add: "Adding files",
  update: "Updating files",
  delete: "Deleting files",
});

const COLLAB_OPERATION_LABELS = Object.freeze({
  spawnAgent: "Starting a sub-agent",
  sendInput: "Steering a sub-agent",
  resumeAgent: "Resuming a sub-agent",
  wait: "Waiting for sub-agents",
  closeAgent: "Closing a sub-agent",
});

const SUBAGENT_ACTIVITY_LABELS = Object.freeze({
  started: "Sub-agent started",
  interacted: "Sub-agent active",
  interrupted: "Sub-agent interrupted",
});

const AGENT_STATE_KEYS = Object.freeze([
  "pendingInit",
  "running",
  "interrupted",
  "completed",
  "errored",
  "shutdown",
  "notFound",
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeText(value, maximum = MAX_ACTION_TEXT) {
  if (typeof value !== "string" || maximum < 1) {
    return "";
  }
  const withoutControls = [...value]
    .map((character) => {
      const codePoint = character.codePointAt(0);
      return codePoint >= 32 && codePoint !== 127 ? character : " ";
    })
    .join("");
  const normalized = withoutControls
    .replace(/\s+/gu, " ")
    .trim();
  return normalized.length > maximum ? `${normalized.slice(0, maximum - 1)}…` : normalized;
}

function safeChunk(value, maximum = 320) {
  if (typeof value !== "string" || maximum < 1) return "";
  const normalized = [...value]
    .map((character) => {
      const codePoint = character.codePointAt(0);
      return codePoint >= 32 && codePoint !== 127 ? character : " ";
    })
    .join("");
  return normalized.length > maximum ? normalized.slice(0, maximum) : normalized;
}

function eventPayload(event) {
  return isRecord(event?.payload) ? event.payload : {};
}

function normalizeEvents(events) {
  if (!Array.isArray(events)) {
    return [];
  }
  return events
    .filter((event) => (
      isRecord(event)
      && Number.isSafeInteger(event.sequence)
      && event.sequence > 0
      && typeof event.event_type === "string"
      && event.event_type.length <= 120
      && isRecord(event.payload)
    ))
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-MAX_EVENTS);
}

function eventRunId(event) {
  const runId = eventPayload(event).run_id;
  return typeof runId === "string" && /^[A-Za-z0-9_.:-]{1,256}$/u.test(runId) ? runId : "";
}

function latestEventRunId(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const runId = eventRunId(events[index]);
    if (runId) return runId;
  }
  return "";
}

function eventBelongsToRun(event, runId) {
  return !runId || !eventRunId(event) || eventRunId(event) === runId;
}

function currentPlan(events, runId) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type !== "plan.updated" || !eventBelongsToRun(event, runId)) {
      continue;
    }
    const plan = eventPayload(event).plan;
    if (!Array.isArray(plan)) {
      continue;
    }
    const steps = plan.slice(0, MAX_PLAN_STEPS).map((item) => {
      if (!isRecord(item)) {
        return null;
      }
      const label = safeText(item.step);
      const status = item.status === "inProgress" || item.status === "in_progress"
        ? "inProgress"
        : item.status === "completed" ? "completed" : item.status === "pending" ? "pending" : "";
      return label && status ? { label, status } : null;
    }).filter(Boolean);
    const activeIndex = steps.findIndex((step) => step.status === "inProgress");
    const pendingIndex = steps.findIndex((step) => step.status === "pending");
    const completedCount = steps.filter((step) => step.status === "completed").length;
    const displayIndex = activeIndex >= 0
      ? activeIndex
      : pendingIndex >= 0
        ? pendingIndex
        : steps.length ? Math.min(completedCount, steps.length - 1) : null;
    return {
      steps,
      activeIndex: displayIndex,
      completedCount,
      sourceSequence: event.sequence,
    };
  }
  return { steps: [], activeIndex: null, sourceSequence: 0 };
}

function joinActivityLabels(labels, fallback) {
  const unique = [...new Set(labels)].filter(Boolean);
  if (!unique.length) return fallback;
  if (unique.length === 1) return unique[0];
  if (unique.length === 2) return `${unique[0]} and ${unique[1].toLowerCase()}`;
  return `${unique[0]}, ${unique[1].toLowerCase()}, and more`;
}

function itemLabel(payload = {}) {
  const itemType = payload.item_type;
  if (itemType === "collabAgentToolCall" && Object.hasOwn(COLLAB_OPERATION_LABELS, payload.operation)) {
    return COLLAB_OPERATION_LABELS[payload.operation];
  }
  if (itemType === "subAgentActivity" && Object.hasOwn(SUBAGENT_ACTIVITY_LABELS, payload.kind)) {
    return SUBAGENT_ACTIVITY_LABELS[payload.kind];
  }
  if (itemType === "commandExecution" && Array.isArray(payload.action_types)) {
    return joinActivityLabels(
      payload.action_types.slice(0, 3).filter((type) => Object.hasOwn(COMMAND_ACTION_LABELS, type)).map((type) => COMMAND_ACTION_LABELS[type]),
      ITEM_LABELS[itemType],
    );
  }
  if (itemType === "webSearch" && Object.hasOwn(WEB_ACTION_LABELS, payload.action_type)) {
    return WEB_ACTION_LABELS[payload.action_type];
  }
  if (itemType === "fileChange" && Array.isArray(payload.change_kinds)) {
    return joinActivityLabels(
      payload.change_kinds.slice(0, 3).filter((kind) => Object.hasOwn(FILE_CHANGE_LABELS, kind)).map((kind) => FILE_CHANGE_LABELS[kind]),
      ITEM_LABELS[itemType],
    );
  }
  return typeof itemType === "string" && Object.hasOwn(ITEM_LABELS, itemType)
    ? ITEM_LABELS[itemType]
    : "Working on the request";
}

function safeAgentStateCounts(value) {
  if (!isRecord(value)) return null;
  const counts = {};
  let total = 0;
  for (const state of AGENT_STATE_KEYS) {
    const count = value[state];
    if (!Number.isSafeInteger(count) || count < 1) continue;
    counts[state] = Math.min(count, 1000000);
    total += counts[state];
  }
  if (!total) return null;
  const active = (counts.pendingInit || 0) + (counts.running || 0);
  const completed = (counts.completed || 0) + (counts.shutdown || 0);
  const attention = (counts.interrupted || 0) + (counts.errored || 0) + (counts.notFound || 0);
  const labels = [];
  if (active) labels.push(`${active} active`);
  if (completed) labels.push(`${completed} complete`);
  if (attention) labels.push(`${attention} need${attention === 1 ? "s" : ""} attention`);
  return {
    counts,
    total,
    active,
    completed,
    attention,
    label: labels.join(" · "),
  };
}

function clearTerminalSubagentActivity(snapshot) {
  if (!snapshot?.active) return snapshot;
  const counts = { ...snapshot.counts };
  delete counts.pendingInit;
  delete counts.running;
  const labels = [];
  if (snapshot.completed) labels.push(`${snapshot.completed} complete`);
  if (snapshot.attention) labels.push(`${snapshot.attention} need${snapshot.attention === 1 ? "s" : ""} attention`);
  return {
    ...snapshot,
    counts,
    total: snapshot.completed + snapshot.attention,
    active: 0,
    label: labels.join(" \u00b7 "),
  };
}

function latestSubagentSnapshot(events, runId) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (runId ? eventRunId(event) !== runId : !eventBelongsToRun(event, runId)) continue;
    const payload = eventPayload(event);
    if (payload.item_type !== "collabAgentToolCall") continue;
    const snapshot = safeAgentStateCounts(payload.agent_state_counts);
    if (snapshot) return { ...snapshot, sourceSequence: event.sequence };
  }
  return null;
}

function latestItemStart(events, runId) {
  const completedItemIds = new Set();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const payload = eventPayload(event);
    if (!eventBelongsToRun(event, runId)) continue;
    if (event.event_type === "item.completed" && typeof payload.item_id === "string") {
      completedItemIds.add(payload.item_id);
      continue;
    }
    if (event.event_type !== "item.started") continue;
    const type = payload.item_type;
    if (
      typeof type === "string"
      && Object.hasOwn(ITEM_LABELS, type)
      && !(typeof payload.item_id === "string" && completedItemIds.has(payload.item_id))
    ) {
      return { event, label: itemLabel(payload), itemType: type };
    }
  }
  return null;
}

function diffLineCounts(diff) {
  if (typeof diff !== "string") return { additions: 0, deletions: 0 };
  let additions = 0;
  let deletions = 0;
  for (const line of diff.slice(0, 262144).split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) additions += 1;
    if (line.startsWith("-")) deletions += 1;
  }
  return { additions, deletions };
}

function patchCounts(events, runId) {
  const files = new Map();
  let seen = 0;
  for (const event of events) {
    if (event.event_type !== "patch.updated" || !eventBelongsToRun(event, runId)) {
      continue;
    }
    const changes = eventPayload(event).changes;
    if (!Array.isArray(changes)) {
      continue;
    }
    for (const change of changes) {
      if (seen >= MAX_PATCH_CHANGES || !isRecord(change)) {
        break;
      }
      seen += 1;
      const path = safeText(change.path, 512);
      if (!path) {
        continue;
      }
      const kind = isRecord(change.kind) ? change.kind.type : change.kind;
      files.set(path, {
        kind: kind === "add" || kind === "delete" || kind === "update" ? kind : "update",
        ...diffLineCounts(change.diff),
      });
    }
  }
  let additions = 0;
  let deletions = 0;
  for (const file of files.values()) {
    additions += file.additions;
    deletions += file.deletions;
  }
  return { changed: files.size, additions, deletions };
}

function runEventState(events, runId) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!eventBelongsToRun(event, runId)) continue;
    if (event.event_type === "run.completed") return "completed";
    if (event.event_type === "run.failed") return "failed";
    if (event.event_type === "run.cancelled") return "cancelled";
    if (event.event_type === "run.interrupted") return "interrupted";
    if (event.event_type === "run.started" || event.event_type === "run.dequeued") return "running";
    if (event.event_type === "run.queued") return "queued";
  }
  return "";
}

function terminalEventState(eventType) {
  if (typeof eventType !== "string" || !eventType.startsWith("run.")) return "";
  const state = eventType.slice(4);
  return TERMINAL_STATES.has(state) ? state : "";
}

function latestTerminalRunId(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!terminalEventState(event.event_type)) continue;
    const runId = eventRunId(event);
    if (runId) return runId;
  }
  return "";
}

function latestLiveEventRunId(events) {
  const runId = latestEventRunId(events);
  if (!runId) return "";
  return TERMINAL_STATES.has(runEventState(events, runId)) ? "" : runId;
}

function assistantState(events, runId) {
  let latest = null;
  for (const event of events) {
    if ((event.event_type === "message.delta" || event.event_type === "message.completed") && eventBelongsToRun(event, runId)) {
      latest = event;
    }
  }
  return latest?.event_type === "message.delta"
    ? "streaming"
    : latest?.event_type === "message.completed" ? "complete" : "idle";
}

function reasoningSummary(events, runId) {
  let latest = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type === "reasoning.summary_delta" && eventBelongsToRun(event, runId)) {
      latest = event;
      break;
    }
  }
  if (!latest) return "";
  const latestPayload = eventPayload(latest);
  const itemId = latestPayload.item_id;
  const summaryIndex = latestPayload.summary_index;
  let joined = "";
  for (const event of events) {
    if (event.event_type !== "reasoning.summary_delta" || !eventBelongsToRun(event, runId)) continue;
    const payload = eventPayload(event);
    if (payload.item_id !== itemId || payload.summary_index !== summaryIndex) continue;
    const chunk = safeChunk(payload.delta || payload.text, 320);
    if (chunk) joined += chunk;
    if (joined.length >= 2000) break;
  }
  return safeText(joined, 2000);
}

function addHistory(history, seen, label, kind) {
  const text = safeText(label);
  if (!text || seen.has(`${kind}:${text}`)) return;
  seen.add(`${kind}:${text}`);
  history.push({ label: text, kind });
  if (history.length > MAX_HISTORY_ITEMS) history.shift();
}

/** Project a thread snapshot and its untrusted event history into safe activity UI data. */
export function getRunActivityViewModel(thread = {}, events = []) {
  const normalizedEvents = normalizeEvents(events);
  const threadStatus = safeText(thread?.status, 40).toLowerCase();
  const activeRunId = typeof thread?.active_run_id === "string"
    && /^[A-Za-z0-9_.:-]{1,256}$/u.test(thread.active_run_id)
    ? thread.active_run_id
    : "";
  const threadIsBusy = BUSY_STATUSES.has(threadStatus);
  const threadHasLiveProjection = threadIsBusy || threadStatus === "queued";
  const hasThreadStatus = Boolean(threadStatus);
  // A persisted idle snapshot is authoritative. Do not let an orphaned
  // active_run_id or an unfinished historical delta resurrect a run in the UI.
  // For an idle thread, retain only the most recent run with a terminal event
  // so its completed/failed metrics remain visible.
  const runId = threadHasLiveProjection
    ? activeRunId || latestLiveEventRunId(normalizedEvents)
    : hasThreadStatus
      ? latestTerminalRunId(normalizedEvents)
      : activeRunId || latestEventRunId(normalizedEvents);
  const scopedEvents = runId
    ? normalizedEvents.filter((event) => eventBelongsToRun(event, runId))
    : normalizedEvents.filter((event) => !eventRunId(event) && terminalEventState(event.event_type));
  const eventState = runEventState(scopedEvents, runId);
  let state = "idle";
  if (threadStatus === "queued") state = "queued";
  else if (threadStatus === "error") state = "failed";
  else if (threadIsBusy && !activeRunId) state = "running";
  else if (TERMINAL_STATES.has(eventState)) state = eventState;
  else if (threadIsBusy) state = "running";
  else if (eventState === "queued") state = "queued";
  else if (!hasThreadStatus && (activeRunId || eventState === "running")) state = "running";

  const plan = currentPlan(scopedEvents, runId);
  const activeStep = plan.activeIndex === null ? null : {
    index: plan.activeIndex + 1,
    total: plan.steps.length,
    label: plan.steps[plan.activeIndex].label,
    status: plan.steps[plan.activeIndex].status,
    completedCount: plan.completedCount,
  };
  const reasoningText = reasoningSummary(scopedEvents, runId);
  const startedItem = latestItemStart(scopedEvents, runId);
  const files = patchCounts(scopedEvents, runId);
  const history = [];
  const seenHistory = new Set();
  for (const event of scopedEvents) {
    if (!eventBelongsToRun(event, runId)) continue;
    const payload = eventPayload(event);
    if (event.event_type === "plan.updated" && Array.isArray(payload.plan)) {
      for (const item of payload.plan.slice(0, MAX_PLAN_STEPS)) {
        if (isRecord(item) && (item.status === "completed" || item.status === "inProgress" || item.status === "in_progress")) {
          addHistory(history, seenHistory, item.step, "plan");
        }
      }
    } else if (event.event_type === "reasoning.summary_delta") {
      // Chunks are added as one bounded summary below.
    } else if ((event.event_type === "item.started" || event.event_type === "item.completed") && Object.hasOwn(ITEM_LABELS, payload.item_type)) {
      const historyKind = payload.item_type === "collabAgentToolCall" || payload.item_type === "subAgentActivity"
        ? "subagent"
        : "item";
      addHistory(history, seenHistory, itemLabel(payload), historyKind);
    } else if (event.event_type === "patch.updated") {
      addHistory(history, seenHistory, "Updating files", "patch");
    }
  }
  addHistory(history, seenHistory, reasoningText, "reasoning");
  const terminal = TERMINAL_STATES.has(state);
  const projectedAssistant = assistantState(scopedEvents, runId);
  const assistant = projectedAssistant === "streaming" && state !== "running"
    ? "idle"
    : projectedAssistant;
  const subagents = terminal
    ? clearTerminalSubagentActivity(latestSubagentSnapshot(scopedEvents, runId))
    : latestSubagentSnapshot(scopedEvents, runId);
  const stepAction = activeStep?.status === "completed" ? "" : activeStep?.label;
  let action = state === "running" ? stepAction || reasoningText || startedItem?.label || "" : "";
  if (!action && state === "queued") action = "Waiting in queue";
  if (!action && state === "running") action = assistant === "streaming" ? "Generating a response" : "Working on the request";
  if (!action && state === "completed") action = "Run completed";
  if (!action && state === "failed") action = "Run failed";
  if (!action && state === "cancelled") action = "Run cancelled";
  if (!action && state === "interrupted") action = "Run interrupted";

  return {
    state,
    status: state,
    busy: state === "queued" || state === "running",
    terminal,
    runId,
    action: safeText(action),
    currentActivity: safeText(action),
    step: activeStep,
    stages: plan.steps.map((step, index) => ({ ...step, index: index + 1 })),
    actionHistory: history,
    files,
    subagents,
    assistant,
    assistantState: assistant,
  };
}

export const deriveRunActivity = getRunActivityViewModel;
