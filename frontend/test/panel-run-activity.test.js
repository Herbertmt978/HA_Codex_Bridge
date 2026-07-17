/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function event(sequence, event_type, payload = {}) {
  return {
    event_id: `evt-${sequence}`,
    thread_id: "thread-activity",
    sequence,
    event_type,
    payload,
  };
}

function createPanel({ status = "running", activeRunId = "run-activity", events = [] } = {}) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  const thread = {
    thread_id: "thread-activity",
    project_id: "project-activity",
    title: "Activity chat",
    status,
    mode: "edit",
    attachments: [],
    active_run_id: activeRunId,
  };
  panel._config = { api_version: 1, connection_type: "supervisor", panel_title: "Codex Bridge" };
  panel._status = {
    auth: { state: "ok", auth_required: false },
    account: { available: true, auth_mode: "chatgpt", plan_type: "plus" },
    diagnostics: { app_version: "0.6.0", bridge_version: "0.6.0", active_codex_version: "1.2.3" },
    model_catalog: { models: [{ model: "gpt-5.6", thinking_levels: ["medium"] }], default_model: "gpt-5.6", default_thinking_level: "medium" },
    limits: { available: true },
  };
  panel._projects = [{ project_id: "project-activity", kind: "project", name: "Activity project", archived_at: null }];
  panel._threads = [thread];
  panel._selectedProjectId = "project-activity";
  panel._selectedThreadId = thread.thread_id;
  panel._activeThread = thread;
  panel._events = events;
  panel._forceMessageRebuild = true;
  return panel;
}

