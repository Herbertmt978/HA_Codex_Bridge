/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function event(sequence, event_type, payload = {}) {
  return { event_id: `event-${sequence}`, thread_id: "thread-timeline", sequence, event_type, payload };
}

function createPanel(events) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  const thread = {
    thread_id: "thread-timeline", project_id: "project-timeline", title: "Timeline chat",
    status: "idle", mode: "edit", attachments: [], active_run_id: null,
  };
  panel._config = { api_version: 1, connection_type: "supervisor", panel_title: "Codex Bridge" };
  panel._status = { auth: { state: "ok", auth_required: false }, account: { available: true, auth_mode: "chatgpt" } };
  panel._projects = [{ project_id: thread.project_id, kind: "project", name: "Timeline project", archived_at: null }];
  panel._threads = [thread];
  panel._selectedProjectId = thread.project_id;
  panel._selectedThreadId = thread.thread_id;
  panel._activeThread = thread;
  panel._events = events;
  vi.spyOn(panel.shadowRoot.getElementById("conversation-scroll"), "getBoundingClientRect").mockReturnValue({ top: 0, bottom: 640, left: 0, right: 800, width: 800, height: 640 });
  panel._forceMessageRebuild = true;
  panel._render(true);
  return panel;
}

describe("conversation timeline panel", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("shows turn ticks and paired snippets without exposing run identifiers", () => {
    const panel = createPanel([
      event(1, "message.created", { run_id: "private-run-id", text: "Find the config" }),
      event(2, "message.completed", { run_id: "private-run-id", text: "The config is in settings." }),
      event(3, "message.created", { run_id: "next-private-run-id", text: "Thanks" }),
    ]);

    const navigation = panel.shadowRoot.getElementById("conversation-timeline");
    const buttons = navigation.querySelectorAll(".timeline-item");
    expect(navigation.getAttribute("aria-label")).toBe("Conversation turns");
    expect(navigation.hidden).toBe(false);
    expect(buttons).toHaveLength(2);
    expect(buttons[0].getAttribute("aria-label")).toContain("You: Find the config");
    buttons[0].focus();
    expect(navigation.querySelector(".timeline-preview-desktop").textContent).toContain("The config is in settings.");
    expect(buttons[1].getAttribute("aria-label")).toContain("Codex response in progress");
    expect(navigation.textContent).not.toContain("private-run-id");
    expect(navigation.querySelector("[aria-current='location']").dataset.sequence).toBe("3");
  });

  it("uses arrow keys for roving focus and jumps inside the conversation scroller", () => {
    const panel = createPanel([
      event(1, "message.created", { run_id: "run-one", text: "First" }),
      event(2, "message.completed", { run_id: "run-one", text: "First answer" }),
      event(3, "message.created", { run_id: "run-two", text: "Second" }),
    ]);
    const buttons = [...panel.shadowRoot.querySelectorAll(".timeline-item")];
    buttons[0].focus();
    buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    expect(panel.shadowRoot.activeElement).toBe(buttons[1]);
    buttons[0].focus();

    const scroller = panel.shadowRoot.getElementById("conversation-scroll");
    const target = [...panel.shadowRoot.querySelectorAll("#message-list [data-sequence]")].find((node) => node.dataset.sequence === "1");
    vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue({ top: 100 });
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ top: 320 });
    buttons[0].click();
    expect(scroller.scrollTop).toBe(204);
    expect(buttons[0].getAttribute("aria-current")).toBe("location");
    expect(panel.shadowRoot.activeElement).toBe(buttons[0]);
  });

  it("keeps the focused marker and off-bottom reading position during incremental updates", () => {
    const panel = createPanel([
      event(1, "message.created", { run_id: "run-one", text: "First" }),
      event(2, "message.completed", { run_id: "run-one", text: "First answer" }),
      event(3, "message.created", { run_id: "run-two", text: "Second" }),
    ]);
    const scroller = panel.shadowRoot.getElementById("conversation-scroll");
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1200 },
      clientHeight: { configurable: true, value: 500 },
    });
    scroller.scrollTop = 250;
    const firstButton = panel.shadowRoot.querySelector(".timeline-item");
    firstButton.focus();

    panel._events = [...panel._events, event(4, "message.completed", { run_id: "run-one", text: "Additional answer" })];
    panel._render();

    expect(panel.shadowRoot.querySelector(".timeline-item")).toBe(firstButton);
    expect(panel.shadowRoot.activeElement).toBe(firstButton);
    expect(scroller.scrollTop).toBe(250);
    expect(panel.shadowRoot.querySelector(".timeline-preview-desktop").textContent).toContain("Additional answer");
  });

  it("hides an empty timeline and retains responsive, large-target, and reduced-motion rules", () => {
    const panel = createPanel([]);
    expect(panel.shadowRoot.getElementById("conversation-timeline").hidden).toBe(true);
    const stylesheet = panel.shadowRoot.querySelector("style").textContent;
    expect(panel.shadowRoot.getElementById("conversation-timeline-toggle")).toBeNull();
    expect(stylesheet.includes("grid-template-columns: 32px")).toBe(true);
    expect(stylesheet).toMatch(/min-width:\s*44px/);
    expect(stylesheet).toMatch(/prefers-reduced-motion\s*:\s*reduce/i);
  });

  it("persists bookmark anchors without storing transcript text and isolates chats", () => {
    localStorage.clear();
    const events = [event(1, "message.created", { text: "Private prompt" })];
    const panel = createPanel(events);
    panel.shadowRoot.querySelector(".timeline-item").focus();
    const bookmark = panel.shadowRoot.querySelector(".timeline-bookmark");
    bookmark.click();
    expect(panel.shadowRoot.querySelector(".timeline-item").hasAttribute("data-bookmarked")).toBe(true);
    expect(localStorage.getItem(panel._conversationBookmarkKey())).toBe("[1]");
    panel.remove();
    const restored = createPanel(events);
    expect(restored.shadowRoot.querySelector(".timeline-item").hasAttribute("data-bookmarked")).toBe(true);
    restored._selectedThreadId = "another-chat";
    restored._renderConversationTimeline();
    expect(restored.shadowRoot.querySelector(".timeline-item").hasAttribute("data-bookmarked")).toBe(false);
  });

  it("keeps preview controls stable across unrelated renders and Escape dismisses the card", () => {
    const panel = createPanel([event(1, "message.created", { text: "First" })]);
    panel.shadowRoot.querySelector(".timeline-item").focus();
    const bookmark = panel.shadowRoot.querySelector(".timeline-bookmark");
    bookmark.focus();
    panel._renderConversationTimeline();
    expect(panel.shadowRoot.querySelector(".timeline-bookmark")).toBe(bookmark);
    expect(panel.shadowRoot.activeElement).toBe(bookmark);
    bookmark.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(panel.shadowRoot.getElementById("conversation-timeline-desktop-preview").hidden).toBe(true);
    expect(panel.shadowRoot.activeElement).toBe(panel.shadowRoot.querySelector(".timeline-item"));
  });

  it("does not cancel pending hover dismissal when streamed events arrive", () => {
    vi.useFakeTimers();
    const panel = createPanel([event(1, "message.created", { text: "First" })]);
    const tick = panel.shadowRoot.querySelector(".timeline-item");
    panel._handleTimelinePointerOver({ target: tick });
    panel._handleTimelinePointerOut({ target: tick, relatedTarget: null });
    panel._events = [...panel._events, event(2, "message.completed", { text: "Answer" })];
    panel._renderConversationTimeline();
    vi.advanceTimersByTime(210);
    expect(panel.shadowRoot.getElementById("conversation-timeline-desktop-preview").hidden).toBe(true);
    panel.remove();
    vi.useRealTimers();
  });

  it("keeps the jump action focused when a response completes", () => {
    const panel = createPanel([event(1, "message.created", { text: "First" })]);
    panel.shadowRoot.querySelector(".timeline-item").focus();
    panel.shadowRoot.querySelector(".timeline-preview-jump").focus();
    panel._events = [...panel._events, event(2, "message.completed", { text: "Answer" })];
    panel._renderConversationTimeline();
    expect(panel.shadowRoot.activeElement).toBe(panel.shadowRoot.querySelector(".timeline-preview-jump"));
  });
});
