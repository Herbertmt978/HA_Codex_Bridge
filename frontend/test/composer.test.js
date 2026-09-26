/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = {
    api_version: 1,
    connection_type: "supervisor",
    panel_title: "Codex Bridge",
  };
  panel._status = {
    auth: { state: "ok", auth_required: false },
    account: { available: true, auth_mode: "chatgpt", plan_type: "plus" },
    diagnostics: { app_version: "0.6.0", bridge_version: "0.6.0", active_codex_version: "1.2.3" },
    model_catalog: {
      models: [{ model: "gpt-5.6", display_name: "GPT-5.6", thinking_levels: ["medium"] }],
      default_model: "gpt-5.6",
      default_thinking_level: "medium",
    },
    limits: { available: true },
  };
  panel._selectedThreadId = "thread-alpha";
  panel._activeThread = {
    thread_id: "thread-alpha",
    title: "Composer test",
    status: "idle",
    mode: "edit",
    attachments: [],
  };
  return panel;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("prompt composer mutation contract", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps keyboard guidance accessible without a persistent idle status row", () => {
    const panel = createPanel();
    panel._render(true);

    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const status = panel.shadowRoot.getElementById("composer-status");
    const hint = panel.shadowRoot.getElementById("composer-shortcut-hint");
    expect(prompt.getAttribute("aria-describedby")).toContain("composer-shortcut-hint");
    expect(hint.textContent).toBe("Enter sends; Shift+Enter adds a new line.");
    expect(status.textContent).toBe("");
  });

  it("enables the send action as soon as the user enters a prompt", () => {
    const panel = createPanel();
    panel._render(true);

    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const send = panel.shadowRoot.getElementById("send-button");
    expect(send.disabled).toBe(true);

    prompt.value = "Continue this chat";
    prompt.dispatchEvent(new Event("input", { bubbles: true, composed: true }));

    expect(panel._draftForThread("thread-alpha")).toBe("Continue this chat");
    expect(send.disabled).toBe(false);

    prompt.value = "   ";
    prompt.dispatchEvent(new Event("input", { bubbles: true, composed: true }));

    expect(send.disabled).toBe(true);
  });

  it("explains Assist-managed messages and blocks ordinary sends without creating a mutation", async () => {
    const panel = createPanel();
    panel._activeThread.schedule_eligible = false;
    panel._setDraftForThread("thread-alpha", "Keep my draft");
    panel._callWS = vi.fn();
    panel._render(true);
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const send = panel.shadowRoot.getElementById("send-button");

    expect(prompt.value).toBe("Keep my draft");
    expect(prompt.disabled).toBe(true);
    expect(send.disabled).toBe(true);
    const notice = panel.shadowRoot.getElementById("assist-conversation-notice");
    expect(notice.hidden).toBe(false);
    expect(notice.textContent).toMatch(/Home Assistant conversation.*managed by Home Assistant Assist.*cannot be messaged here.*start a new chat/s);
    expect(prompt.getAttribute("aria-describedby")).toContain("assist-conversation-notice");
    expect(panel.shadowRoot.getElementById("composer-status").textContent).toBe("");
    await panel._sendPrompt();
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel._promptMutations.size).toBe(0);
    expect(panel._promptMutation).toBeNull();
    expect(prompt.value).toBe("Keep my draft");
    expect(panel._errorRetryable).toBe(false);
    expect(panel.shadowRoot.querySelector(".composer-shell").classList).not.toContain("retry-ready");
  });

  it("keeps Stop available in an Assist-managed running conversation", () => {
    const panel = createPanel();
    panel._activeThread = { ...panel._activeThread, schedule_eligible: false, status: "running" };
    panel._setDraftForThread("thread-alpha", "Saved draft");
    panel._render(true);
    const send = panel.shadowRoot.getElementById("send-button");
    expect(send.disabled).toBe(false);
    expect(send.dataset.action).toBe("stop-run");
    expect(send.getAttribute("aria-label")).toBe("Stop");
    expect(panel.shadowRoot.getElementById("prompt-input").disabled).toBe(true);
  });

  it("removes the Assist banner when a regular chat is selected without losing its draft", () => {
    const panel = createPanel();
    panel._activeThread.schedule_eligible = false;
    panel._render(true);
    const notice = panel.shadowRoot.getElementById("assist-conversation-notice");
    expect(notice.hidden).toBe(false);
    panel._activeThread = { ...panel._activeThread, schedule_eligible: true };
    panel._setDraftForThread("thread-alpha", "Regular draft");
    panel._render(true);
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    expect(notice.hidden).toBe(true);
    expect(prompt.value).toBe("Regular draft");
    expect(prompt.disabled).toBe(false);
    expect(prompt.getAttribute("aria-describedby")).not.toContain("assist-conversation-notice");
  });

  it.each([undefined, true, "false"])("does not infer Assist ownership from schedule_eligible %s", (eligible) => {
    const panel = createPanel();
    panel._activeThread.schedule_eligible = eligible;
    panel._setDraftForThread("thread-alpha", "Regular message");
    panel._render(true);
    expect(panel.shadowRoot.getElementById("prompt-input").disabled).toBe(false);
    expect(panel.shadowRoot.getElementById("send-button").disabled).toBe(false);
  });

  it("stops local dictation and rejects a stale enabled control when Assist ownership arrives", () => {
    const instances = [];
    class Recognition {
      constructor() { instances.push(this); }
      start = vi.fn();
      abort = vi.fn();
    }
    Recognition.prototype.processLocally = false;
    vi.stubGlobal("SpeechRecognition", Recognition);
    const panel = createPanel();
    panel._render(true);
    panel._toggleDictation();
    expect(instances[0].start).toHaveBeenCalledOnce();
    panel._activeThread.schedule_eligible = false;
    panel._renderComposerState(panel._activeThread);
    expect(instances[0].abort).toHaveBeenCalledOnce();
    expect(panel.shadowRoot.getElementById("dictation-button").disabled).toBe(true);
    panel.shadowRoot.getElementById("prompt-input").disabled = false;
    panel._toggleDictation();
    expect(instances).toHaveLength(1);
  });

  it("keeps the visible composer action, accessible name, and tooltip in sync", () => {
    const panel = createPanel();
    const send = panel.shadowRoot.getElementById("send-button");

    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Send");
    expect(send.getAttribute("aria-label")).toBe("Send");
    expect(send.hasAttribute("title")).toBe(false);
    expect(send.dataset.tooltip).toBe("Send message to Codex");

    panel._activeThread = { ...panel._activeThread, status: "running", active_run_id: "run-one" };
    panel._renderComposerState(panel._activeThread);
    expect(send.getAttribute("aria-label")).toBe("Stop");
    expect(send.disabled).toBe(false);
    panel._setDraftForThread("thread-alpha", "Use the smaller fix");
    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Steer");
    expect(send.getAttribute("aria-label")).toBe("Steer");
    expect(send.dataset.tooltip).toMatch(/steer the running/i);
    expect(send.hasAttribute("title")).toBe(false);

    panel._promptMutation = {
      threadId: "thread-alpha",
      state: "retryable",
      prompt: "Retry this",
      clientRequestId: "request-one",
    };
    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Retry");
    expect(send.getAttribute("aria-label")).toBe("Retry");
    expect(send.dataset.tooltip).toMatch(/retry this message safely/i);
    expect(send.hasAttribute("title")).toBe(false);
  });

  it("offers Stop with an empty running composer, and both Steer and Stop with a draft", async () => {
    const panel = createPanel();
    panel._activeThread.status = "running";
    panel._render(true);
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const send = panel.shadowRoot.getElementById("send-button");
    const stop = panel.shadowRoot.getElementById("stop-run-button");
    const pending = deferred();
    panel._callWS = vi.fn(() => pending.promise);
    panel._refreshActiveThread = vi.fn().mockResolvedValue(undefined);
    expect(send.dataset.action).toBe("stop-run");
    expect(stop.classList.contains("hidden")).toBe(true);
    prompt.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(panel._callWS).not.toHaveBeenCalled();
    prompt.value = "Take a different approach";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    expect(send.getAttribute("aria-label")).toBe("Steer");
    expect(stop.classList.contains("hidden")).toBe(false);
    stop.click();
    stop.click();
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    expect(panel._callWS).toHaveBeenCalledWith("cancel_run", { thread_id: "thread-alpha" });
    expect(send.disabled).toBe(true);
    expect(prompt.value).toBe("Take a different approach");
    pending.resolve({ status: "cancelling" });
    await Promise.resolve();
  });

  it("updates the context ring from thread events without confusing account limits", () => {
    const panel = createPanel();
    panel._render(true);
    const button = panel.shadowRoot.getElementById("context-usage-button");
    expect(button.getAttribute("aria-label")).toMatch(/not reported yet/);
    expect(button.hidden).toBe(true);
    for (const [sequence, used] of [[1, 800], [2, 200]]) {
      panel._handleSubscribedEvent("thread-alpha", {
        event_id: `context-${sequence}`, thread_id: "thread-alpha", sequence,
        event_type: "context.updated", payload: { context_usage: { used_tokens: used, context_window: 1000 } },
      });
      expect(button.getAttribute("aria-label")).toContain(`${used / 10}% of context used`);
      expect(button.hidden).toBe(false);
      expect(button.querySelector(".context-fill").getAttribute("stroke-dasharray")).toBe(`${used / 10} 100`);
    }
    button.click();
    expect(panel._sideTab).toBe("usage");
    expect(panel.shadowRoot.getElementById("usage-panel").textContent).toContain("200 of 1,000 tokens");
    panel._activeThread = { ...panel._activeThread, thread_id: "other", context_usage: null };
    panel._selectedThreadId = "other";
    panel._render(true);
    expect(button.getAttribute("aria-label")).toMatch(/not reported yet/);
    expect(button.hidden).toBe(true);
  });

  it("dictates locally into the active draft without sending, and stops when the chat changes", async () => {
    const instances = [];
    class Recognition {
      static available = vi.fn();
      constructor() { instances.push(this); }
      start = vi.fn();
      stop = vi.fn();
      abort = vi.fn();
    }
    Recognition.prototype.processLocally = false;
    vi.stubGlobal("SpeechRecognition", Recognition);
    const panel = createPanel();
    panel._render(true);
    panel._callWS = vi.fn();
    const button = panel.shadowRoot.getElementById("dictation-button");
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    expect(button.hidden).toBe(false);
    expect(Recognition.available).not.toHaveBeenCalled();
    button.click();
    await vi.waitFor(() => expect(instances[0].start).toHaveBeenCalledOnce());
    expect(instances[0].processLocally).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    instances[0].onresult({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: "Inspect the workspace" } }],
    });
    expect(prompt.value).toBe("Inspect the workspace");
    expect(panel._draftForThread("thread-alpha")).toBe(prompt.value);
    expect(panel._callWS).not.toHaveBeenCalled();
    button.click();
    expect(instances[0].stop).toHaveBeenCalledOnce();
    instances[0].onend();
    button.click();
    await vi.waitFor(() => expect(instances).toHaveLength(2));
    panel._setSelectedThreadId("thread-beta");
    expect(instances[1].abort).toHaveBeenCalledOnce();
  });

  it("hides dictation without a local-only recogniser, without querying a hosted service", () => {
    const instances = [];
    class Recognition {
      static available = vi.fn();
      constructor() { instances.push(this); }
      start = vi.fn();
    }
    vi.stubGlobal("SpeechRecognition", Recognition);
    const panel = createPanel();
    panel._render(true);
    const button = panel.shadowRoot.getElementById("dictation-button");
    expect(button.hidden).toBe(true);
    expect(Recognition.available).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
  });

  it("locks the composer before awaiting the Bridge and sends one stable request id", async () => {
    const panel = createPanel();
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const send = panel.shadowRoot.getElementById("send-button");
    const pending = deferred();
    panel._refreshActiveThread = vi.fn().mockImplementation(async () => panel._render());
    panel._callWS = vi.fn((action) => action === "send_prompt" ? pending.promise : Promise.resolve([]));
    prompt.value = "Inspect the workspace";

    const first = panel._sendPrompt();

    expect(panel._callWS).toHaveBeenCalledWith("send_prompt", {
      thread_id: "thread-alpha",
      prompt: "Inspect the workspace",
      client_request_id: expect.stringMatching(/^[A-Za-z0-9_.:-]{1,256}$/),
    });
    expect(prompt.disabled).toBe(true);
    expect(send.disabled).toBe(true);

    await panel._sendPrompt();
    expect(panel._callWS.mock.calls.filter(([action]) => action === "send_prompt")).toHaveLength(1);

    pending.resolve({ accepted: true });
    await first;
    expect(prompt.value).toBe("");
    expect(panel._promptMutation).toBeNull();
  });

  it("keeps a successful prompt refresh healthy when artifacts are temporarily unavailable", async () => {
    const panel = createPanel();
    const prompt = panel.shadowRoot.getElementById("prompt-input");
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
      if (action === "send_prompt") return Promise.resolve({ accepted: true });
      if (action === "get_thread") {
        return Promise.resolve({ ...panel._activeThread, status: "running" });
      }
      if (action === "get_events") {
        return Promise.resolve([{
          event_id: "event-prompt-success",
          sequence: 1,
          thread_id: "thread-alpha",
          event_type: "message.created",
          payload: { text: "Inspect the workspace" },
          timestamp: "2026-07-15T12:00:00Z",
        }]);
      }
      if (action === "list_artifacts") {
        return Promise.reject(Object.assign(new Error("Artifacts are reserved"), {
          code: "reservation_conflict",
        }));
      }
      if (action === "get_status") return Promise.resolve(panel._status);
      throw new Error(`Unexpected action: ${action}`);
    });
    prompt.value = "Inspect the workspace";

    await panel._sendPrompt();

    expect(panel._activeThread?.status).toBe("running");
    expect(panel._events).toHaveLength(1);
    expect(panel._artifacts).toEqual(previousArtifacts);
    expect(panel._error).toBe("");
    expect(panel.shadowRoot.getElementById("error-strip").classList).not.toContain("visible");
  });

  it("reuses the same request id after an uncertain response and clears on a matching event", async () => {
    const panel = createPanel();
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const attempts = [];
    panel._refreshActiveThread = vi.fn().mockResolvedValue(undefined);
    panel._callWS = vi.fn((action, payload) => {
      if (action !== "send_prompt") return Promise.resolve([]);
      attempts.push(payload);
      return Promise.reject(new Error("Bridge response was lost"));
    });
    prompt.value = "Run the focused tests";

    await panel._sendPrompt();
    expect(attempts).toHaveLength(1);
    expect(panel._promptMutation).toBeTruthy();
    const requestId = attempts[0].client_request_id;
    expect(requestId).toBeTruthy();

    prompt.value = "Run the focused tests";
    await panel._sendPrompt();
    expect(attempts).toHaveLength(2);
    expect(attempts[1].client_request_id).toBe(requestId);

    panel._handleSubscribedEvent("thread-alpha", {
      event_id: "event-prompt-1",
      sequence: 1,
      thread_id: "thread-alpha",
      event_type: "message.created",
      payload: { text: "Run the focused tests", client_request_id: requestId },
    });
    expect(panel._promptMutation).toBeNull();
  });

  it.each([
    { code: "assist_policy_invalid", message: "private-policy-detail" },
    { body: { error: { code: "assist_policy_invalid", message: "private-policy-detail" } } },
  ])("preserves the draft without an uncertain retry after a definite Assist policy rejection", async (error) => {
    const panel = createPanel();
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    panel._callWS = vi.fn().mockRejectedValue(error);
    panel._refreshActiveThread = vi.fn().mockImplementation(async () => {
      panel._activeThread.schedule_eligible = false;
    });
    prompt.value = "Keep this draft";

    await panel._sendPrompt();

    expect(panel._promptMutation).toBeNull();
    expect(panel._promptMutations.size).toBe(0);
    expect(panel._draftForThread("thread-alpha")).toBe("Keep this draft");
    expect(prompt.value).toBe("Keep this draft");
    expect(panel._error).toMatch(/managed by Home Assistant Assist/);
    expect(panel._error).not.toMatch(/interrupted|private-policy-detail/);
    expect(panel._errorRetryable).toBe(false);
    expect(panel.shadowRoot.querySelector(".composer-shell").classList).not.toContain("retry-ready");
    expect(panel.shadowRoot.getElementById("send-button").disabled).toBe(true);
    await panel._sendPrompt();
    expect(panel._callWS).toHaveBeenCalledTimes(1);
  });

  it("retains the same request id for a generic Bridge rejection whose outcome is unknown", async () => {
    const panel = createPanel();
    panel._refreshActiveThread = vi.fn().mockResolvedValue(undefined);
    panel._callWS = vi.fn().mockRejectedValue({ code: "bridge_error", message: "Bridge request failed" });
    panel.shadowRoot.getElementById("prompt-input").value = "Retain my request";
    await panel._sendPrompt();
    const requestId = panel._promptMutation.clientRequestId;
    expect(panel._promptMutation.state).toBe("retryable");
    await panel._sendPrompt();
    expect(panel._callWS.mock.calls[1][1].client_request_id).toBe(requestId);
  });

  it("retains an uncertain prompt per chat so retrying after A-to-B-to-A uses its original request id", async () => {
    const panel = createPanel();
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    const send = panel.shadowRoot.getElementById("send-button");
    const attempts = [];
    panel._refreshActiveThread = vi.fn().mockImplementation(async () => {
      panel._activeThread = {
        thread_id: panel._selectedThreadId,
        title: "Composer test",
        status: "idle",
        mode: "edit",
        attachments: [],
      };
      panel._render();
    });
    panel._callWS = vi.fn((action, payload) => {
      if (action !== "send_prompt") return Promise.resolve([]);
      attempts.push(payload);
      return Promise.reject(new Error("Bridge response was lost"));
    });
    prompt.value = "Keep this request id";

    await panel._sendPrompt();
    const requestId = attempts[0].client_request_id;
    expect(panel._promptMutation?.state).toBe("retryable");

    await panel._selectThread("thread-beta");
    expect(panel._promptMutation).toBeNull();
    expect(prompt.value).toBe("");
    expect(prompt.disabled).toBe(false);
    expect(send.disabled).toBe(true);
    await panel._selectThread("thread-alpha");
    expect(panel._promptMutation).toMatchObject({
      threadId: "thread-alpha",
      clientRequestId: requestId,
      state: "retryable",
    });
    expect(prompt.value).toBe("Keep this request id");

    await panel._sendPrompt();
    expect(attempts).toHaveLength(2);
    expect(attempts[1].client_request_id).toBe(requestId);
  });

});
