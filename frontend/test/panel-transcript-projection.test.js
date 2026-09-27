/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { acceptEvents, createEventStreamState } from "../src/event-stream.js";
import "../src/codex-bridge-panel.js";

function event(sequence, event_type, payload = {}, metadata = {}) {
  return {
    event_id: `evt-${sequence}`,
    thread_id: "thread-one",
    sequence,
    event_type,
    payload,
    ...metadata,
  };
}

function makePanel({ status = "running", activeRunId = "run-active", events = [] } = {}) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  const thread = {
    thread_id: "thread-one",
    project_id: "project-one",
    title: "Plan projection chat",
    status,
    active_run_id: activeRunId,
    mode: "edit",
    attachments: [],
  };
  panel._config = { api_version: 1, connection_type: "supervisor", panel_title: "Codex Bridge" };
  panel._status = {};
  panel._projects = [{ project_id: "project-one", kind: "project", name: "Project", archived_at: null }];
  panel._threads = [thread];
  panel._selectedProjectId = "project-one";
  panel._selectedThreadId = "thread-one";
  panel._activeThread = thread;
  panel._events = events;
  panel._forceMessageRebuild = true;
  panel._callWS = vi.fn(async () => ({}));
  panel._render(true);
  return panel;
}

function seedEventStream(panel, events) {
  const batch = acceptEvents(createEventStreamState(), events);
  panel._eventStream = batch.state;
  panel._events = batch.state.events;
  panel._sequence = batch.state.cursor;
  panel._forceMessageRebuild = true;
  panel._renderMessages();
}