describe("panel run activity integration", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("marks a busy rail chat with a spinner and a natural accessible label", () => {
    const panel = createPanel({ events: [event(1, "run.started", { run_id: "run-activity" })] });
    panel._render(true);

    const select = panel.shadowRoot.querySelector('[data-thread-id="thread-activity"]');
    const pill = select?.querySelector(".status-pill");
    expect(pill?.classList.contains("running")).toBe(true);
    expect(select?.getAttribute("aria-label")).toMatch(/Activity chat.*(working|running)/i);
  });

  it("renders the live action line, step chip, bounded history, and file counters", () => {
    const plan = [
      { step: "Inspect repository", status: "completed" },
      { step: "Implement the change", status: "inProgress" },
      { step: "Run checks", status: "pending" },
    ];
    const events = [
      event(1, "run.started", { run_id: "run-activity" }),
      event(2, "item.started", { run_id: "run-activity", item_type: "commandExecution", action_types: ["read"] }),
      event(3, "plan.updated", { run_id: "run-activity", plan }),
      event(4, "patch.updated", {
        run_id: "run-activity",
        changes: [{ path: "frontend/src/example.js", kind: "update", diff: "@@\n-old\n+new\n" }],
      }),
      event(5, "item.started", {
        run_id: "run-activity",
        item_type: "collabAgentToolCall",
        operation: "wait",
        agent_state_counts: { running: 2, completed: 1 },
      }),
    ];
    const panel = createPanel({ events });
    panel._render(true);

    const activity = panel.shadowRoot.getElementById("run-activity");
    expect(activity?.getAttribute("role")).toBe("status");
    expect(activity?.getAttribute("aria-live")).toBe("polite");
    expect(activity?.getAttribute("aria-atomic")).toBe("true");
    expect(activity?.querySelector(".run-activity-copy")?.textContent).toMatch(/Implement the change/i);

    const chip = activity?.querySelector(".run-step-chip");
    expect(chip).toBeTruthy();
    expect(chip?.hasAttribute("data-tooltip")).toBe(false);
    expect(chip?.hasAttribute("title")).toBe(false);
    expect(chip?.textContent).toMatch(/2\s*\/\s*3/);
    expect(chip?.textContent).toMatch(/1 file/i);
    expect(chip?.textContent).toContain("+1");
    expect(chip?.textContent).toContain("-1");
    expect(chip?.textContent).toContain("2 agents active");
    expect(chip?.getAttribute("aria-expanded")).toBe("false");

    chip?.click();
    const expandedChip = activity?.querySelector(".run-step-chip");
    expect(expandedChip?.getAttribute("aria-expanded")).toBe("true");
    const history = activity?.querySelectorAll(".run-step-history li");
    expect(history?.length).toBeGreaterThan(0);
    expect(history?.length).toBeLessThanOrEqual(8);
    expect(activity?.querySelector(".run-step-tooltip")?.textContent).toMatch(/Inspect repository|Reading files/i);
    expect(activity?.querySelectorAll(".run-stage-item")).toHaveLength(3);
    expect(activity?.querySelector('.run-stage-item[aria-current="step"]')?.textContent).toContain("Implement the change");
    expect(activity?.querySelector(".run-step-agent-summary")?.textContent).toMatch(/Subagents.*2 active.*1 complete/i);
  });

  it("sets transcript busy state while streaming and replaces the delta with completion", () => {
    const panel = createPanel({
      events: [event(1, "message.delta", { run_id: "run-activity", item_id: "assistant-1", text: "Partial answer" })],
    });
    panel._render(true);

    let messageList = panel.shadowRoot.getElementById("message-list");
    expect(messageList?.getAttribute("aria-busy")).toBe("true");
    expect(messageList?.querySelector("article.message.assistant.streaming")?.textContent).toContain("Partial answer");
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(stylesheet).toContain("\\00B7 responding");
    expect(stylesheet).not.toContain("Â·");
    expect(panel.shadowRoot.getElementById("run-activity")?.querySelector(".run-activity-copy")?.textContent).toContain("Generating a response");
    expect(panel.shadowRoot.getElementById("run-activity")?.querySelector(".run-step-chip")).toBeTruthy();

    panel._events = [
      event(1, "message.delta", { run_id: "run-activity", item_id: "assistant-1", text: "Partial answer" }),
      event(2, "message.completed", { run_id: "run-activity", item_id: "assistant-1", text: "Final answer" }),
      event(3, "run.completed", { run_id: "run-activity" }),
    ];
    panel._render(true);

    messageList = panel.shadowRoot.getElementById("message-list");
    expect(messageList?.getAttribute("aria-busy")).toBe("false");
    expect(messageList?.querySelector("article.message.assistant.streaming")).toBeNull();
    expect(messageList?.textContent).toContain("Final answer");
    expect(messageList?.textContent).not.toContain("Partial answer");
  });

  it("does not show a stale preparing state for an idle thread with orphaned events", () => {
    const panel = createPanel({
      status: "idle",
      activeRunId: "run-old",
      events: [
        event(1, "item.started", { run_id: "run-old", item_type: "agentMessage" }),
        event(2, "message.delta", { run_id: "run-old", text: "stale response" }),
      ],
    });
    panel._render(true);

    expect(panel.shadowRoot.getElementById("thread-status-text")?.textContent).toBe("");
    expect(panel.shadowRoot.getElementById("stop-run-button")?.classList.contains("hidden")).toBe(true);
    expect(panel.shadowRoot.getElementById("prompt-input")?.placeholder).toBe("Message Codex through Home Assistant");
    expect(panel.shadowRoot.getElementById("message-list")?.getAttribute("aria-busy")).toBe("false");
    expect(panel.shadowRoot.querySelector("article.message.assistant.streaming")).toBeNull();
    expect(panel.shadowRoot.getElementById("run-activity")?.textContent).not.toContain("Preparing a response");
  });

  it("refreshes the Activity card for every accepted streamed run event", () => {
    const panel = createPanel({ events: [] });
    panel._render(true);
    const activitySpy = vi.spyOn(panel, "_renderActivityCenter");

    panel._handleSubscribedEvent("thread-activity", event(1, "plan.updated", {
      run_id: "run-activity",
      plan: [{ step: "Inspect the workspace", status: "inProgress" }],
    }));

    expect(activitySpy).toHaveBeenCalledTimes(1);
    expect(panel.shadowRoot.querySelector('[data-section="background"]')?.textContent).toContain("Working");
  });

  it("counts sources only for the run currently represented by Activity", () => {
    const panel = createPanel({ events: [
      event(1, "web_search.completed", {
        run_id: "run-previous",
        sources: [{ url: "https://old.example/1" }, { url: "https://old.example/2" }],
      }),
      event(2, "web_search.completed", {
        run_id: "run-activity",
        citations: [{ url: "https://current.example/1" }],
      }),
    ] });
    panel._render(true);

    const sources = panel.shadowRoot.querySelector('[data-section="sources"]');
    expect(sources?.textContent).toContain("1 source reported for this run");
    expect(sources?.textContent).not.toContain("3 sources");
  });

  it("keeps reduced-motion and mobile layout fallbacks in the activity stylesheet", () => {
    const panel = createPanel();
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(stylesheet).toMatch(/prefers-reduced-motion\s*:\s*reduce/i);
    expect(stylesheet).toMatch(/--rail-bg:\s*color-mix\(in srgb,\s*var\(--surface-bg\)\s+90%,\s*#dff4c1\s+10%\)/i);
    expect(stylesheet).toMatch(/@media\s*\(max-width\s*:\s*\d+px\)/i);
    expect(stylesheet).toMatch(/run-activity-region|run-step-chip/);
    expect(stylesheet).toMatch(/@media\s*\(max-width\s*:\s*\d+px\)[\s\S]*?\.run-activity-region/i);
  });
});
