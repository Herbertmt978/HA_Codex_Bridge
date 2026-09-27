export const MAX_CONVERSATION_TURNS = 12_500;
const SNIPPET_LIMIT = 120;
export function conversationMarkerWidth(length) {
  return Math.round(6 + Math.min(1, Math.log2(1 + Math.max(0, Number(length) || 0)) / 14) * 20);
}

// Bookmarks contain only turn anchors, scoped by the HA user and chat in the
// caller. Never persist prompt/response text or provider identifiers here.
export function readConversationBookmarks(storage, key) {
  try {
    const value = JSON.parse(storage.getItem(key) || "[]");
    return new Set((Array.isArray(value) ? value : []).filter((item) => Number.isSafeInteger(item) && item > 0).slice(-500));
  } catch { return new Set(); }
}

export function saveConversationBookmarks(storage, key, values) {
  const safe = [...values].filter((item) => Number.isSafeInteger(item) && item > 0).slice(-500);
  storage.setItem(key, JSON.stringify(safe));
  return new Set(safe);
}
const TERMINAL_LABELS = Object.freeze({
  completed: "Run completed without a recorded answer",
  cancelled: "Run cancelled",
  failed: "Run failed",
  interrupted: "Run interrupted",
  cleared: "Queued turn cleared",
});

function snippet(value) {
  if (typeof value !== "string") return "";
  const compact = value.replace(/\s+/gu, " ").trim();
  if (compact.length <= SNIPPET_LIMIT) return compact;
  return `${compact.slice(0, SNIPPET_LIMIT - 1).trimEnd()}…`;
}

/** Project retained transcript events into safe, user-visible turn previews. */
export function projectTranscriptMessages(events = []) {
  const edits = new Map();
  const removed = new Set();
  const removedQueuedRuns = new Set();
  const messages = new Map();
  const queuedByRun = new Map();
  // The runtime broker records queue ownership in run.queued; the older
  // runner also marks message.created.queued. Both use the same run identity.
  const queueBoundaries = new Map();
  for (const event of events) {
    const runId = event?.payload?.run_id;
    if (event?.event_type === "run.queued" && typeof runId === "string" && runId
      && Number.isSafeInteger(event.sequence) && event.sequence > 0) {
      queueBoundaries.set(runId, Math.min(queueBoundaries.get(runId) ?? Infinity, event.sequence));
    }
  }
  // Later active-turn steers share the run ID but cannot own its queued draft.
  const isQueuedDraft = (event) => event?.event_type === "message.created"
    && (event.payload?.queued === true || event.sequence < queueBoundaries.get(event.payload?.run_id));
  for (const event of events) {
    if (!["message.created", "message.completed"].includes(event?.event_type)) continue;
    messages.set(event.sequence, event);
    const runId = event.payload?.run_id;
    if (isQueuedDraft(event) && typeof runId === "string" && runId) {
      queuedByRun.set(runId, queuedByRun.has(runId) ? null : event);
    }
  }
  const targetSequence = (event) => {
    const reference = event.payload?.message_sequence;
    if (!Number.isSafeInteger(reference) || reference <= 0) return null;
    const runId = event.payload?.run_id;
    let target = messages.get(reference);
    if (typeof runId === "string" && runId) {
      if (target?.payload?.run_id !== runId) target = null;
      // API v1 orders events by global cursor; queue references use the
      // thread-local message sequence. Each queued run owns one draft.
      // A unique run-bound draft bridges those namespaces without matching
      // another run's coincidentally equal cursor or an ambiguous anchor.
      target ||= queuedByRun.get(runId);
    }
    return target?.sequence ?? null;
  };
  for (const event of events) {
    if (!["message.removed", "message.updated"].includes(event?.event_type)) continue;
    const sequence = targetSequence(event);
    if (sequence === null) continue;
    if (event.event_type === "message.removed") {
      removed.add(sequence);
      const target = messages.get(sequence);
      if (isQueuedDraft(target) && typeof target.payload?.run_id === "string") {
        removedQueuedRuns.add(target.payload.run_id);
      }
    }
    if (event.event_type === "message.updated" && typeof event.payload?.text === "string") {
      edits.set(sequence, event.payload.text);
    }
  }
  return events.filter((event) => !(["message.created", "message.completed"].includes(event?.event_type) && removed.has(event.sequence))
    && !(["run.queued", "run.dequeued", "run.cancelled"].includes(event?.event_type) && removedQueuedRuns.has(event.payload?.run_id))).map((event) => event?.event_type === "message.created" && edits.has(event.sequence)
    ? { ...event, payload: { ...event.payload, text: edits.get(event.sequence) } } : event);
}

