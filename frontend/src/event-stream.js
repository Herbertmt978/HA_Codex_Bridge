import { normalizeEvents, parseEvent } from "./protocol.js";

export function createEventStreamState({ cursor = 0, maxEvents = 10000 } = {}) {
  return {
    cursor: Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0,
    events: [],
    maxEvents: Math.max(1, Math.min(10000, Number(maxEvents) || 10000)),
    needsSnapshot: false,
    error: null,
  };
}

export function acceptEvent(state, value) {
  const current = state || createEventStreamState();
  const event = parseEvent(value);
  if (!event) return { state: current, accepted: false, reason: "invalid" };
  if (event.sequence <= current.cursor) return { state: current, accepted: false, reason: "replay" };

  if (event.event_type === "bridge.snapshot_required") {
    return {
      state: { ...current, cursor: event.sequence, needsSnapshot: true },
      accepted: true,
      control: "snapshot",
      event,
    };
  }
  if (event.event_type === "bridge.error") {
    const message = typeof event.payload?.error === "string" ? event.payload.error.slice(0, 1000) : "Bridge event stream failed";
    return {
      state: { ...current, cursor: event.sequence, needsSnapshot: true, error: message },
      accepted: true,
      control: "error",
      event,
    };
  }
  if (current.needsSnapshot) {
    return {
      state: { ...current, cursor: event.sequence, needsSnapshot: true },
      accepted: true,
      control: "snapshot",
      event,
    };
  }
  if (event.event_id && current.events.some((item) => item.event_id === event.event_id)) {
    // A duplicate event with a newer cursor must not mutate the stream state.
    return { state: current, accepted: false, reason: "duplicate" };
  }
  const events = normalizeEvents([...current.events, event], { maxEvents: current.maxEvents });
  return {
    state: { ...current, cursor: event.sequence, events, needsSnapshot: false, error: null },
    accepted: true,
    event,
  };
}

export function acceptEvents(state, values) {
  let next = state || createEventStreamState();
  const accepted = [];
  const controls = [];
  for (const value of normalizeEvents(values)) {
    const result = acceptEvent(next, value);
    next = result.state;
    if (result.accepted) {
      accepted.push(result.event);
      if (result.control) {
        controls.push(result.control);
        // A control event is a replay boundary. Later events cannot be trusted
        // until the panel has refreshed the authoritative snapshot or surfaced
        // the broker error.
        break;
      }
    }
  }
  return { state: next, accepted, controls };
}

export function resetEventStream(state, cursor = 0) {
  return createEventStreamState({ cursor, maxEvents: state?.maxEvents });
}
