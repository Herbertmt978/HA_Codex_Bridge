import { describe, expect, it } from "vitest";
import { acceptEvent, acceptEvents, createEventStreamState } from "../src/event-stream.js";
import { makeEvent } from "./helpers.js";

describe("event stream", () => {
  it("accepts monotonic events and rejects replay", () => {
    const initial = createEventStreamState();
    const first = acceptEvent(initial, makeEvent({ sequence: 1 }));
    expect(first.accepted).toBe(true);
    expect(first.state.cursor).toBe(1);
    const replay = acceptEvent(first.state, makeEvent({ sequence: 1 }));
    expect(replay.accepted).toBe(false);
    expect(replay.state.cursor).toBe(1);
  });

  it("does not let duplicate IDs advance the cursor", () => {
    const first = acceptEvent(createEventStreamState(), makeEvent({ sequence: 1, event_id: "evt_same" }));
    const duplicate = acceptEvent(first.state, makeEvent({ sequence: 2, event_id: "evt_same" }));
    expect(duplicate.accepted).toBe(false);
    expect(duplicate.reason).toBe("duplicate");
    expect(duplicate.state.cursor).toBe(1);
  });

  it("marks control events without rendering them as transcript events", () => {
    const snapshot = acceptEvent(createEventStreamState({ cursor: 4 }), makeEvent({ sequence: 5, event_type: "bridge.snapshot_required", payload: {} }));
    expect(snapshot.control).toBe("snapshot");
    expect(snapshot.state.needsSnapshot).toBe(true);
    expect(snapshot.state.events).toEqual([]);
    const error = acceptEvent(snapshot.state, makeEvent({ sequence: 6, event_type: "bridge.error", payload: { error: "bad bridge" } }));
    expect(error.control).toBe("error");
    expect(error.state.error).toBe("bad bridge");
    expect(error.state.needsSnapshot).toBe(true);
  });

  it("keeps the snapshot boundary sticky until an authoritative replay", () => {
    const snapshot = acceptEvent(createEventStreamState({ cursor: 4 }), makeEvent({ sequence: 5, event_type: "bridge.snapshot_required", payload: {} }));
    const laterEvent = acceptEvent(snapshot.state, makeEvent({ sequence: 6, event_type: "message.created" }));

    expect(laterEvent.control).toBe("snapshot");
    expect(laterEvent.state.needsSnapshot).toBe(true);
    expect(laterEvent.state.cursor).toBe(5);
    expect(laterEvent.state.events).toEqual([]);
  });

  it("stops a batch at snapshot and error replay boundaries", () => {
    const snapshotBatch = acceptEvents(createEventStreamState(), [
      makeEvent({ sequence: 1 }),
      makeEvent({ sequence: 2, event_type: "bridge.snapshot_required", payload: {} }),
      makeEvent({ sequence: 3 }),
    ]);
    expect(snapshotBatch.controls).toEqual(["snapshot"]);
    expect(snapshotBatch.state.cursor).toBe(2);
    expect(snapshotBatch.state.needsSnapshot).toBe(true);
    expect(snapshotBatch.state.events.map((event) => event.sequence)).toEqual([1]);

    const errorBatch = acceptEvents(createEventStreamState(), [
      makeEvent({ sequence: 4, event_type: "bridge.error", payload: { error: "broker stopped" } }),
      makeEvent({ sequence: 5 }),
    ]);
    expect(errorBatch.controls).toEqual(["error"]);
    expect(errorBatch.state.cursor).toBe(4);
    expect(errorBatch.state.error).toBe("broker stopped");
    expect(errorBatch.state.events).toEqual([]);
  });

  it("sorts a replay batch before advancing the cursor", () => {
    const result = acceptEvents(createEventStreamState(), [makeEvent({ sequence: 3 }), makeEvent({ sequence: 1 }), makeEvent({ sequence: 2 })]);
    expect(result.accepted.map((event) => event.sequence)).toEqual([1, 2, 3]);
    expect(result.state.cursor).toBe(3);
  });

  it("bounds retained events", () => {
    const result = acceptEvents(createEventStreamState({ maxEvents: 2 }), [1, 2, 3].map((sequence) => makeEvent({ sequence })));
    expect(result.state.events.map((event) => event.sequence)).toEqual([2, 3]);
  });
});
