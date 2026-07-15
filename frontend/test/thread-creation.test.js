/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function createdThread(overrides = {}) {
  return {
    thread_id: "thr_created",
    project_id: "prj_direct",
    title: "First chat",
    status: "idle",
    mode: "full-auto",
    attachments: [],
    effective_model: "gpt-5.6-sol",
    effective_thinking_level: "max",
    ...overrides,
  };
}

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { connection_type: "supervisor", api_version: 1 };
  panel._threadForm = {
    title: "First chat",
    mode: "full-auto",
    projectId: "prj_direct",
  };
  panel._showThreadForm = true;
  panel._startEventSubscription = vi.fn();
  panel._startPolling = vi.fn();
  return panel;
}

describe("chat creation recovery", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("keeps a successfully created chat usable when list reconciliation fails", async () => {
    const thread = createdThread();
    const panel = createPanel();
    panel._callWS = vi.fn(async (action) => {
      if (action === "create_thread") return thread;
      if (action === "list_threads") throw new Error("Bridge request failed");
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._createThread();

    expect(panel._selectedThreadId).toBe(thread.thread_id);
    expect(panel._activeThread).toEqual(thread);
    expect(panel._threads).toContainEqual(thread);
    expect(panel._showThreadForm).toBe(false);
    expect(panel._error).toBe("");
    expect(panel._startEventSubscription).toHaveBeenCalled();
    expect(panel._startPolling).toHaveBeenCalled();
  });

  it("does not turn an auxiliary snapshot failure into a create failure", async () => {
    const thread = createdThread();
    const panel = createPanel();
    panel._callWS = vi.fn(async (action) => {
      if (action === "create_thread" || action === "get_thread") return thread;
      if (action === "list_threads") return [thread];
      if (action === "get_events") throw new Error("Bridge request failed");
      if (action === "list_artifacts" || action === "list_pending_interactions") return [];
      if (action === "get_status") return {};
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._createThread();

    expect(panel._selectedThreadId).toBe(thread.thread_id);
    expect(panel._activeThread).toEqual(thread);
    expect(panel._threads).toContainEqual(thread);
    expect(panel._error).toBe("");
    expect(panel._startPolling).toHaveBeenCalled();
  });

  it("preserves the created chat while list reconciliation is eventually consistent", async () => {
    const thread = createdThread();
    const panel = createPanel();
    panel._callWS = vi.fn(async (action) => {
      if (action === "create_thread") return thread;
      if (action === "list_threads") return [];
      if (action === "get_thread") return thread;
      if (action === "get_events" || action === "list_artifacts" || action === "list_pending_interactions") return [];
      if (action === "get_status") return {};
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._createThread();

    expect(panel._selectedThreadId).toBe(thread.thread_id);
    expect(panel._activeThread).toEqual(thread);
    expect(panel._threads).toContainEqual(thread);
    expect(panel._error).toBe("");
  });
});
