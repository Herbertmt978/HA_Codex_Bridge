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

  it("keeps the visible composer action, accessible name, and tooltip in sync", () => {
    const panel = createPanel();
    const send = panel.shadowRoot.getElementById("send-button");

    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Send");
    expect(send.getAttribute("aria-label")).toBe("Send");
    expect(send.title).toBe("Send message to Codex");
    expect(send.dataset.tooltip).toBe("Send message to Codex");

    panel._activeThread = { ...panel._activeThread, status: "running", active_run_id: "run-one" };
    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Steer");
    expect(send.getAttribute("aria-label")).toBe("Steer");
    expect(send.title).toMatch(/queue steering/i);
    expect(send.dataset.tooltip).toBe(send.title);

    panel._promptMutation = {
      threadId: "thread-alpha",
      state: "retryable",
      prompt: "Retry this",
      clientRequestId: "request-one",
    };
    panel._renderComposerState(panel._activeThread);
    expect(send.textContent).toContain("Retry");
    expect(send.getAttribute("aria-label")).toBe("Retry");
    expect(send.title).toMatch(/retry this message safely/i);
    expect(send.dataset.tooltip).toBe(send.title);
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
