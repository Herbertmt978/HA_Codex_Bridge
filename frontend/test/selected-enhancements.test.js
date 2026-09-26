/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import transcriptSearchRequests from "../../tests/fixtures/transcript_search_requests.json";

import "../src/codex-bridge-panel.js";

function makePanel({ capabilities = [], status = "idle" } = {}) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  const thread = {
    thread_id: "thread-one", project_id: "project-one", title: "Working chat",
    status, active_run_id: status === "running" ? "run-one" : null,
    mode: "edit", attachments: [], pending_prompts: [],
  };
  panel._config = { api_version: 1, connection_type: "supervisor", capabilities };
  panel._status = { auth: { state: "ok", auth_required: false }, account: { available: true, auth_mode: "chatgpt" } };
  panel._projects = [{ project_id: thread.project_id, kind: "project", name: "Project", archived_at: null }];
  panel._threads = [thread];
  panel._selectedProjectId = thread.project_id;
  panel._selectedThreadId = thread.thread_id;
  panel._activeThread = thread;
  panel._events = [];
  panel._callWS = vi.fn(async () => ({}));
  panel._forceMessageRebuild = true;
  panel._render(true);
  return panel;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe("selected enhancements panel behaviour", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("renders assistant Markdown while keeping user text and assistant HTML inert", () => {
    const panel = makePanel();
    const assistant = panel._renderMessage("assistant", "# Answer\n\n<script>window.pwned = true</script>", 1);
    const user = panel._renderMessage("user", "# Keep this plain\n\n**not formatting**", 2);

    expect(assistant.querySelector(".bubble h1")?.textContent).toBe("Answer");
    expect(assistant.querySelector(".bubble script, .bubble [onerror]")).toBeNull();
    expect(assistant.querySelector(".bubble").textContent).toContain("<script>window.pwned = true</script>");
    expect(user.querySelector(".bubble h1, .bubble strong")).toBeNull();
    expect(user.querySelector(".bubble-text")?.textContent).toContain("# Keep this plain");
  });

  it("discards a late transcript response and includes archived matches when selected", async () => {
    const panel = makePanel({ capabilities: ["transcript_search_v1"] });
    const older = deferred();
    const newer = deferred();
    panel._callWS.mockImplementation((_method, args) => args.query === "older" ? older.promise : newer.promise);
    panel._searchQuery = "older";
    const oldSearch = panel._searchTranscript();
    panel._searchQuery = "newer";
    panel._searchArchived = true;
    const newSearch = panel._searchTranscript();

    expect(panel._callWS).toHaveBeenNthCalledWith(2, "search_transcript", transcriptSearchRequests.initial);
    newer.resolve({ results: [{ thread_id: "thread-archived", sequence: 7, title: "Old chat", archived_at: "2026-09-01", excerpt: "matching text" }] });
    await newSearch;
    older.resolve({ results: [{ thread_id: "thread-old", sequence: 2, title: "Stale result", excerpt: "older match" }] });
    await oldSearch;

    expect(panel.shadowRoot.getElementById("transcript-search-results").textContent).toContain("Old chat (archived)");
    expect(panel.shadowRoot.getElementById("transcript-search-results").textContent).not.toContain("Stale result");
  });

  it("uses the next cursor for More matching messages and appends the next page", async () => {
    const panel = makePanel({ capabilities: ["transcript_search_v1"] });
    panel._searchQuery = "needle";
    panel._transcriptSearchResults = [{ thread_id: "thread-one", sequence: 1, title: "Chat", excerpt: "first" }];
    panel._transcriptSearchCursor = 700;
    panel._transcriptSearchHasMore = true;
    panel._renderTranscriptSearch();
    const page = deferred();
    panel._callWS.mockReturnValue(page.promise);

    const more = panel.shadowRoot.getElementById("transcript-search-more");
    expect(more.hidden).toBe(false);
    more.click();

    expect(panel._callWS).toHaveBeenCalledWith("search_transcript", transcriptSearchRequests.pagination);
    page.resolve({ results: [{ thread_id: "thread-one", sequence: 2, title: "Chat", excerpt: "second" }], next_cursor: 600, has_more: false });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(panel._transcriptSearchCursor).toBe(600);
    expect(panel._transcriptSearchResults.map((item) => item.excerpt)).toEqual(["first", "second"]);
    expect(panel.shadowRoot.getElementById("transcript-search-more").hidden).toBe(true);
  });

  it("loads a compacted archived search match and focuses the rendered message", async () => {
    const panel = makePanel({ capabilities: ["transcript_search_v1"] });
    panel._transcriptSearchResults = [{
      thread_id: "archived-thread", sequence: 7, title: "Archived chat",
      archived_at: "2026-09-01", excerpt: "matching text",
    }];
    panel._renderTranscriptSearch();
    panel._selectThread = vi.fn(async (threadId) => {
      panel._selectedThreadId = threadId;
    });
    panel._callWS.mockResolvedValue({ role: "assistant", text: "# Matching answer", timestamp: "2026-09-01T10:00:00Z" });
    const result = panel.shadowRoot.querySelector('[data-action="open-search-result"]');

    await panel._openTranscriptSearchResult(result);

    expect(panel._selectThread).toHaveBeenCalledWith("archived-thread");
    expect(panel._callWS).toHaveBeenCalledWith("get_transcript_message", { thread_id: "archived-thread", sequence: 7 });
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="7"] h1')?.textContent).toBe("Matching answer");
    expect(panel.shadowRoot.activeElement).toBe(panel.shadowRoot.querySelector('#message-list [data-sequence="7"]'));
  });

  it("retains the chosen queue mode on a safe prompt retry", async () => {
    const panel = makePanel({ capabilities: ["prompt_queue_v1"], status: "running" });
    panel._followUpMode = "queue";
    panel.shadowRoot.getElementById("prompt-input").value = "Follow up after this run";
    panel._callWS.mockRejectedValueOnce(new Error("connection dropped")).mockResolvedValueOnce({});
    panel._refreshActiveThread = vi.fn().mockRejectedValueOnce(new Error("snapshot unavailable")).mockResolvedValueOnce({});

    await panel._sendPrompt();
    expect(panel._promptMutations.get("thread-one").state).toBe("retryable");
    panel._followUpMode = "steer";
    await panel._sendPrompt();

    expect(panel._callWS.mock.calls.filter(([method]) => method === "send_prompt").map(([, args]) => args.follow_up_mode))
      .toEqual(["queue", "queue"]);
    expect(panel._callWS.mock.calls.filter(([method]) => method === "send_prompt").map(([, args]) => args.client_request_id))
      .toHaveLength(2);
    expect(panel._callWS.mock.calls[0][1].client_request_id).toBe(panel._callWS.mock.calls[1][1].client_request_id);
  });

  it("preserves the editable draft when queueing is required for a collaboration mode", async () => {
    const panel = makePanel({ capabilities: ["prompt_queue_v1", "plan_mode_v1"], status: "running" });
    panel._followUpMode = "steer";
    panel._collaborationMode = "plan";
    panel._collaborationChoices.set("thread-one", "plan");
    panel.shadowRoot.getElementById("prompt-input").value = "Keep this plan request editable";
    const error = Object.assign(new Error("Plan mode requires queueing while a turn is active."), { code: "collaboration_mode_requires_queue" });
    panel._callWS.mockRejectedValueOnce(error);

    await panel._sendPrompt();

    const input = panel.shadowRoot.getElementById("prompt-input");
    expect(panel._promptMutations.has("thread-one")).toBe(false);
    expect(input.value).toBe("Keep this plan request editable");
    expect(input.disabled).toBe(false);
    expect(panel.shadowRoot.getElementById("send-button").textContent).not.toContain("Retry");
    expect(panel.shadowRoot.getElementById("error-strip").textContent).toContain("Plan mode requires queueing");
  });

  it("keeps native Plan selected when a refreshed thread snapshot advertises Plan", async () => {
    const panel = makePanel({ capabilities: ["plan_mode_v1"] });
    const refreshedThread = { ...panel._activeThread, collaboration_mode: "plan" };
    panel._callWS.mockImplementation(async (method) => {
      if (method === "get_thread") return refreshedThread;
      if (method === "list_artifacts") return [];
      return {};
    });
    panel._loadThreadEventHistory = vi.fn().mockResolvedValue([]);
    panel._listPendingInteractions = vi.fn().mockResolvedValue([]);
    panel._startEventSubscription = vi.fn();
    panel._render = vi.fn();

    await panel._refreshActiveThread();
    panel._renderComposerState(panel._activeThread);

    const selector = panel.shadowRoot.getElementById("collaboration-mode");
    expect(panel._activeThread.collaboration_mode).toBe("plan");
    expect(selector.querySelector('option[value="plan"]').disabled).toBe(false);
    expect(selector.value).toBe("plan");
  });

  it("keeps Plan disabled for an older App without plan_mode_v1", () => {
    const panel = makePanel();
    panel._activeThread.collaboration_mode = "plan";

    panel._renderComposerState(panel._activeThread);

    expect(panel.shadowRoot.querySelector('#collaboration-mode option[value="plan"]').disabled).toBe(true);
    expect(panel.shadowRoot.getElementById("implement-plan-button").hidden).toBe(true);
    expect(panel.shadowRoot.getElementById("plan-availability").hidden).toBe(false);
  });

  it("implements a reviewed Plan using default collaboration while retaining thread permissions and model", async () => {
    const panel = makePanel({ capabilities: ["plan_mode_v1"] });
    panel._activeThread = {
      ...panel._activeThread, collaboration_mode: "plan", mode: "read-only",
      effective_model: "gpt-5.6", effective_thinking_level: "high",
    };
    panel._threads = [{ ...panel._activeThread }];
    panel._renderComposerState(panel._activeThread);
    panel._refreshActiveThread = vi.fn().mockResolvedValue(panel._activeThread);

    await panel._implementReviewedPlan();

    const [, request] = panel._callWS.mock.calls.find(([method]) => method === "send_prompt");
    expect(request).toMatchObject({
      thread_id: "thread-one", collaboration_mode: "default",
      prompt: "Implement the reviewed plan within this chat's selected workspace and permissions.",
    });
    expect(request).not.toHaveProperty("mode");
    expect(request).not.toHaveProperty("model");
    expect(panel._callWS.mock.calls.some(([method]) => method === "update_thread")).toBe(false);
    expect(panel._activeThread).toMatchObject({ mode: "read-only", effective_model: "gpt-5.6", effective_thinking_level: "high" });
    expect(panel._threads[0]).toMatchObject({ mode: "read-only", effective_model: "gpt-5.6", effective_thinking_level: "high" });
  });

  it("updates and removes queued prompts with their revision and edited text", async () => {
    const panel = makePanel({ capabilities: ["prompt_queue_v1"] });
    panel._promptQueues.set("thread-one", [{ run_id: "queued-run", revision: 4, prompt: "Original" }]);
    panel._refreshActiveThread = vi.fn().mockResolvedValue(panel._activeThread);
    panel._loadPromptQueue = vi.fn().mockResolvedValue(undefined);
    panel._renderPromptQueue(panel._activeThread);
    const section = panel.shadowRoot.getElementById("prompt-queue");
    section.querySelector("textarea").value = "Edited prompt";
    await panel._mutateQueuedPrompt(section.querySelector('[data-action="save-queued-prompt"]'), false);
    expect(panel._callWS).toHaveBeenNthCalledWith(1, "update_queued_prompt", {
      thread_id: "thread-one", run_id: "queued-run", expected_revision: 4, prompt: "Edited prompt",
    });

    panel._renderPromptQueue(panel._activeThread);
    await panel._mutateQueuedPrompt(section.querySelector('[data-action="cancel-queued-prompt"]'), true);
    expect(panel._callWS).toHaveBeenNthCalledWith(2, "cancel_queued_prompt", {
      thread_id: "thread-one", run_id: "queued-run", expected_revision: 4,
    });
  });

  it("applies a live queued prompt edit at its original message sequence and timeline preview", () => {
    const panel = makePanel();
    panel._events = [
      { sequence: 1, event_type: "message.created", payload: { run_id: "queued-run", role: "user", text: "Draft A" } },
      { sequence: 2, event_type: "run.queued", payload: { run_id: "queued-run" } },
    ];
    panel._forceMessageRebuild = true;

    panel._renderMessages();
    expect(panel.shadowRoot.querySelectorAll("#message-list .message.user")).toHaveLength(1);
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="1"]')?.textContent).toContain("Draft A");
    expect(panel._conversationTurns[0]).toMatchObject({ anchorSequence: 1, prompt: "Draft A" });

    panel._timelinePreviewSequence = "1";
    panel._events = [
      ...panel._events,
      { sequence: 3, event_type: "run.queue_item_updated", payload: { run_id: "queued-run", prompt: "Draft B", revision: 2 } },
      { sequence: 4, event_type: "message.updated", payload: { run_id: "queued-run", message_sequence: 1, role: "user", text: "Draft B" } },
    ];

    // Event acceptance rebuilds message DOM after message.updated; unchanged
    // prior event object references make this an incremental live projection.
    panel._forceMessageRebuild = true;
    panel._renderMessages();

    const userMessages = panel.shadowRoot.querySelectorAll("#message-list .message.user");
    expect(userMessages).toHaveLength(1);
    expect(userMessages[0].dataset.sequence).toBe("1");
    expect(userMessages[0].textContent).toContain("Draft B");
    expect(userMessages[0].textContent).not.toContain("Draft A");
    expect(panel._conversationTurns).toHaveLength(1);
    expect(panel._conversationTurns[0]).toMatchObject({ anchorSequence: 1, prompt: "Draft B" });
    expect(panel.shadowRoot.querySelector("#conversation-timeline-desktop-preview .timeline-preview-title")?.textContent).toBe("Draft B");
    expect(panel.shadowRoot.getElementById("conversation-timeline-desktop-preview").textContent).not.toContain("Draft A");
  });

  it("removes a cancelled queued prompt's original user article and rail preview", () => {
    const panel = makePanel();
    panel._events = [
      { sequence: 1, event_type: "message.created", payload: { run_id: "queued-run", role: "user", text: "Remove this queued draft" } },
      { sequence: 2, event_type: "run.queued", payload: { run_id: "queued-run" } },
    ];
    panel._forceMessageRebuild = true;
    panel._renderMessages();
    expect(panel.shadowRoot.querySelectorAll("#message-list .message.user")).toHaveLength(1);
    expect(panel._conversationTurns).toHaveLength(1);

    panel._events = [
      ...panel._events,
      { sequence: 3, event_type: "message.removed", payload: { run_id: "queued-run", message_sequence: 1, role: "user" } },
      { sequence: 4, event_type: "run.queue_cleared", payload: { run_id: "queued-run" } },
    ];
    panel._forceMessageRebuild = true;
    panel._renderMessages();

    expect(panel.shadowRoot.querySelectorAll("#message-list .message.user")).toHaveLength(0);
    expect(panel.shadowRoot.querySelector('#message-list [data-sequence="1"]')).toBeNull();
    expect(panel._conversationTurns).toHaveLength(0);
    expect(panel.shadowRoot.getElementById("conversation-timeline").hidden).toBe(true);
  });

  it("does not call optional transcript, queue or Git methods when an older App lacks capabilities", async () => {
    const panel = makePanel();
    panel._searchQuery = "search this";
    panel._promptQueues.set("thread-one", [{ run_id: "queued", prompt: "pending" }]);
    panel._renderPromptQueue(panel._activeThread);

    await panel._searchTranscript();
    await panel._loadGitReview();

    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel.shadowRoot.getElementById("transcript-search").hidden).toBe(true);
    expect(panel.shadowRoot.getElementById("prompt-queue").hidden).toBe(true);
    expect(panel.shadowRoot.getElementById("git-review-button").hidden).toBe(true);
  });

  it("loads a large Git diff only on demand and labels truncated results", async () => {
    const panel = makePanel({ capabilities: ["git_review_v1"] });
    panel._callWS
      .mockResolvedValueOnce({ files: [{ path: "src/large.js", status: "modified", patch_truncated: true }], files_truncated: true, state_token: "state-one" })
      .mockResolvedValueOnce({ files: [{ path: "src/large.js", patch: "@@ -1 +1 @@\n-old\n+new", patch_truncated: true }] });

    await panel._loadGitReview();
    const results = panel.shadowRoot.getElementById("git-review-results");
    expect(results.querySelector("pre")).toBeNull();
    expect(results.textContent).toContain("File list truncated");
    const load = results.querySelector('[data-action="load-git-file"]');
    await panel._loadGitFile(load);

    expect(panel._callWS).toHaveBeenNthCalledWith(2, "git_review", {
      thread_id: "thread-one", scope: "unstaged", path: "src/large.js", expected_state_token: "state-one",
    });
    expect(results.querySelector("pre.git-diff")?.textContent).toContain("+new");
    expect(results.textContent).toContain("Diff truncated");
  });

  it("discards a lazy Git file response after switching chats", async () => {
    const panel = makePanel({ capabilities: ["git_review_v1"] });
    panel._callWS.mockResolvedValueOnce({ files: [{ path: "src/app.js", status: "modified" }] });
    await panel._loadGitReview();
    const load = panel.shadowRoot.querySelector('[data-action="load-git-file"]');
    const pending = deferred();
    panel._callWS.mockReturnValueOnce(pending.promise);

    const loading = panel._loadGitFile(load);
    panel._selectedThreadId = "thread-two";
    pending.resolve({ files: [{ path: "src/app.js", patch: "stale patch" }] });
    await loading;

    expect(panel.shadowRoot.getElementById("git-review-results").textContent).not.toContain("stale patch");
  });
});