export function projectConversationTurns(events = []) {
  const turns = [];
  const byRun = new Map();
  let lastUserTurn = null;

  for (const event of projectTranscriptMessages(Array.isArray(events) ? events : [])) {
    const type = event?.event_type;
    if (!Number.isSafeInteger(event?.sequence) || event.sequence <= 0) continue;

    const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
    const runId = typeof payload.run_id === "string" && payload.run_id ? payload.run_id : null;
    let turn = runId ? byRun.get(runId) : null;

    if (type === "run.queue_cleared") {
      for (const queuedTurn of runId ? (turn ? [turn] : []) : turns.filter((item) => item.queued)) {
        queuedTurn.queued = false;
        queuedTurn.outcome ||= "cleared";
      }
      continue;
    }
    if (["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(type)) {
      if (!turn && !runId) turn = lastUserTurn;
      if (turn) {
        turn.queued = false;
        turn.outcome = type.slice(4);
      }
      continue;
    }
    if (["run.queued", "run.started", "run.dequeued"].includes(type)) {
      if (turn) turn.queued = type === "run.queued";
      continue;
    }
    if (type !== "message.created" && type !== "message.completed") continue;

    if (type === "message.created") {
      if (!turn) {
        turn = { runId, firstSequence: event.sequence, userSequence: event.sequence, prompt: "", response: "", queued: false };
        turns.push(turn);
        if (runId) byRun.set(runId, turn);
      }
      turn.userSequence ??= event.sequence;
      turn.firstSequence = Math.min(turn.firstSequence, event.sequence);
      turn.prompt = snippet([turn.prompt, snippet(payload.text)].filter(Boolean).join(" "));
      turn.contentLength = Math.min(1_000_000, (turn.contentLength || 0) + (typeof payload.text === "string" ? payload.text.length : 0));
      turn.queued ||= payload.queued === true;
      lastUserTurn = turn;
      continue;
    }

    if (!turn && !runId && lastUserTurn) turn = lastUserTurn;
    if (!turn) {
      turn = { runId, firstSequence: event.sequence, userSequence: null, prompt: "", response: "", queued: false };
      turns.push(turn);
      if (runId) byRun.set(runId, turn);
    }
    turn.firstSequence = Math.min(turn.firstSequence, event.sequence);
    const response = snippet(payload.text);
    turn.contentLength = Math.min(1_000_000, (turn.contentLength || 0) + (typeof payload.text === "string" ? payload.text.length : 0));
    if (response) turn.response = snippet([turn.response, response].filter(Boolean).join(" · "));
  }

  const ordered = turns
    .sort((left, right) => left.firstSequence - right.firstSequence)
    .slice(-MAX_CONVERSATION_TURNS);

  return ordered.map((turn, index) => {
    const number = index + 1;
    const pending = Boolean(turn.prompt && !turn.response && !turn.outcome);
    const outcomeLabel = !turn.response ? TERMINAL_LABELS[turn.outcome] || "" : "";
    const label = snippet([
      `Turn ${number}.`,
      turn.queued && "Queued",
      outcomeLabel,
      pending && "Codex response in progress",
      turn.prompt && `You: ${turn.prompt}`,
      turn.response && `Codex: ${turn.response}`,
    ].filter(Boolean).join(" "));
    return {
      key: String(turn.userSequence ?? turn.firstSequence),
      anchorSequence: turn.userSequence ?? turn.firstSequence,
      index: number,
      prompt: turn.prompt,
      response: turn.response,
      queued: turn.queued,
      pending,
      outcomeLabel,
      label,
      markerWidth: conversationMarkerWidth(turn.contentLength),
    };
  });
}
