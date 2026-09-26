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
