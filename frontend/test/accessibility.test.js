/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const APPROVAL = {
  interaction_id: "interaction-command-1",
  kind: "command_approval",
  thread_id: "thread-alpha",
  run_id: "run-3",
  turn_id: "turn-7",
  item_id: "item-command-4",
  event_id: 41,
  status: "pending",
  expires_at: "2099-07-13T12:05:00Z",
  display: {
    title: "Run the focused tests",
    summary: "Codex wants to run a command inside this workspace.",
    command: "python -m pytest -q",
    workspace_paths: ["bridge_service/tests"],
  },
  allowed_actions: ["accept", "decline", "cancel"],
};

const QUESTION = {
  interaction_id: "interaction-question-1",
  kind: "user_input",
  thread_id: "thread-alpha",
  run_id: "run-3",
  turn_id: "turn-7",
  item_id: "item-question-3",
  event_id: 42,
  status: "pending",
  expires_at: "2099-07-13T12:05:00Z",
  display: {
    title: "Choose the change scope",
    summary: "Codex needs an answer before it can continue.",
    questions: [{
      question_id: "scope",
      header: "Scope",
      prompt: "Which files should Codex update?",
      options: [{ label: "Source only", description: "Update source files." }],
      multiple: false,
      allow_free_text: false,
    }],
  },
  allowed_actions: ["answer"],
};

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", panel_title: "Codex Bridge" };
  panel._status = {
    auth: { state: "ok", auth_required: false },
    account: { available: true, auth_mode: "chatgpt", plan_type: "plus" },
    diagnostics: { app_version: "0.6.0", bridge_version: "0.6.0", active_codex_version: "1.2.3" },
    model_catalog: { models: [{ model: "gpt-5.6", thinking_levels: ["medium"] }], default_model: "gpt-5.6", default_thinking_level: "medium" },
    limits: { available: true },
  };
  panel._selectedThreadId = "thread-alpha";
  panel._activeThread = { thread_id: "thread-alpha", title: "Accessibility", status: "idle", mode: "observe", attachments: [] };
  return panel;
}