describe("panel transcript projection", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("renders zero-offset native Plan chunks as safe Markdown and includes the answer in the rail", () => {
    const panel = makePanel({ status: "idle", activeRunId: null, events: [
      event(1, "message.created", { run_id: "plan-run", role: "user", text: "Plan this" }),
      event(2, "run.started", { run_id: "plan-run" }),
      event(3, "item.started", { item_type: "plan", item_id: "plan-item", run_id: "plan-run" }),
      event(4, "plan.delta", { delta: "# Plan overview\n\n- First step\n", item_id: "plan-item", run_id: "plan-run", byte_offset: 0, chunk_index: 0 }),
      event(5, "plan.delta", { delta: "<script>globalThis.__planPwned = true</script>", item_id: "plan-item", run_id: "plan-run", byte_offset: 0, chunk_index: 0 }),
      event(6, "item.completed", { item_type: "plan", item_id: "plan-item", run_id: "plan-run" }),
      event(7, "run.completed", { run_id: "plan-run" }),
    ] });

    const article = panel.shadowRoot.querySelector('#message-list article.message.assistant[data-sequence="6"]');
    expect(article?.querySelector(".message-state")?.textContent).toBe("Plan");
    expect(article?.querySelector(".bubble h1")?.textContent).toBe("Plan overview");
    expect(article?.querySelector(".bubble li")?.textContent).toBe("First step");
    expect(article?.querySelector(".bubble script, .bubble [onerror]")).toBeNull();
    expect(article?.querySelector(".bubble")?.textContent).toContain("<script>globalThis.__planPwned = true</script>");
    expect(globalThis.__planPwned).toBeUndefined();

    const railItem = panel.shadowRoot.querySelector('#conversation-timeline .timeline-item[data-sequence="1"]');
    expect(railItem?.getAttribute("aria-label")).toContain("Plan overview");
    expect(panel._conversationTurns[0].response).toContain("Plan overview");
    expect(panel.shadowRoot.querySelectorAll("#message-list article.message.assistant")).toHaveLength(1);
  });

  it("shows active Plan text as a labelled stream and acknowledges it only after item completion", () => {
    const events = [
      event(1, "message.created", { run_id: "run-active", role: "user", text: "Draft a plan" }),
      event(2, "run.started", { run_id: "run-active" }),
      event(3, "item.started", { item_type: "plan", item_id: "plan-active", run_id: "run-active" }),
      event(4, "plan.delta", { delta: "# In progress", item_id: "plan-active", run_id: "run-active", byte_offset: 0, chunk_index: 0 }),
    ];
    const panel = makePanel({ events });

    let assistant = panel.shadowRoot.querySelectorAll("#message-list article.message.assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].classList.contains("streaming")).toBe(true);
    expect(assistant[0].querySelector(".message-state")?.textContent).toBe("Plan");
    expect(panel._conversationTurns[0].response).toBe("");
    expect(panel._conversationTurns[0].pending).toBe(true);

    panel._events = [...panel._events, event(5, "item.completed", { item_type: "plan", item_id: "plan-active", run_id: "run-active" })];
    panel._forceMessageRebuild = true;
    panel._renderMessages();

    assistant = panel.shadowRoot.querySelectorAll("#message-list article.message.assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].classList.contains("streaming")).toBe(false);
    expect(assistant[0].querySelector(".message-state")?.textContent).toBe("Plan");
    expect(assistant[0].querySelector(".bubble h1")?.textContent).toBe("In progress");
    expect(panel._conversationTurns[0].response).toContain("In progress");
    expect(panel._conversationTurns[0].pending).toBe(false);
  });

  it("keeps Plan chunks run-scoped and does not turn plan.updated progress into a response", () => {
    const panel = makePanel({ events: [
      event(1, "message.created", { run_id: "run-active", role: "user", text: "Current run" }),
      event(2, "run.started", { run_id: "run-active" }),
      event(3, "item.started", { item_type: "plan", item_id: "active-plan", run_id: "run-active" }),
      event(4, "plan.delta", { delta: "Active plan only", item_id: "active-plan", run_id: "run-active", byte_offset: 0, chunk_index: 0 }),
      event(5, "item.started", { item_type: "plan", item_id: "other-plan", run_id: "run-other" }),
      event(6, "plan.delta", { delta: "Other run text", item_id: "other-plan", run_id: "run-other", byte_offset: 0, chunk_index: 0 }),
      event(7, "plan.updated", { run_id: "run-active", plan: [{ step: "Check files", status: "completed" }] }),
    ] });

    const streaming = panel.shadowRoot.querySelector('#message-list article.message.assistant.streaming[data-streaming-message="true"]');
    expect(streaming?.textContent).toContain("Active plan only");
    expect(streaming?.textContent).not.toContain("Other run text");
    expect(panel._conversationTurns.find((turn) => turn.prompt === "Current run")?.response).toBe("");
    expect(panel.shadowRoot.querySelectorAll('#message-list article.message.assistant:not(.streaming)')).toHaveLength(0);
  });

  it.each(["poll", "subscription"])("applies queued edit and removal by local message sequence over %s while retaining the active run", async (transport) => {
    const initial = [
      event(101, "message.created", { run_id: "run-active", role: "user", text: "Active original prompt" }),
      event(102, "run.started", { run_id: "run-active" }),
      event(104, "message.created", { run_id: "run-kept", role: "user", text: "Kept draft before edit", queued: true }),
      event(105, "message.created", { run_id: "run-cancelled", role: "user", text: "cancelleddraft", queued: true }),
      event(106, "run.queued", { run_id: "run-kept" }),
      event(107, "run.queued", { run_id: "run-cancelled" }),
    ];
    const panel = makePanel({ events: initial });
    seedEventStream(panel, initial);
    const update = event(110, "message.updated", { run_id: "run-kept", message_sequence: 4, role: "user", text: "Kept draft after edit" });
    const remove = event(111, "message.removed", { run_id: "run-cancelled", message_sequence: 5, role: "user" });
    const cancelled = event(112, "run.cancelled", { run_id: "run-cancelled" });

    if (transport === "poll") {
      panel._pollActive = true;
      panel._pollGeneration = 1;
      panel._lastStatusRefreshAt = Date.now();
      panel._scheduleNextPoll = vi.fn();
      panel._callWS.mockImplementation(async (operation) => {
        if (operation === "get_events") return [update, remove, cancelled];
        if (operation === "get_thread") return panel._activeThread;
        if (operation === "get_status") return panel._status;
        return [];
      });
      await panel._runPollTick(1);
    } else {
      panel._handleSubscribedEvent("thread-one", update);
      panel._handleSubscribedEvent("thread-one", remove);
      panel._handleSubscribedEvent("thread-one", cancelled);
    }

    const users = [...panel.shadowRoot.querySelectorAll("#message-list article.message.user")];
    expect(users.map((article) => article.querySelector(".bubble-text")?.textContent)).toEqual(["Active original prompt", "Kept draft after edit"]);
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="104"]')?.textContent).toContain("Kept draft after edit");
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="104"]')?.textContent).not.toContain("before edit");
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="105"]')).toBeNull();
    expect(panel.shadowRoot.getElementById("message-list")?.textContent).not.toContain("cancelleddraft");
    expect(panel.shadowRoot.getElementById("message-list")?.textContent).not.toContain("Run cancelled");
    expect(panel._conversationTurns.map((turn) => turn.prompt)).toEqual(["Active original prompt", "Kept draft after edit"]);
    expect(panel._events).toContainEqual(cancelled);
    expect(panel._selectedThreadId).toBe("thread-one");
    expect(panel._activeThread.active_run_id).toBe("run-active");
    expect(panel._runActivityForThread()).toMatchObject({ runId: "run-active", busy: true });
  });
});
