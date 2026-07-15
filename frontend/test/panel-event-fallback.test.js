/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function threadRecord(threadId, title, status = "idle") {
  return {
    thread_id: threadId,
    project_id: "project-safe",
    title,
    status,
    mode: "edit",
    attachments: [],
  };
}

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
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

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

  it("clears a recovered broker error instead of silently advancing the cursor", async () => {
    const panel = pollingPanel(controlEvent("bridge.error", { error: "broker stopped" }));

    await panel._runPollTick(1);

    expect(panel._error).toBe("broker stopped");
    expect(panel._errorSource).toBe("poll");
    expect(panel._sequence).toBe(5);
    expect(panel._events).toEqual([]);

    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);

    expect(panel._error).toBe("");
  });

  it("retains a broker boundary cursor through an empty authoritative replay", async () => {
    const brokerError = controlEvent("bridge.error", { error: "broker stopped" });
    const panel = pollingPanel(brokerError);
    let firstEventsRead = true;
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") {
        if (firstEventsRead) {
          firstEventsRead = false;
          return Promise.resolve([brokerError]);
        }
        return Promise.resolve([]);
      }
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered snapshot"));
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._runPollTick(1);

    expect(panel._sequence).toBe(brokerError.sequence);
    expect(panel._error).toBe("");
  });

  it("does not replace a newer action error with a broker error from polling", async () => {
    const brokerError = controlEvent("bridge.error", { error: "broker stopped" });
    const panel = pollingPanel(brokerError);
    const delayedEvents = deferred();
    panel._callWS.mockReturnValueOnce(delayedEvents.promise);

    const pending = panel._runPollTick(1);
    panel._setError("Upload network failed", { retryable: true });
    delayedEvents.resolve([brokerError]);
    await pending;

    expect(panel._error).toBe("Upload network failed");
  });

  it("reconciles a broker replay boundary without replacing an action error", async () => {
    const panel = pollingPanel(controlEvent("bridge.error", { error: "broker stopped" }));
    panel._setError("Upload network failed", { retryable: true });
    panel._refreshActiveThread = vi.fn(async () => {
      panel._eventStream = { ...panel._eventStream, needsSnapshot: false };
      return true;
    });

    await panel._runPollTick(1);

    expect(panel._refreshActiveThread).toHaveBeenCalledOnce();
    expect(panel._error).toBe("Upload network failed");
  });

  it("clears recovered live stream errors without replacing action errors", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    panel._retireEventSubscription = vi.fn();

    panel._handleSubscribedEvent("thr_safe", { type: "error" });
    expect(panel._error).toBe("Bridge event stream failed");
    expect(panel._errorSource).toBe("poll");

    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);
    expect(panel._error).toBe("");

    panel._setError("Upload network failed", { retryable: true });
    panel._handleSubscribedEvent("thr_safe", { type: "error" });
    expect(panel._error).toBe("Upload network failed");
  });

  it("preserves a live broker error when its sticky snapshot recovery fails", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    const brokerError = controlEvent("bridge.error", { error: "broker stopped" });
    panel._retireEventSubscription = vi.fn();
    panel._handleSubscribedEvent("thr_safe", brokerError);
    panel._lastStatusRefreshAt = 0;
    expect(panel._eventStream.needsSnapshot).toBe(true);
    expect(panel._error).toBe("broker stopped");

    panel._callWS = vi.fn((action) => {
      if (action === "get_events") return Promise.resolve([]);
      if (action === "get_thread") return Promise.reject(new Error("Snapshot fetch failed"));
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);

    await panel._runPollTick(1);

    expect(panel._error).toBe("broker stopped");
    expect(panel._errorSource).toBe("poll");

    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered snapshot"));
      if (action === "get_events" || action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });
    await panel._runPollTick(1);
    expect(panel._error).toBe("");
  });

  it.each([
    ["raw stream snapshot", (panel) => panel._handleSubscribedEvent("thr_safe", { type: "snapshot_required" })],
    ["accepted stream snapshot", (panel) => panel._handleSubscribedEvent("thr_safe", controlEvent("bridge.snapshot_required"))],
  ])("preserves a newer action error during a delayed %s refresh", async (_label, triggerSnapshot) => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    const refresh = deferred();
    panel._retireEventSubscription = vi.fn();
    panel._stopEventSubscription = vi.fn();
    panel._refreshActiveThread = vi.fn(async ({ errorSource }) => {
      await refresh.promise;
      panel._clearError({ source: errorSource });
      return true;
    });

    triggerSnapshot(panel);
    expect(panel._refreshActiveThread).toHaveBeenCalledWith(expect.objectContaining({
      errorSource: "poll",
      expectedErrorRevision: 0,
    }));

    panel._setError("Upload network failed", { retryable: true });
    refresh.resolve();
    await panel._refreshActiveThread.mock.results[0].value;
    expect(panel._error).toBe("Upload network failed");
  });

  it("retries a failed snapshot replay before clearing its poll error", async () => {
    const snapshot = controlEvent("bridge.snapshot_required");
    const panel = pollingPanel(snapshot);
    panel._refreshActiveThread = vi.fn()
      .mockImplementationOnce(async () => {
        panel._setError("Bridge request failed", { source: "poll" });
        return false;
      })
      .mockImplementationOnce(async () => {
        panel._eventStream = { ...panel._eventStream, needsSnapshot: false };
        panel._clearError({ source: "poll" });
        return true;
      });

    await panel._runPollTick(1);
    expect(panel._eventStream.needsSnapshot).toBe(true);
    expect(panel._error).toBe("Bridge request failed");

    panel._callWS.mockResolvedValue([controlEvent("message.created", { text: "Untrusted after snapshot" })]);
    await panel._runPollTick(1);

    expect(panel._refreshActiveThread).toHaveBeenCalledTimes(2);
    expect(panel._sequence).toBe(snapshot.sequence);
    expect(panel._error).toBe("");
    expect(panel._events).toEqual([]);
  });

  it("retries a failed raw stream snapshot replay on a later empty poll", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    panel._retireEventSubscription = vi.fn();
    panel._refreshActiveThread = vi.fn()
      .mockImplementationOnce(async () => {
        panel._setError("Bridge request failed", { source: "poll" });
        return false;
      })
      .mockImplementationOnce(async () => {
        panel._eventStream = { ...panel._eventStream, needsSnapshot: false };
        panel._clearError({ source: "poll" });
        return true;
      });

    panel._handleSubscribedEvent("thr_safe", { type: "snapshot_required", cursor: 7 });
    await panel._refreshActiveThread.mock.results[0].value;
    expect(panel._eventStream.needsSnapshot).toBe(true);
    expect(panel._sequence).toBe(7);
    expect(panel._error).toBe("Bridge request failed");

    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);

    expect(panel._refreshActiveThread).toHaveBeenCalledTimes(2);
    expect(panel._error).toBe("");
  });

  it.each([
    ["poll", async (panel, _snapshot) => panel._runPollTick(1), controlEvent("bridge.snapshot_required")],
    ["raw stream", async (panel, snapshot) => {
      panel._handleSubscribedEvent("thr_safe", { type: "snapshot_required", cursor: snapshot.sequence });
      await vi.waitFor(() => expect(panel._eventStream.needsSnapshot).toBe(false));
    }, { ...controlEvent("bridge.snapshot_required"), sequence: 7 }],
  ])("retains the %s snapshot cursor through an empty authoritative replay", async (_label, triggerSnapshot, snapshot) => {
    const panel = pollingPanel(snapshot);
    let firstEventsRead = _label === "poll";
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._retireEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") {
        if (firstEventsRead) {
          firstEventsRead = false;
          return Promise.resolve([snapshot]);
        }
        return Promise.resolve([]);
      }
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered snapshot"));
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    await triggerSnapshot(panel, snapshot);

    expect(panel._eventStream.needsSnapshot).toBe(false);
    expect(panel._sequence).toBe(snapshot.sequence);
  });

  it("retries transient snapshot failures quietly just after creating a chat", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thr_created";
    panel._activeThread = threadRecord("thr_created", "Created");
    panel._status = {};
    panel._lastStatusRefreshAt = 0;
    panel._pollActive = true;
    panel._pollGeneration = 1;
    panel._threadRefreshGraceUntil = Date.now() + 5000;
    panel._scheduleNextPoll = vi.fn();
    panel._callWS = vi.fn().mockRejectedValue(new Error("Bridge request failed"));

    await panel._runPollTick(1);

    expect(panel._error).toBe("");
    expect(panel._scheduleNextPoll).toHaveBeenCalled();
  });

  it("does not let a delayed live refresh failure replace a newer action error", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Alpha");
    const delayedThread = deferred();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return delayedThread.promise;
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    vi.advanceTimersByTime(250);
    await Promise.resolve();
    panel._setError("Upload network failed", { retryable: true });
    delayedThread.reject(new Error("Bridge request failed"));
    await Promise.resolve();
    await Promise.resolve();

    expect(panel._error).toBe("Upload network failed");
  });

  it("keeps live thread state healthy when artifacts are temporarily unavailable", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Before live refresh", "running");
    const previousArtifacts = [{
      artifact_id: "artifact-existing",
      filename: "existing.txt",
      mime_type: "text/plain",
      size: 12,
    }];
    panel._artifacts = previousArtifacts;
    panel._selectedArtifactId = "artifact-existing";
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thread-alpha", "Live refresh", "running"));
      if (action === "list_artifacts") {
        return Promise.reject(Object.assign(new Error("Artifacts are reserved"), {
          code: "reservation_conflict",
        }));
      }
      if (action === "get_status") return Promise.resolve({ runtime: { state: "running" } });
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    await vi.advanceTimersByTimeAsync(250);

    expect(panel._activeThread?.title).toBe("Live refresh");
    expect(panel._artifacts).toEqual(previousArtifacts);
    expect(panel._error).toBe("");
    expect(panel.shadowRoot.getElementById("error-strip").classList).not.toContain("visible");
    vi.useRealTimers();
  });

  it("surfaces an artifact refresh failure once the live thread is idle", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Before live refresh");
    const previousArtifacts = [{
      artifact_id: "artifact-existing",
      filename: "existing.txt",
      mime_type: "text/plain",
      size: 12,
    }];
    panel._artifacts = previousArtifacts;
    panel._selectedArtifactId = "artifact-existing";
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thread-alpha", "Idle refresh"));
      if (action === "list_artifacts") return Promise.reject(new Error("Artifact scan failed"));
      if (action === "get_status") return Promise.resolve({ runtime: { state: "idle" } });
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    await vi.advanceTimersByTimeAsync(250);

    expect(panel._error).toBe("Artifact scan failed");
    expect(panel._errorSource).toBe("poll");
    expect(panel._activeThread?.title).toBe("Before live refresh");
    expect(panel._artifacts).toEqual(previousArtifacts);
    vi.useRealTimers();
  });

  it("surfaces a non-reservation artifact failure from a busy live refresh", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Before live refresh", "running");
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thread-alpha", "Live refresh", "running"));
      if (action === "list_artifacts") {
        return Promise.reject(Object.assign(new Error("Artifact backend failed"), {
          code: "workspace_error",
        }));
      }
      if (action === "get_status") return Promise.resolve({ runtime: { state: "running" } });
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    await vi.advanceTimersByTimeAsync(250);

    expect(panel._error).toBe("Artifact backend failed");
    expect(panel._errorSource).toBe("poll");
    vi.useRealTimers();
  });

  it("retains artifacts on a queued polling refresh with a reservation conflict", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Queued work" }));
    panel._activeThread = threadRecord("thr_safe", "Queued", "queued");
    panel._lastStatusRefreshAt = 0;
    const previousArtifacts = [{
      artifact_id: "artifact-existing",
      filename: "existing.txt",
      mime_type: "text/plain",
      size: 12,
    }];
    panel._artifacts = previousArtifacts;
    panel._selectedArtifactId = "artifact-existing";
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Queued", "queued"));
      if (action === "list_artifacts") {
        return Promise.reject(Object.assign(new Error("Artifacts are reserved"), {
          code: "reservation_conflict",
        }));
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._runPollTick(1);

    expect(panel._activeThread?.status).toBe("queued");
    expect(panel._artifacts).toEqual(previousArtifacts);
    expect(panel._error).toBe("");
  });

  it("clears a polling connection error after the next successful poll", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    panel._callWS
      .mockRejectedValueOnce(new Error("Bridge request failed"))
      .mockResolvedValueOnce([]);

    await panel._runPollTick(1);
    expect(panel._error).toBe("Bridge request failed");
    expect(panel.shadowRoot.getElementById("error-strip").classList).toContain("visible");

    await panel._runPollTick(1);

    expect(panel._error).toBe("");
    expect(panel.shadowRoot.getElementById("error-strip").classList).not.toContain("visible");
  });

  it("does not let an older successful poll clear a newer stream error", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    const delayedStatus = deferred();
    panel._eventSubscriptionActive = true;
    panel._retireEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_status") return delayedStatus.promise;
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered"));
      throw new Error(`Unexpected action: ${action}`);
    });

    const pending = panel._runPollTick(1);
    panel._handleSubscribedEvent("thr_safe", { type: "error" });
    delayedStatus.resolve({});
    await pending;
    expect(panel._error).toBe("Bridge event stream failed");

    panel._eventSubscriptionActive = false;
    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);
    expect(panel._error).toBe("");
  });

  it("does not clear an unrelated retryable action error after a successful poll", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Healthy poll" }));
    panel._callWS.mockResolvedValue([]);
    panel._setError("Upload network failed", { retryable: true });

    await panel._runPollTick(1);

    expect(panel._error).toBe("Upload network failed");
  });

  it("does not overwrite an existing action error when a poll fails", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Failed poll" }));
    panel._callWS.mockRejectedValue(new Error("Bridge request failed"));
    panel._setError("Upload network failed", { retryable: true });

    await panel._runPollTick(1);

    expect(panel._error).toBe("Upload network failed");
  });

  it("does not let an older failed poll overwrite a newer action error", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Delayed poll" }));
    const delayedPoll = deferred();
    panel._callWS
      .mockReturnValueOnce(delayedPoll.promise)
      .mockResolvedValueOnce([]);

    const pending = panel._runPollTick(1);
    panel._setError("Upload network failed", { retryable: true });
    delayedPoll.reject(new Error("Bridge request failed"));
    await pending;

    expect(panel._error).toBe("Upload network failed");

    await panel._runPollTick(1);

    expect(panel._error).toBe("Upload network failed");
  });

  it("reports a delayed poll failure after a no-op error clear", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Delayed poll" }));
    const delayedPoll = deferred();
    panel._callWS.mockReturnValueOnce(delayedPoll.promise);

    const pending = panel._runPollTick(1);
    expect(panel._clearError()).toBe(false);
    delayedPoll.reject(new Error("Bridge request failed"));
    await pending;

    expect(panel._error).toBe("Bridge request failed");
    expect(panel._errorSource).toBe("poll");
  });

  it("does not clear a newer action error while reconciling a poll snapshot", async () => {
    const snapshotEvent = controlEvent("bridge.snapshot_required");
    const panel = pollingPanel(snapshotEvent);
    const snapshotThread = deferred();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") return Promise.resolve([snapshotEvent]);
      if (action === "get_thread") return snapshotThread.promise;
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const pending = panel._runPollTick(1);
    await vi.waitFor(() => expect(panel._callWS).toHaveBeenCalledWith("get_thread", { thread_id: "thr_safe" }));
    panel._setError("Upload network failed", { retryable: true });
    snapshotThread.resolve(threadRecord("thr_safe", "Recovered snapshot"));
    await pending;

    expect(panel._error).toBe("Upload network failed");
  });

  it("does not clear a newer polling error while snapshot recovery succeeds", async () => {
    const panel = pollingPanel(controlEvent("bridge.snapshot_required"));
    const snapshotThread = deferred();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return snapshotThread.promise;
      if (action === "get_events") return Promise.resolve([]);
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const pending = panel._refreshActiveThread({
      errorSource: "poll",
      expectedErrorRevision: panel._errorRevision,
    });
    await vi.waitFor(() => expect(panel._callWS).toHaveBeenCalledWith("get_thread", { thread_id: "thr_safe" }));
    panel._setError("Newer stream failure", { source: "poll" });
    snapshotThread.resolve(threadRecord("thr_safe", "Recovered snapshot"));
    await pending;

    expect(panel._error).toBe("Newer stream failure");

    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);
    expect(panel._error).toBe("");
  });

  it("does not loop snapshot recovery when an authoritative replay contains a bridge error", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    const historicalBridgeError = controlEvent("bridge.error", { error: "broker stopped" });
    const laterMessage = {
      ...controlEvent("message.created", { text: "Retained after broker error" }),
      event_id: "evt_after_broker_error",
      sequence: 6,
    };
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered snapshot"));
      if (action === "get_events") return Promise.resolve([historicalBridgeError, laterMessage]);
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });
    const refresh = vi.spyOn(panel, "_refreshActiveThread");

    await panel._refreshActiveThread({
      errorSource: "poll",
      expectedErrorRevision: panel._errorRevision,
      cursorFloor: historicalBridgeError.sequence,
    });
    expect(panel._eventStream.needsSnapshot).toBe(false);
    expect(panel._sequence).toBe(6);
    expect(panel._events).toEqual([laterMessage]);

    panel._lastStatusRefreshAt = Date.now();
    panel._callWS.mockResolvedValue([]);
    await panel._runPollTick(1);
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("advances the cursor past control-only authoritative replays", async () => {
    const panel = pollingPanel(controlEvent("message.created", { text: "Recovered" }));
    const historicalBridgeError = controlEvent("bridge.error", { error: "broker stopped" });
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return Promise.resolve(threadRecord("thr_safe", "Recovered snapshot"));
      if (action === "get_events") return Promise.resolve([historicalBridgeError]);
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._refreshActiveThread({
      errorSource: "poll",
      expectedErrorRevision: panel._errorRevision,
      cursorFloor: historicalBridgeError.sequence,
    });

    expect(panel._eventStream.needsSnapshot).toBe(false);
    expect(panel._sequence).toBe(historicalBridgeError.sequence);
  });

  it("does not overwrite an existing action error when poll snapshot reconciliation fails", async () => {
    const snapshotEvent = controlEvent("bridge.snapshot_required");
    const panel = pollingPanel(snapshotEvent);
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") return Promise.resolve([snapshotEvent]);
      if (action === "get_thread") return Promise.reject(new Error("Bridge request failed"));
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });
    panel._setError("Upload network failed", { retryable: true });

    await panel._runPollTick(1);

    expect(panel._error).toBe("Upload network failed");
  });

  it("rejects a misrouted live event from another chat", () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thr_safe";
    panel._promptMutation = {
      threadId: "thr_safe",
      prompt: "Keep this request isolated",
      clientRequestId: "prompt-safe",
      state: "reconciling",
    };

    panel._handleSubscribedEvent("thr_safe", {
      event_id: "evt_other_chat",
      thread_id: "thr_other",
      sequence: 1,
      event_type: "message.created",
      payload: { text: "Wrong chat", client_request_id: "prompt-safe" },
    });

    expect(panel._sequence).toBe(0);
    expect(panel._events).toEqual([]);
    expect(panel._promptMutation).not.toBeNull();
  });

  it("preserves exponential reconnect attempts across failed subscriptions", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thr_safe";
    panel._eventReconnectAttempt = 3;
    panel._hass = {
      connection: {
        subscribeMessage: vi.fn().mockRejectedValue(new Error("stream unavailable")),
      },
    };

    await panel._startEventSubscription({ reconnecting: true });

    expect(panel._eventReconnectAttempt).toBe(4);
    panel._stopEventSubscription();
    vi.useRealTimers();
  });

  it("rejects an older same-chat snapshot after an A-to-B-to-A selection", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._startEventSubscription = vi.fn();
    panel._startPolling = vi.fn();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    const firstAlpha = deferred();
    const secondAlpha = deferred();
    let alphaRequests = 0;
    panel._callWS = vi.fn((action, payload) => {
      if (action === "get_thread" && payload.thread_id === "thread-alpha") {
        alphaRequests += 1;
        return alphaRequests === 1 ? firstAlpha.promise : secondAlpha.promise;
      }
      if (action === "get_thread") return Promise.resolve(threadRecord(payload.thread_id, "Beta"));
      if (action === "get_events" || action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const staleRefresh = panel._refreshActiveThread();
    await panel._selectThread("thread-beta");
    const currentRefresh = panel._selectThread("thread-alpha");
    secondAlpha.resolve(threadRecord("thread-alpha", "Newest Alpha"));
    await currentRefresh;
    expect(panel._activeThread?.title).toBe("Newest Alpha");
    const subscriptionCount = panel._startEventSubscription.mock.calls.length;

    firstAlpha.resolve(threadRecord("thread-alpha", "Stale Alpha"));
    await staleRefresh;

    expect(panel._activeThread?.title).toBe("Newest Alpha");
    expect(panel._startEventSubscription).toHaveBeenCalledTimes(subscriptionCount);
  });

  it("does not surface a rejected refresh from a previously selected chat", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._startEventSubscription = vi.fn();
    panel._startPolling = vi.fn();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    const staleAlpha = deferred();
    panel._callWS = vi.fn((action, payload) => {
      if (action === "get_thread" && payload.thread_id === "thread-alpha") return staleAlpha.promise;
      if (action === "get_thread") return Promise.resolve(threadRecord(payload.thread_id, "Beta"));
      if (action === "get_events" || action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const staleRefresh = panel._refreshActiveThread();
    await panel._selectThread("thread-beta");
    staleAlpha.reject(new Error("private stale alpha failure"));
    await staleRefresh;

    expect(panel._selectedThreadId).toBe("thread-beta");
    expect(panel._error).toBe("");
  });

  it("rejects a delayed live response from an earlier visit to the same chat", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Initial Alpha");
    panel._status = {};
    panel._refreshActiveThread = vi.fn().mockResolvedValue(true);
    panel._startPolling = vi.fn();
    const liveThread = deferred();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") return liveThread.promise;
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    vi.advanceTimersByTime(250);
    await Promise.resolve();
    await panel._selectThread("thread-beta");
    await panel._selectThread("thread-alpha");
    panel._activeThread = threadRecord("thread-alpha", "Newest Alpha");

    liveThread.resolve(threadRecord("thread-alpha", "Stale Live Alpha"));
    await vi.runAllTimersAsync();

    expect(panel._activeThread?.title).toBe("Newest Alpha");
    vi.useRealTimers();
  });

  it("rejects a delayed poll response from an earlier visit to the same chat", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Initial Alpha");
    panel._status = {};
    panel._lastStatusRefreshAt = Date.now();
    panel._pollActive = true;
    panel._pollGeneration = 1;
    panel._scheduleNextPoll = vi.fn();
    panel._refreshActiveThread = vi.fn().mockResolvedValue(true);
    panel._startPolling = vi.fn();
    const pollEvents = deferred();
    panel._callWS = vi.fn((action) => {
      if (action === "get_events") return pollEvents.promise;
      throw new Error(`Unexpected action: ${action}`);
    });

    const stalePoll = panel._runPollTick(1);
    await panel._selectThread("thread-beta");
    await panel._selectThread("thread-alpha");
    panel._events = [{ event_id: "new-alpha" }];
    panel._sequence = 10;

    pollEvents.resolve([{
      ...controlEvent("message.created", { text: "stale poll" }),
      thread_id: "thread-alpha",
    }]);
    await stalePoll;

    expect(panel._events).toEqual([{ event_id: "new-alpha" }]);
    expect(panel._sequence).toBe(10);
  });

  it("keeps a newer full snapshot when an older same-chat live refresh finishes last", async () => {
    vi.useFakeTimers();
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Initial Alpha");
    panel._status = {};
    panel._startEventSubscription = vi.fn();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    const staleLiveThread = deferred();
    let threadRequests = 0;
    panel._callWS = vi.fn((action) => {
      if (action === "get_thread") {
        threadRequests += 1;
        return threadRequests === 1
          ? staleLiveThread.promise
          : Promise.resolve(threadRecord("thread-alpha", "Newest Full Alpha"));
      }
      if (action === "get_events" || action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    panel._scheduleLiveRefresh("thread-alpha");
    vi.advanceTimersByTime(250);
    await Promise.resolve();
    expect(threadRequests).toBe(1);
    await panel._refreshActiveThread();
    expect(panel._activeThread?.title).toBe("Newest Full Alpha");

    staleLiveThread.resolve(threadRecord("thread-alpha", "Stale Live Alpha"));
    await vi.runAllTimersAsync();

    expect(panel._activeThread?.title).toBe("Newest Full Alpha");
    vi.useRealTimers();
  });

  it("keeps a newer full snapshot when an older same-chat poll finishes last", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Initial Alpha");
    panel._status = {};
    panel._sequence = 4;
    panel._lastStatusRefreshAt = Date.now();
    panel._pollActive = true;
    panel._pollGeneration = 1;
    panel._scheduleNextPoll = vi.fn();
    panel._startEventSubscription = vi.fn();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    const stalePollEvents = deferred();
    panel._callWS = vi.fn((action, payload) => {
      if (action === "get_events") {
        return payload.after === 4 ? stalePollEvents.promise : Promise.resolve([]);
      }
      if (action === "get_thread") return Promise.resolve(threadRecord("thread-alpha", "Newest Full Alpha"));
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const stalePoll = panel._runPollTick(1);
    await Promise.resolve();
    await panel._refreshActiveThread();
    expect(panel._activeThread?.title).toBe("Newest Full Alpha");

    stalePollEvents.resolve([{
      ...controlEvent("message.created", { text: "stale poll" }),
      thread_id: "thread-alpha",
    }]);
    await stalePoll;

    expect(panel._events).toEqual([]);
    expect(panel._sequence).toBe(0);
  });

  it("keeps a newer poll update when an older same-chat full snapshot finishes last", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Initial Alpha");
    panel._status = {};
    panel._sequence = 4;
    panel._lastStatusRefreshAt = Date.now();
    panel._pollActive = true;
    panel._pollGeneration = 1;
    panel._scheduleNextPoll = vi.fn();
    panel._startEventSubscription = vi.fn();
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    const staleFullThread = deferred();
    let threadRequests = 0;
    panel._callWS = vi.fn((action, payload) => {
      if (action === "get_thread") {
        threadRequests += 1;
        return threadRequests === 1
          ? staleFullThread.promise
          : Promise.resolve(threadRecord("thread-alpha", "Newest Poll Alpha"));
      }
      if (action === "get_events") {
        return Promise.resolve(payload.after === 4 ? [{
          ...controlEvent("message.created", { text: "new poll" }),
          thread_id: "thread-alpha",
        }] : []);
      }
      if (action === "list_artifacts") return Promise.resolve([]);
      if (action === "get_status") return Promise.resolve({});
      throw new Error(`Unexpected action: ${action}`);
    });

    const staleFull = panel._refreshActiveThread();
    await Promise.resolve();
    await panel._runPollTick(1);
    expect(panel._events).toHaveLength(1);
    expect(panel._sequence).toBe(5);

    staleFullThread.resolve(threadRecord("thread-alpha", "Stale Full Alpha"));
    await staleFull;

    expect(panel._events).toHaveLength(1);
    expect(panel._events[0]?.payload?.text).toBe("new poll");
    expect(panel._sequence).toBe(5);
    expect(panel._activeThread?.title).toBe("Newest Poll Alpha");
  });

  it.each([
    ["archive", async (panel) => {
      panel._callWS = vi.fn().mockResolvedValue({
        ...threadRecord("thread-alpha", "Archived Alpha"),
        archived_at: "2026-07-14T12:00:00Z",
      });
      await panel._archiveThread("thread-alpha");
    }],
    ["restore", async (panel) => {
      panel._callWS = vi.fn().mockResolvedValue(threadRecord("thread-beta", "Restored Beta"));
      await panel._restoreThread("thread-beta");
    }],
    ["delete", async (panel) => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      panel._callWS = vi.fn().mockResolvedValue({});
      await panel._deleteThread("thread-alpha");
    }],
  ])("restarts polling after a %s replacement selects another chat", async (_action, run) => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._threads = [
      threadRecord("thread-alpha", "Alpha"),
      threadRecord("thread-beta", "Beta"),
    ];
    panel._selectedThreadId = "thread-alpha";
    panel._activeThread = threadRecord("thread-alpha", "Alpha");
    panel._pollActive = true;
    panel._pollGeneration = 1;
    panel._refreshActiveThread = vi.fn().mockResolvedValue(true);
    panel._startPolling = vi.fn();

    await run(panel);

    expect(panel._selectedThreadId).toBe("thread-beta");
    expect(panel._refreshActiveThread).toHaveBeenCalledOnce();
    expect(panel._startPolling).toHaveBeenCalledOnce();
  });
});
