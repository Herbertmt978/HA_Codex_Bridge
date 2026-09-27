/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const question = {
  interaction_id: "interaction-question-1",
  thread_id: "thread-one",
  kind: "user_input",
  status: "pending",
  expires_at: "2099-07-14T12:05:00Z",
  allowed_actions: ["answer", "cancel"],
  display: {
    title: "Choose the scope",
    summary: "Codex needs an answer before continuing.",
    questions: [{
      question_id: "scope",
      header: "Scope",
      prompt: "Which files should Codex update?",
      options: [
        { label: "Source only", description: "Update source files." },
        { label: "Source and docs", description: "Keep docs aligned." },
      ],
      multiple: false,
      allow_free_text: true,
    }],
  },
};

function makePanel(url, interactions = [question]) {
  window.history.replaceState({}, "", url);
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", capabilities: [] };
  panel._callWS = vi.fn(async (operation) => operation === "list_threads"
    ? [{ thread_id: "thread-one", project_id: "project-one" }]
    : []);
  panel._refreshSelectedThreadAndStartPolling = vi.fn(async () => {
    if (panel._questionDeepLink) panel._questionDeepLink.ready = true;
    panel._pendingInteractions = interactions;
    panel._renderInteractions();
    return true;
  });
  return panel;
}

describe("authenticated question notification links", () => {
  let originalScrollIntoView;

  beforeEach(() => {
    document.body.replaceChildren();
    originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    document.body.replaceChildren();
    window.history.replaceState({}, "", "/");
    if (originalScrollIntoView) HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    else delete HTMLElement.prototype.scrollIntoView;
    vi.restoreAllMocks();
  });

  it("renders the public pending-question envelope when status is omitted", async () => {
    const panel = makePanel("/");
    const publicQuestion = { ...question, event_id: 42, allowed_actions: ["answer"] };
    delete publicQuestion.status;
    panel._callWS = vi.fn(async () => ({ items: [publicQuestion] }));
    const pending = await panel._listPendingInteractions("thread-one");
    expect(pending).toHaveLength(1);
    expect(pending[0].status).toBe("pending");
    panel._selectedThreadId = "thread-one";
    panel._replacePendingInteractions(pending);
    panel._renderInteractions();
    expect(panel.shadowRoot.querySelector(".user-input-card")?.textContent).toContain("Which files should Codex update?");
    expect(panel._callWS).toHaveBeenCalledWith("list_pending_interactions", { thread_id: "thread-one" });
  });

  it.each([null, "answered", "expired", "cancelled"])("rejects an explicitly non-pending status %s", async (status) => {
    const panel = makePanel("/");
    panel._callWS = vi.fn(async () => ({ items: [{ ...question, event_id: 42, status }] }));
    expect(await panel._listPendingInteractions("thread-one")).toEqual([]);
  });

  it.each(["poll", "subscription"])("fetches complete question choices after a redacted %s event", async (transport) => {
    vi.useFakeTimers();
    try {
      const panel = makePanel("/");
      const thread = { thread_id: "thread-one", project_id: "project-one", title: "Plan", status: "waiting_input", mode: "edit", attachments: [] };
      const publicQuestion = { ...question, event_id: 42, allowed_actions: ["answer"] };
      delete publicQuestion.status;
      const event = { event_id: "event-question", thread_id: "thread-one", sequence: 42,
        event_type: "interaction.created", timestamp: "2026-09-26T12:00:00Z",
        payload: { interaction_id: publicQuestion.interaction_id, kind: "user_input",
          display: { ...question.display, questions: [{ ...question.display.questions[0], options: [null, null] }] } } };
      panel._selectedThreadId = "thread-one";
      panel._activeThread = thread;
      panel._status = {};
      panel._lastStatusRefreshAt = Date.now();
      panel._callWS = vi.fn(async (operation) => {
        if (operation === "get_events") return [event];
        if (operation === "list_pending_interactions") return { items: [publicQuestion] };
        if (operation === "get_thread") return thread;
        if (operation === "get_status") return {};
        if (operation === "list_artifacts") return [];
        throw new Error(`Unexpected operation ${operation}`);
      });
      if (transport === "poll") {
        panel._pollActive = true; panel._pollGeneration = 1;
        panel._scheduleNextPoll = vi.fn();
        await panel._runPollTick(1);
      } else {
        panel._handleSubscribedEvent("thread-one", event);
        await vi.advanceTimersByTimeAsync(250);
      }
      const card = panel.shadowRoot.querySelector(".user-input-card");
      expect(card?.textContent).toContain("Source only");
      expect(card?.textContent).toContain("Source and docs");
      expect(panel._pendingInteractions[0].display.questions[0].options).toEqual(question.display.questions[0].options);
      expect(panel._callWS).toHaveBeenCalledWith("list_pending_interactions", { thread_id: "thread-one" });
      expect(panel._callWS.mock.calls.some(([operation]) => operation === "answer_interaction")).toBe(false);
    } finally { vi.useRealTimers(); }
  });

  it("opens the linked chat and focuses its matching pending question once", async () => {
    const panel = makePanel("/?thread=thread-one&interaction=interaction-question-1");

    await panel._loadThreads();

    const card = panel.shadowRoot.querySelector(".user-input-card");
    expect(panel._selectedThreadId).toBe("thread-one");
    expect(card).not.toBeNull();
    expect(panel.shadowRoot.activeElement).toBe(card);
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ block: "start", inline: "nearest" });
    expect(panel._callWS.mock.calls.map(([operation]) => operation)).toEqual(["list_threads"]);
    expect(panel.shadowRoot.querySelector(".question-link-status")).toBeNull();
  });

  it.each([
    ["malformed", "/?thread=thread-one&interaction=bad%2Fid", "This question link is invalid."],
    ["unknown", "/?thread=thread-one&interaction=interaction-missing", "This question is no longer available."],
    ["malformed thread", "/?thread=bad%2Fid&interaction=interaction-question-1", "This question link is invalid."],
    ["unknown thread", "/?thread=thread-missing&interaction=interaction-question-1", "This question is no longer available."],
  ])("keeps the ordinary chat open and reports a %s question link without an action", async (_label, url, message) => {
    const panel = makePanel(url, []);

    await expect(panel._loadThreads()).resolves.toBeUndefined();

    expect(panel._selectedThreadId).toBe("thread-one");
    expect(panel.shadowRoot.querySelector(".question-link-status")?.textContent).toContain(message);
    expect(panel._callWS.mock.calls.map(([operation]) => operation)).toEqual(["list_threads"]);
    expect(panel._interactionMutations.size).toBe(0);
  });

  it("keeps the existing unavailable-chat error for ordinary shared-chat links", async () => {
    const panel = makePanel("/?thread=thread-missing", []);

    await expect(panel._loadThreads()).rejects.toThrow("This shared chat is no longer available");
  });

  it("preserves the answer draft across refresh and avoids refocusing after reconnect", async () => {
    const panel = makePanel("/?thread=thread-one&interaction=interaction-question-1");
    await panel._loadThreads();
    panel._interactionAnswers.set(question.interaction_id, { scope: ["Source only"] });

    panel._replacePendingInteractions([question]);
    panel._renderInteractions();

    expect(panel.shadowRoot.querySelector("input[type='radio']:checked")?.value).toBe("Source only");
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(panel.shadowRoot.activeElement).toBe(panel.shadowRoot.querySelector(".user-input-card"));

    panel._replacePendingInteractions([]);
    panel._questionDeepLink.ready = true;
    panel._renderInteractions();
    expect(panel.shadowRoot.querySelector(".question-link-status")?.textContent).toContain("no longer available");
  });
});
