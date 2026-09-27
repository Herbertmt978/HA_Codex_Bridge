/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { selectedMessagePassage } from "../src/message-actions.js";
import "../src/codex-bridge-panel.js";

function makePanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._selectedThreadId = "chat";
  panel._activeThread = { thread_id: "chat", title: "Working chat", status: "idle", mode: "edit", attachments: [] };
  panel._status = { auth: { state: "ok", auth_required: false }, account: { available: true } };
  panel._config = { capabilities: [] };
  panel._callWS = vi.fn();
  panel._renderComposerState(panel._activeThread);
  return panel;
}

function action(article, name) {
  return [...article.querySelectorAll(".message-actions button")].find((button) => button.getAttribute("aria-label") === name);
}

describe("public message actions", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("copies exactly one public response, excluding its labels, tools and adjacent messages", async () => {
    const panel = makePanel();
    panel._writeClipboardText = vi.fn(async () => {});
    panel._events = [{ event_type: "tool.output", payload: { text: "private tool output" } }];
    const article = panel._renderMessage("assistant", "# Original\n\n**answer**", 2, "Plan");
    action(article, "Copy message").click();
    await Promise.resolve();
    expect(panel._writeClipboardText).toHaveBeenCalledWith("# Original\n\n**answer**");
    expect(panel._callWS).not.toHaveBeenCalled();
  });

  it("appends an attributed editable quote without sending or replacing a draft", async () => {
    const panel = makePanel();
    const input = panel.shadowRoot.getElementById("prompt-input");
    input.value = "Existing draft";
    action(panel._renderMessage("assistant", "First line\nSecond line", 3), "Quote message").click();
    await Promise.resolve();
    expect(input.value).toBe("Existing draft\n\nAssistant response in “Working chat”:\n> First line\n> Second line");
    expect(panel._draftForThread("chat")).toBe(input.value);
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel.shadowRoot.activeElement).toBe(input);
  });

  it("refuses quote insertion into a pending mutation or another chat", () => {
    const panel = makePanel();
    const article = panel._renderMessage("assistant", "Answer", 4);
    panel._promptMutations.set("chat", { state: "retryable", prompt: "Reviewed prompt" });
    action(article, "Quote message").click();
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("Reviewed prompt");
    panel._promptMutations.clear();
    panel.shadowRoot.getElementById("prompt-input").value = "";
    panel._selectedThreadId = "other";
    action(article, "Quote message").click();
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("");
  });

  it("shows clipboard failure and rejects missing passage selection", async () => {
    const panel = makePanel();
    panel._setError = vi.fn();
    panel._writeClipboardText = vi.fn(async () => { throw new Error("Clipboard denied"); });
    const article = panel._renderMessage("user", "Message", 5);
    action(article, "Copy message").click();
    await Promise.resolve();
    expect(panel._setError).toHaveBeenCalledWith(expect.objectContaining({ message: "Clipboard denied" }));
    action(article, "Copy passage").click();
    expect(panel._setError).toHaveBeenCalledWith("Select a passage inside this message first.");
  });

  it("accepts only a contained passage, rejecting cross-message and toolbar selections", () => {
    const content = document.createElement("div");
    content.innerHTML = '<p>First passage</p><div class="code-head">Copy code</div><pre>code</pre>';
    document.body.append(content, document.createTextNode("Neighbour"));
    const range = document.createRange();
    range.setStart(content.firstChild.firstChild, 6);
    range.setEnd(content.firstChild.firstChild, 13);
    const selection = { isCollapsed: false, rangeCount: 1, getRangeAt: () => range };
    expect(selectedMessagePassage(content, selection)).toBe("passage");
    range.setEnd(document.body.lastChild, 5);
    expect(selectedMessagePassage(content, selection)).toBe("");
    range.setEnd(content.lastChild.firstChild, 4);
    expect(selectedMessagePassage(content, selection)).toBe("");
  });

  it("copies unchanged fenced source after wrap and line-number toggles", async () => {
    const panel = makePanel();
    panel._writeClipboardText = vi.fn(async () => {});
    const article = panel._renderMessage("assistant", "```js\nconst value = 1;\n```", 6);
    article.querySelectorAll(".code-toggle").forEach((button) => button.click());
    article.querySelector(".copy-button").click();
    await Promise.resolve();
    expect(panel._writeClipboardText).toHaveBeenCalledWith("const value = 1;\n");
  });
});

