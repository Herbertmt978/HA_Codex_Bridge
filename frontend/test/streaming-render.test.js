/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createEventStreamState } from "../src/event-stream.js";
import "../src/codex-bridge-panel.js";

function event(sequence, event_type, payload = {}) {
  return { event_id: `stream-${sequence}`, thread_id: "stream-chat", sequence,
    event_type, payload: { run_id: "stream-run", ...payload } };
}

function panel() {
  const element = document.createElement("codex-bridge-panel");
  document.body.append(element);
  element._stopPolling();
  element._stopEventSubscription();
  window.clearTimeout(element._activityClockTimer);
  element._selectedThreadId = "stream-chat";
  element._activeThread = { thread_id: "stream-chat", title: "Streaming", status: "running",
    active_run_id: "stream-run", attachments: [] };
  const started = event(1, "run.started");
  element._eventStream = { ...createEventStreamState({ cursor: 1 }), events: [started] };
  element._events = [started];
  element._scheduleLiveRefresh = vi.fn();
  element._loadPromptQueue = vi.fn(async () => []);
  element._writeClipboardText = vi.fn(async () => {});
  return element;
}

describe("bounded streaming renders", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.useRealTimers(); });

  it("coalesces many small deltas while retaining and copying the complete long code", () => {
    const element = panel();
    const render = vi.spyOn(element, "_renderMessages");
    const code = `${'Status.Value = "Triaged"; // λ🧪\r\n'.repeat(10_000)}// END\r\n`;
    const source = `\`\`\`powerfx\r\n${code}\`\`\``;
    const chunks = source.match(/[\s\S]{1,256}/gu);
    chunks.forEach((text, index) => element._handleSubscribedEvent("stream-chat",
      event(index + 2, "message.delta", { text })));

    expect(code.length).toBeGreaterThan(200_000);
    expect(render).not.toHaveBeenCalled();
    vi.advanceTimersByTime(199);
    expect(render).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(render).toHaveBeenCalledOnce();
    const block = element.shadowRoot.querySelector(".message.streaming .code-block");
    expect(block.querySelector(".code-text").textContent).toBe(code);
    block.querySelector(".copy-button").click();
    expect(element._writeClipboardText).toHaveBeenLastCalledWith(code);

    element._handleSubscribedEvent("stream-chat", event(chunks.length + 2,
      "message.completed", { text: source }));
    expect(element.shadowRoot.querySelector(".message.streaming")).toBeNull();
    expect(element.shadowRoot.querySelector(".message.assistant .code-text").textContent).toBe(code);
  });

  it.each(["message.completed", "run.completed", "run.interrupted", "run.failed"])(
    "flushes pending text immediately on %s", (terminal) => {
      const element = panel();
      const render = vi.spyOn(element, "_renderMessages");
      element._handleSubscribedEvent("stream-chat", event(2, "message.delta", { text: "latest text" }));
      element._handleSubscribedEvent("stream-chat", event(3, terminal, { text: "latest text" }));
      expect(render).toHaveBeenCalledOnce();
      expect(element._streamingRenderTimer).toBeNull();
      if (terminal === "run.completed") {
        expect(element.shadowRoot.querySelector(".message.streaming")).toBeNull();
      } else {
        expect(element.shadowRoot.getElementById("message-list").textContent).toContain("latest text");
      }
      vi.advanceTimersByTime(250);
      expect(render).toHaveBeenCalledOnce();
    });

  it.each(["switch", "disconnect", "retire"])("cancels a stale delta render on %s", (action) => {
    const element = panel();
    const render = vi.spyOn(element, "_renderMessages");
    element._handleSubscribedEvent("stream-chat", event(2, "message.delta", { text: "old chat text" }));
    if (action === "switch") {
      element._stopEventSubscription();
      element._selectedThreadId = "other-chat";
      element._resetEventState();
    } else if (action === "disconnect") element.remove();
    else element._retireEventSubscription({ reconnect: false });
    render.mockClear();
    vi.advanceTimersByTime(250);
    expect(render).not.toHaveBeenCalled();
    expect(element._streamingRenderTimer).toBeNull();
  });
});