describe("panel accessibility contract", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("exposes named live regions for errors, status, transcript, and interactions", () => {
    const panel = createPanel();
    panel._render(true);

    expect(panel.shadowRoot.getElementById("error-strip").getAttribute("role")).toBe("alert");
    expect(panel.shadowRoot.getElementById("status-banner").getAttribute("role")).toBe("status");
    expect(panel.shadowRoot.getElementById("message-list")).toMatchObject({
      getAttribute: expect.any(Function),
    });
    expect(panel.shadowRoot.getElementById("message-list").getAttribute("aria-live")).toBe("polite");
    const interactionRegion = panel.shadowRoot.getElementById("interaction-region");
    expect(interactionRegion?.getAttribute("aria-live")).toBe("polite");
  });

  it("describes each permission mode and links the selector to that explanation", () => {
    const panel = createPanel();
    panel._showThreadForm = true;
    panel._threadForm = { title: "", mode: "observe", projectId: null };
    panel._renderThreadForm();

    const select = panel.shadowRoot.getElementById("thread-mode-select");
    expect(select).toBeTruthy();
    const describedBy = select.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = [...describedBy.split(/\s+/u)]
      .map((id) => panel.shadowRoot.getElementById(id))
      .filter(Boolean)
      .map((node) => node.textContent)
      .join(" ");
    expect(description).toMatch(/read[- ]only|read only/i);
    expect(description).toMatch(/workspace|files/i);
    expect(description).toMatch(/network/i);
    expect(description).toMatch(/full auto/i);
  });

  it("renders approval and question cards as keyboard-addressable alert dialogs", () => {
    const panel = createPanel();
    panel._pendingInteractions = [APPROVAL, QUESTION];
    panel._render(true);

    const dialogs = [...panel.shadowRoot.querySelectorAll("[role='alertdialog']")];
    expect(dialogs).toHaveLength(2);
    for (const dialog of dialogs) {
      expect(dialog.tabIndex).toBe(-1);
      expect(dialog.getAttribute("aria-modal")).toBe("false");
      expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
      expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    }
    expect(dialogs[0].querySelector("[data-action='accept-interaction']")).toBeTruthy();
    expect(dialogs[1].querySelector("[data-action='answer-interaction']")).toBeTruthy();
  });

  it("uses Escape for an allowed approval cancel and returns focus to the composer", async () => {
    const panel = createPanel();
    panel._pendingInteractions = [APPROVAL];
    panel._refreshActiveThread = vi.fn().mockResolvedValue(undefined);
    panel._callWS = vi.fn().mockResolvedValue({ status: "cancelled" });
    panel._render(true);
    const dialog = panel.shadowRoot.querySelector("[role='alertdialog']");
    const prompt = panel.shadowRoot.getElementById("prompt-input");
    dialog.focus();

    dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await Promise.resolve();

    expect(panel._callWS).toHaveBeenCalledWith("decide_interaction", expect.objectContaining({
      interaction_id: "interaction-command-1",
      decision: "cancel",
      client_request_id: expect.any(String),
    }));
    expect(panel.shadowRoot.activeElement === prompt || document.activeElement === prompt).toBe(true);
  });

  it("keeps an outcome-unknown interaction locked until the authoritative list removes it", async () => {
    const panel = createPanel();
    panel._pendingInteractions = [APPROVAL];
    let decisionCalls = 0;
    panel._callWS = vi.fn(async (action) => {
      if (action === "decide_interaction") {
        decisionCalls += 1;
        const error = new Error("private upstream detail");
        error.code = "interaction_outcome_unknown";
        throw error;
      }
      if (action === "list_pending_interactions") {
        return { items: [APPROVAL], count: 1, thread_id: "thread-alpha" };
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._decideInteraction(APPROVAL.interaction_id, "accept");
    panel._render(true);

    expect(panel._interactionMutations.get(APPROVAL.interaction_id)?.state).toBe("reconciling");
    expect(panel.shadowRoot.querySelector('[data-action="accept-interaction"]')?.disabled).toBe(true);
    await panel._decideInteraction(APPROVAL.interaction_id, "accept");
    expect(decisionCalls).toBe(1);
  });

  it("keeps a confirmed response locked while the authoritative list is stale", async () => {
    const panel = createPanel();
    panel._pendingInteractions = [APPROVAL];
    let decisionCalls = 0;
    panel._callWS = vi.fn(async (action) => {
      if (action === "decide_interaction") {
        decisionCalls += 1;
        return { status: "accepted" };
      }
      if (action === "list_pending_interactions") {
        return { items: [APPROVAL], count: 1, thread_id: "thread-alpha" };
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._decideInteraction(APPROVAL.interaction_id, "accept");

    expect(panel._interactionMutations.get(APPROVAL.interaction_id)).toMatchObject({
      state: "reconciling",
      deliveryConfirmed: true,
    });
    expect(panel.shadowRoot.querySelector('[data-action="accept-interaction"]')?.disabled).toBe(true);
    await panel._decideInteraction(APPROVAL.interaction_id, "accept");
    expect(decisionCalls).toBe(1);
  });

  it("ignores a late interaction failure after the user switches chats", async () => {
    const panel = createPanel();
    panel._pendingInteractions = [APPROVAL];
    let rejectDecision;
    panel._callWS = vi.fn(() => new Promise((_resolve, reject) => {
      rejectDecision = reject;
    }));

    const pending = panel._decideInteraction(APPROVAL.interaction_id, "decline");
    panel._retireThreadInteractionState("thread-beta");
    panel._selectedThreadId = "thread-beta";
    panel._activeThread = { ...panel._activeThread, thread_id: "thread-beta", title: "Other chat" };
    rejectDecision(new Error("late private failure"));
    await pending;

    expect(panel._error).toBe("");
    expect(panel._interactionMutations.size).toBe(0);
    expect(panel._selectedThreadId).toBe("thread-beta");
  });

  it("includes reduced-motion and narrow-screen fallbacks in the shadow stylesheet", () => {
    const panel = createPanel();
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(stylesheet).toMatch(/prefers-reduced-motion\s*:\s*reduce/i);
    expect(stylesheet).toMatch(/@media\s*\(max-width\s*:\s*\d+px\)/i);
  });

  it("provides a semantic workspace tree and a new-chat action when nothing is selected", () => {
    const panel = createPanel();
    const project = {
      project_id: "project_aria",
      kind: "project",
      name: "Accessible workspace",
      archived_at: null,
    };
    panel._projects = [project];
    panel._threads = [{
      thread_id: "thread_aria",
      project_id: project.project_id,
      project_kind: "project",
      title: "Selected chat",
      effective_model: "gpt-5.6",
      effective_thinking_level: "medium",
      status: "idle",
      archived_at: null,
    }, {
      thread_id: "thread_archived",
      project_id: "direct_aria",
      project_kind: "direct",
      title: "Archived direct chat",
      effective_model: "gpt-5.6",
      effective_thinking_level: "medium",
      status: "idle",
      archived_at: "2026-07-15T12:00:00Z",
    }];
    panel._selectedProjectId = project.project_id;
    panel._selectedThreadId = "thread_aria";
    panel._activeThread = { ...panel._threads[0], attachments: [] };
    panel._render(true);

    const selectedChat = panel.shadowRoot.querySelector('[data-thread-id="thread_aria"]');
    expect(selectedChat?.getAttribute("aria-current")).toBe("page");

    const collapse = panel.shadowRoot.querySelector('[data-action="toggle-project-collapse"]');
    const projectChatListId = collapse?.getAttribute("aria-controls");
    expect(collapse?.tagName).toBe("BUTTON");
    expect(collapse?.getAttribute("aria-expanded")).toBe("true");
    expect(projectChatListId).toBeTruthy();
    expect(panel.shadowRoot.getElementById(projectChatListId)).toBeTruthy();

    const directToggle = panel.shadowRoot.querySelector('[data-action="toggle-section"][data-section="direct"]');
    const archivedToggle = panel.shadowRoot.querySelector('[data-action="toggle-section"][data-section="archived"]');
    expect(directToggle?.getAttribute("aria-controls")).toBe("direct-chat-list");
    expect(directToggle?.getAttribute("aria-expanded")).toBe("true");
    expect(panel.shadowRoot.getElementById("direct-chat-list")).toBeTruthy();
    expect(archivedToggle?.getAttribute("aria-controls")).toBe("archived-chat-list");
    expect(archivedToggle?.getAttribute("aria-expanded")).toBe("false");
    expect(panel.shadowRoot.getElementById("archived-chat-list")?.hidden).toBe(true);

    panel._selectedThreadId = null;
    panel._activeThread = null;
    panel._renderMessages();
    const newChat = panel.shadowRoot.querySelector('.empty-state-main [data-action="new-direct-chat"]');
    expect(newChat?.textContent).toContain("New chat");
    newChat?.click();
    expect(panel._showThreadForm).toBe(true);
    expect(panel._threadForm.projectId).toBeNull();

    panel._renderProgress();
    const progress = panel.shadowRoot.getElementById("progress-list");
    expect(progress?.getAttribute("role")).toBe("list");
    expect(progress?.querySelector('[role="listitem"]')?.getAttribute("aria-label")).toMatch(/complete|active|error/i);
  });
});
