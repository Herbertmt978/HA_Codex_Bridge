/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fencedCodeParts, renderAssistantMarkdown } from "../src/markdown.js";
import { MAX_STREAMING_MESSAGE_CHARS } from "../src/protocol.js";
import { acceptEvents, createEventStreamState } from "../src/event-stream.js";
import "../src/codex-bridge-panel.js";

function event(sequence, event_type, payload) {
  return { event_id: `format-${sequence}`, thread_id: "format-chat", sequence, event_type, payload };
}

function panel() {
  const element = document.createElement("codex-bridge-panel");
  document.body.append(element);
  element._selectedThreadId = "format-chat";
  element._activeThread = { thread_id: "format-chat", title: "Formatting" };
  element._events = [];
  element._writeClipboardText = vi.fn(async () => {});
  return element;
}

describe("response source and presentation parity", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it.each(["\n", "\r\n", "\r"])("copies exact fenced source with %j line endings for either role", async (eol) => {
    const element = panel();
    const code = `  first${eol}${eol}last  ${eol}`;
    const source = `Prose${eol}${eol}\`\`\`text${eol}${code}\`\`\`${eol}After`;
    for (const role of ["assistant", "user"]) {
      const article = element._renderMessage(role, source, 1);
      article.querySelector(".copy-button").click();
      expect(element._writeClipboardText).toHaveBeenLastCalledWith(code);
      expect(article.querySelector(".message-content").textContent).toContain("After");
    }
    const callback = vi.fn(() => document.createElement("pre"));
    renderAssistantMarkdown(document, source, { createCodeBlock: callback });
    expect(callback).toHaveBeenCalledWith(document, code, "text");
  });

  it.each(["", "last", "last\n", "last\r\n", "last\r"])("never invents a trailing newline for an unfinished fence containing %j", (code) => {
    const callback = vi.fn(() => document.createElement("pre"));
    renderAssistantMarkdown(document, `\`\`\`text\r\n${code}`, { createCodeBlock: callback });
    expect(callback).toHaveBeenCalledWith(document, code, "text");
    expect(fencedCodeParts(`~~~text\r${code}`)[0].code).toBe(code);
  });

  it("keeps fence-like inline text in user prose and recognises longer and tilde fences", () => {
    const element = panel();
    const source = "# Plain **user** `inline` and ```not a block```\n\n~~~~js\nconst x = `safe`;\n~~~~";
    const article = element._renderMessage("user", source, 1);
    expect(article.querySelector("h1, strong, em")).toBeNull();
    expect(article.querySelector(".bubble-text").textContent).toContain("```not a block```");
    expect(article.querySelectorAll(".code-block")).toHaveLength(1);
  });

  it("appends prefix-shaped deltas exactly and rejects replayed event envelopes", () => {
    const element = panel();
    const events = [
      event(1, "message.delta", { run_id: "run-one", text: "Hello" }),
      event(2, "message.delta", { run_id: "run-one", text: "Hello again" }),
      event(3, "message.delta", { run_id: "other", text: "hidden" }),
    ];
    let state = acceptEvents(createEventStreamState(), events).state;
    state = acceptEvents(state, events).state;
    element._events = state.events;
    expect(element._streamingAssistantText({ assistantState: "streaming", runId: "run-one" }, "")).toBe("HelloHello again");
    element._events.push(event(4, "message.completed", { run_id: "run-one", text: "HelloHello again" }));
    expect(element._streamingAssistantText({ assistantState: "streaming", runId: "run-one" }, "")).toBe("");
  });

  it("renders identical safe ordinary Markdown from chunks, completion and retained history", () => {
    const element = panel();
    const source = "# Summary\n\nOrdinary **prose**.\n\n- One\n- Two\n\n| Name | Value |\n| --- | --- |\n| x | `on` |\n\n```js\r\n  const x = 1;\r\n```\r\n\n<script>bad()</script>\n![image](https://tracker.example/pixel)";
    element._events = [event(1, "message.delta", { run_id: "run-one", text: source.slice(0, 47) }), event(2, "message.delta", { run_id: "run-one", text: source.slice(47) })];
    const host = document.createElement("div");
    element._syncStreamingMessage(host, { assistantState: "streaming", runId: "run-one", busy: false });
    const streaming = host.querySelector(".message-content");
    const completed = element._renderEvent(event(3, "message.completed", { run_id: "run-one", text: source }));
    const history = element._renderMessage("assistant", source, 3, "Earlier history");
    expect(streaming.innerHTML).toBe(completed.querySelector(".message-content").innerHTML);
    expect(streaming.innerHTML).toBe(history.querySelector(".message-content").innerHTML);
    expect(streaming.querySelectorAll("li")).toHaveLength(2);
    expect(streaming.querySelector("table")).not.toBeNull();
    expect(streaming.querySelector("script, img, iframe")).toBeNull();
    expect(streaming.querySelector(".bubble-text")).toBeNull();
  });

  it("labels an omitted streaming prefix and shows the bounded tail as inert plaintext", () => {
    const element = panel();
    const source = `\`\`\`text\n${"x".repeat(MAX_STREAMING_MESSAGE_CHARS)}\n# still code`;
    element._events = [event(1, "message.delta", { run_id: "run-one", text: source })];
    const host = document.createElement("div");
    element._syncStreamingMessage(host, { assistantState: "partial", runId: "run-one", busy: false });
    expect(host.querySelector(".assistant-markdown-overflow-notice").textContent).toContain("Only the latest part");
    expect(host.querySelector(".assistant-markdown-overflow").textContent).toBe(source.slice(-MAX_STREAMING_MESSAGE_CHARS));
    expect(host.querySelector("h1, .code-block")).toBeNull();
    const completed = element._renderMessage("assistant", source, 2);
    expect(completed.querySelector(".code-text").textContent).toBe(source.slice("```text\n".length));
  });

  it("does not start a retained streaming tail with half a Unicode character", () => {
    const element = panel();
    element._events = [event(1, "message.delta", { run_id: "run-one", text: `x😀${"a".repeat(MAX_STREAMING_MESSAGE_CHARS - 1)}` })];
    const projection = element._streamingAssistantProjection({ assistantState: "streaming", runId: "run-one" }, "");
    expect(projection.truncated).toBe(true);
    expect(projection.text).toBe("a".repeat(MAX_STREAMING_MESSAGE_CHARS - 1));
  });

  it("copies the complete large code block during streaming, completion and reload", () => {
    const element = panel();
    const code = `${"Status.Value = \"Triaged\"\r\n".repeat(12_000)}// final line 😃\r\n`;
    const source = `\`\`\`powerfx\r\n${code}\`\`\``;
    element._events = source.match(/[\s\S]{1,3000}/gu).map((text, index) =>
      event(index + 1, "message.delta", { run_id: "run-one", text }));
    const projection = element._streamingAssistantProjection({ assistantState: "streaming", runId: "run-one" }, "");
    expect(projection).toEqual({ text: source, truncated: false });
    for (const text of [projection.text, source, JSON.parse(JSON.stringify({ text: source })).text]) {
      const article = element._renderMessage("assistant", text, 1);
      expect(article.querySelector(".code-text").textContent).toBe(code);
      article.querySelector(".copy-button").click();
      expect(element._writeClipboardText).toHaveBeenLastCalledWith(code);
    }
  });
});
