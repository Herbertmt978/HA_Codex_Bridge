/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function controlEvent(eventType, payload = {}) {
  return {
    event_id: `evt_${eventType.replaceAll(".", "_")}`,
    thread_id: "thr_safe",
    sequence: 5,
    event_type: eventType,
    payload,
    timestamp: "2026-01-01T00:00:00Z",
  };
}

function pollingPanel(event) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._selectedThreadId = "thr_safe";
  panel._activeThread = { thread_id: "thr_safe", status: "idle", attachments: [] };
  panel._status = {};
  panel._lastStatusRefreshAt = Date.now();
  panel._pollActive = true;
  panel._pollGeneration = 1;
  panel._scheduleNextPoll = vi.fn();
  panel._callWS = vi.fn(async (action) => {
    if (action === "get_events") return [event];
    throw new Error(`Unexpected action: ${action}`);
  });
  return panel;
}

describe("polling event fallback", () => {
  beforeEach(() => document.body.replaceChildren());

  it("refreshes the authoritative transcript on a snapshot boundary", async () => {
    const panel = pollingPanel(controlEvent("bridge.snapshot_required"));
    panel._refreshActiveThread = vi.fn(async () => {
      panel._pollActive = false;
    });

    await panel._runPollTick(1);

    expect(panel._refreshActiveThread).toHaveBeenCalledOnce();
    expect(panel._sequence).toBe(5);
    expect(panel._events).toEqual([]);
  });

  it("surfaces a broker error instead of silently advancing the cursor", async () => {
    const panel = pollingPanel(controlEvent("bridge.error", { error: "broker stopped" }));

    await panel._runPollTick(1);

    expect(panel._error).toBe("broker stopped");
    expect(panel._sequence).toBe(5);
    expect(panel._events).toEqual([]);
  });
});
