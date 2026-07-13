import { describe, expect, it } from "vitest";
import { normalizeEvents, parseEvent, parseEvents, parseIdentifier } from "../src/protocol.js";
import { makeEvent } from "./helpers.js";

describe("protocol validation", () => {
  it("accepts typed positive-cursor events and copies payloads", () => {
    const raw = makeEvent({ sequence: 2 });
    const event = parseEvent(raw);
    expect(event).toMatchObject({ sequence: 2, event_type: "message.completed" });
    expect(event.payload).not.toBe(raw.payload);
  });

  it.each([
    { sequence: 0 },
    { sequence: -1 },
    { sequence: Number.MAX_SAFE_INTEGER + 1 },
    { sequence: 1.5 },
    { event_type: "" },
    { event_type: "x".repeat(121) },
    { payload: null },
    { payload: [] },
    { event_id: '" onclick="alert(1)' },
  ])("rejects malformed event %#", (change) => {
    expect(parseEvent(makeEvent(change))).toBeNull();
  });

  it("sorts and de-duplicates by sequence and event ID", () => {
    const events = normalizeEvents([
      makeEvent({ sequence: 4, event_id: "evt_same" }),
      makeEvent({ sequence: 2, event_id: "evt_two" }),
      makeEvent({ sequence: 3, event_id: "evt_same" }),
      makeEvent({ sequence: 2, event_id: "evt_duplicate" }),
      { sequence: 99, event_type: "message.completed", payload: null },
    ]);
    expect(events.map((event) => event.sequence)).toEqual([2, 3]);
    expect(events[1].event_id).toBe("evt_same");
  });

  it("filters invalid batches without allowing a bad cursor", () => {
    expect(parseEvents([makeEvent({ sequence: 1 }), { sequence: 999, event_type: "message.created", payload: "bad" }])).toHaveLength(1);
  });

  it("rejects path-breaking IDs", () => {
    expect(parseIdentifier("thr_safe")).toBe("thr_safe");
    expect(parseIdentifier('thr_" onclick="x')).toBeNull();
    expect(parseIdentifier("<script>")).toBeNull();
    expect(parseIdentifier("../thread")).toBeNull();
    expect(parseIdentifier("thread id")).toBeNull();
  });
});
