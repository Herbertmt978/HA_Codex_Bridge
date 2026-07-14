import { sanitizeId } from "./safe-dom.js";

export class ProtocolError extends Error {
  constructor(message, code = "protocol_invalid") {
    super(message);
    this.name = "ProtocolError";
    this.code = code;
  }
}

const EVENT_TYPES = new Set([
  "message.created",
  "message.completed",
  "run.started",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.queued",
  "run.dequeued",
  "run.queue_cleared",
  "attachment.added",
  "artifact.added",
  "thread.updated",
  "thread.archived",
  "thread.restored",
  "session.bound",
  "bridge.snapshot_required",
  "bridge.error",
]);

const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function parseEvent(value, { allowUnknownType = true } = {}) {
  if (!isRecord(value)) return null;
  if (!Number.isSafeInteger(value.sequence) || value.sequence <= 0) return null;
  if (typeof value.event_type !== "string" || !value.event_type || value.event_type.length > 120) return null;
  if (!allowUnknownType && !EVENT_TYPES.has(value.event_type)) return null;
  if (!isRecord(value.payload)) return null;
  if (value.event_id !== undefined && (typeof value.event_id !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(value.event_id))) return null;
  if (value.thread_id !== undefined && (typeof value.thread_id !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(value.thread_id))) return null;
  return {
    ...value,
    event_id: value.event_id === undefined ? undefined : sanitizeId(value.event_id),
    thread_id: value.thread_id === undefined ? undefined : sanitizeId(value.thread_id),
    event_type: value.event_type,
    sequence: value.sequence,
    payload: { ...value.payload },
  };
}

export function parseEvents(value, options) {
  if (!Array.isArray(value)) return [];
  return normalizeEvents(value.map((item) => parseEvent(item, options)).filter(Boolean));
}

/** Sort by the trusted cursor and remove duplicate sequence/event IDs. */
export function normalizeEvents(events, { maxEvents = 10000 } = {}) {
  const seenSequences = new Set();
  const seenIds = new Set();
  const valid = [];
  const candidates = [];
  for (const item of Array.isArray(events) ? events : []) {
    const event = item?.sequence ? parseEvent(item) : null;
    if (event) candidates.push(event);
  }
  candidates.sort((left, right) => left.sequence - right.sequence);
  for (const event of candidates) {
    if (seenSequences.has(event.sequence) || (event.event_id && seenIds.has(event.event_id))) continue;
    seenSequences.add(event.sequence);
    if (event.event_id) seenIds.add(event.event_id);
    valid.push(event);
  }
  const limit = Number.isFinite(maxEvents) ? Math.max(1, Math.min(10000, Math.floor(maxEvents))) : 10000;
  return valid.slice(-limit);
}

export function parseEnvelope(value) {
  if (!isRecord(value)) return null;
  const type = typeof value.type === "string" ? value.type : "";
  if (!type || type.length > 120) return null;
  return { ...value, type };
}

export function parseIdentifier(value, fallback = "") {
  const raw = String(value ?? "");
  if (/^[A-Za-z0-9_.:-]{1,200}$/.test(raw)) return raw;
  return /^[A-Za-z0-9_.:-]{1,200}$/.test(fallback) ? fallback : null;
}

export { EVENT_TYPES };