describe("passage boundary hardening", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
  it("preserves native selected block separators, Unicode and whitespace", () => {
    const content = document.createElement("div");
    content.innerHTML = '<p>  café 👩🏽‍💻</p><p>第二段 é  </p>';
    const range = document.createRange();
    range.selectNodeContents(content);
    const text = "  café 👩🏽‍💻\n\n第二段 é  ";
    expect(selectedMessagePassage(content, {
      isCollapsed: false, rangeCount: 1, getRangeAt: () => range, toString: () => text,
    })).toBe(text);
  });

  it("fails closed for throwing, malformed and unsupported composed ranges", () => {
    const host = document.createElement("div");
    const root = host.attachShadow({ mode: "open" });
    const content = document.createElement("div");
    content.textContent = "public";
    root.append(content);
    const range = document.createRange();
    range.selectNodeContents(content);
    const selection = { isCollapsed: false, rangeCount: 1, getRangeAt: () => range };
    for (const getComposedRanges of [() => { throw new Error("unsupported"); }, () => [], () => [{}]]) {
      expect(selectedMessagePassage(content, { ...selection, getComposedRanges })).toBe("");
    }
    expect(selectedMessagePassage(content, { ...selection, getRangeAt: () => { throw new Error("stale"); } })).toBe("");
  });

  it("rejects hidden text even when it lies between public endpoints", () => {
    const content = document.createElement("div");
    content.innerHTML = '<p>Before</p><span hidden>private</span><p>After</p>';
    const range = document.createRange();
    range.selectNodeContents(content);
    expect(selectedMessagePassage(content, { isCollapsed: false, rangeCount: 1, getRangeAt: () => range })).toBe("");
  });

  it("retains a validated touch activation only until its click or blur", async () => {
    const panel = makePanel();
    panel._writeClipboardText = vi.fn(async () => {});
    panel._setError = vi.fn();
    const article = panel._renderMessage("user", "  exact 👩🏽‍💻\nnext  ", 7);
    const range = document.createRange();
    range.selectNodeContents(article.querySelector(".message-content"));
    const selection = { isCollapsed: false, rangeCount: 1, getRangeAt: () => range };
    vi.spyOn(window, "getSelection").mockReturnValue(selection);
    const button = action(article, "Copy passage");
    button.dispatchEvent(new Event("pointerdown"));
    selection.isCollapsed = true;
    button.click();
    await Promise.resolve();
    expect(panel._writeClipboardText).toHaveBeenCalledWith("  exact 👩🏽‍💻\nnext  ");
    button.click();
    expect(panel._writeClipboardText).toHaveBeenCalledTimes(1);
    selection.isCollapsed = false;
    button.dispatchEvent(new Event("focus"));
    selection.isCollapsed = true;
    button.dispatchEvent(new Event("blur"));
    button.click();
    expect(panel._writeClipboardText).toHaveBeenCalledTimes(1);
  });

  it("never reuses an activation passage for a new cross-message range", () => {
    const panel = makePanel();
    panel._writeClipboardText = vi.fn();
    panel._setError = vi.fn();
    const article = panel._renderMessage("user", "Selected response", 8);
    const neighbour = panel._renderMessage("user", "Neighbour", 9);
    panel.shadowRoot.getElementById("message-list").append(article, neighbour);
    const range = document.createRange();
    range.selectNodeContents(article.querySelector(".message-content"));
    const selection = { isCollapsed: false, rangeCount: 1, getRangeAt: () => range };
    vi.spyOn(window, "getSelection").mockReturnValue(selection);
    const button = action(article, "Copy passage");
    button.dispatchEvent(new Event("focus"));
    range.setEnd(neighbour.querySelector(".bubble-text").firstChild, 4);
    button.click();
    expect(panel._writeClipboardText).not.toHaveBeenCalled();
    expect(panel._setError).toHaveBeenCalledWith("Select a passage inside this message first.");
  });
});


describe('overflow passage activation',()=>{
  it('retains the pointer snapshot when trigger focus collapses selection',async()=>{
    const panel=makePanel();panel._writeClipboardText=vi.fn(async()=>{});
    const article=panel._renderMessage('assistant','Exact selected text',10);
    panel.shadowRoot.getElementById('message-list').append(article);
    const range=document.createRange();range.selectNodeContents(article.querySelector('.message-content'));
    const selection={isCollapsed:false,rangeCount:1,getRangeAt:()=>range};
    vi.spyOn(window,'getSelection').mockReturnValue(selection);
    const trigger=article.querySelector('.message-actions-trigger');
    trigger.dispatchEvent(new Event('pointerdown'));selection.isCollapsed=true;
    trigger.dispatchEvent(new Event('focus'));trigger.click();
    const copy=action(article,'Copy passage');expect(copy.disabled).toBe(false);copy.click();
    await Promise.resolve();expect(panel._writeClipboardText).toHaveBeenCalledWith('Exact selected text');
  });
});
