/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearConversationHighlights, conversationMatchRanges, highlightConversationMessage } from "../src/conversation-highlights.js";
import "../src/codex-bridge-panel.js";

function setup() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._selectedThreadId = "selected";
  panel._activeDestination = "chats";
  panel._config = { capabilities: ["conversation_search_v1"] };
  panel._callWS = vi.fn();
  return panel;
}
const result = (sequence, excerpt = "needle") => ({ thread_id: "selected", sequence, role: "assistant", excerpt, anchor_cursor: sequence, revision_cursor: sequence });
const page = (results, total = results.length, next = null) => ({ results, total_matching_messages: total, has_more: next !== null, next_cursor: next, complete: true });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("rendered conversation highlights", () => {
  it("maps Unicode expansion and astral offsets to original readable characters", () => {
    expect(conversationMatchRanges("😀 Straße STRASSE", "strasse")).toEqual([{ start: 3, end: 9 }, { start: 10, end: 17 }]);
  });
  it("highlights across inline markup without changing controls, listeners or text", () => {
    const root = document.createElement("div");
    root.innerHTML = '<p>Str<strong>aße</strong> needle</p><div class="code-head"><button>needle</button></div><pre><span>nee</span><span>dle</span></pre><span hidden>needle</span>';
    const original = root.textContent;
    const strong = root.querySelector("strong");
    const button = root.querySelector("button");
    const click = vi.fn(); button.addEventListener("click", click);
    expect(highlightConversationMessage(root, "strasse")).toBe(2);
    expect([...root.querySelectorAll("mark")].map((mark) => mark.textContent).join("")).toBe("Straße");
    clearConversationHighlights(root);
    expect(root.textContent).toBe(original);
    expect(root.querySelector("strong")).toBe(strong);
    highlightConversationMessage(root, "needle");
    expect(root.querySelector("button mark")).toBeNull();
    expect(root.querySelector("[hidden] mark")).toBeNull();
    button.click(); expect(click).toHaveBeenCalledOnce();
    clearConversationHighlights(root); expect(root.textContent).toBe(original);
  });
  it("leaves native maths structure and stored source untouched while highlighting prose", () => {
    const root = document.createElement("div");
    const prose = document.createElement("p"); prose.textContent = "needle in prose";
    const wrapper = document.createElement("span");
    wrapper.className = "assistant-math-expression";
    wrapper.dataset.mathSource = "\\mathrm{needle}";
    const math = document.createElementNS("http://www.w3.org/1998/Math/MathML", "math");
    const identifier = document.createElementNS(math.namespaceURI, "mi");
    identifier.textContent = "needle"; math.append(identifier); wrapper.append(math);
    const bareMath = math.cloneNode(true);
    root.append(prose, wrapper, bareMath);
    const originalWrapper = wrapper.outerHTML;
    const originalMath = bareMath.outerHTML;
    expect(highlightConversationMessage(root, "needle")).toBe(1);
    expect(prose.querySelector("mark").textContent).toBe("needle");
    expect(wrapper.children).toHaveLength(1);
    expect(wrapper.firstElementChild).toBe(math);
    expect(wrapper.outerHTML).toBe(originalWrapper);
    expect(bareMath.outerHTML).toBe(originalMath);
    clearConversationHighlights(root);
    expect(wrapper.outerHTML).toBe(originalWrapper);
    expect(bareMath.outerHTML).toBe(originalMath);
    expect(prose.textContent).toBe("needle in prose");
  });
  it("treats hostile markup as text and bounds highlight work", () => {
    const root = document.createElement("div");
    root.textContent = "<img onerror=bad> " + "needle ".repeat(10000);
    highlightConversationMessage(root, "needle");
    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelectorAll("mark")).toHaveLength(400);
    const original = root.textContent;
    clearConversationHighlights(root); expect(root.textContent).toBe(original);
  });
});

describe("selected-chat find controller", () => {
  it("fetches an unloaded match, highlights the actual message and retains search focus", async () => {
    const panel = setup();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([result(2)]) : { role: "assistant", text: "**needle** and prose", sequence: 2 });
    const input = panel.shadowRoot.getElementById("conversation-search-input");
    panel._renderConversationSearch(); input.focus();
    await panel._findConversationMatches("needle");
    expect(panel._callWS).toHaveBeenCalledWith("get_transcript_message", { thread_id: "selected", sequence: 2 });
    const article = panel.shadowRoot.querySelector('[data-sequence="2"]');
    expect(article.querySelector(".message-content mark").textContent).toBe("needle");
    expect(panel.shadowRoot.activeElement).toBe(input);
    expect(panel.shadowRoot.getElementById("conversation-search-status").textContent).toBe("1 of 1 matching messages");
    expect(panel._conversationSearchState.error).toBeUndefined();
  });
  it("supports repeated Enter/Shift+Enter and Escape without losing the input", async () => {
    const panel = setup();
    panel._callWS.mockImplementation(async (action, payload) => action === "search_conversation" ? page([result(3), result(2)]) : { role: "user", text: `needle ${payload.sequence}` });
    const input = panel.shadowRoot.getElementById("conversation-search-input");
    input.value = "needle"; panel._renderConversationSearch(); input.focus();
    await panel._findConversationMatches("needle");
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await vi.waitFor(() => expect(panel._conversationSearchState.index).toBe(1));
    await vi.waitFor(() => expect(panel._conversationSearchState.loading).toBe(false));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }));
    await vi.waitFor(() => expect(panel._conversationSearchState.index).toBe(0));
    expect(panel.shadowRoot.activeElement).toBe(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(panel._conversationSearchState).toBeNull();
    expect(panel.shadowRoot.querySelector("mark.conversation-text-match")).toBeNull();
    expect(panel.shadowRoot.querySelector("[data-conversation-search-inserted]")).toBeNull();
  });
  it("keeps one page and one unloaded message while moving forwards and back across pages", async () => {
    const panel = setup();
    const first = Array.from({ length: 50 }, (_, i) => result(100 - i));
    panel._callWS.mockImplementation(async (action, payload) => action === "search_conversation" ? payload.before_cursor ? page([result(50), result(49)], 52) : page(first, 52, 51) : { role: "user", text: "needle" });
    await panel._findConversationMatches("needle");
    panel._conversationSearchState.index = 49;
    await panel._navigateConversationMatch(1);
    expect(panel._conversationSearchState.results).toHaveLength(2);
    expect(panel._conversationSearchState.pageNumber).toBe(1);
    expect(panel.shadowRoot.getElementById("conversation-search-status").textContent).toBe("51 of 52 matching messages");
    await panel._navigateConversationMatch(-1);
    expect(panel._conversationSearchState.pageNumber).toBe(0);
    expect(panel._conversationSearchState.index).toBe(49);
    expect(panel._conversationSearchState.results).toHaveLength(50);
    expect(panel.shadowRoot.querySelectorAll("[data-conversation-search-inserted]")).toHaveLength(1);
  });
  it("discards stale search and unloaded-message responses after query/chat cancellation", async () => {
    const panel = setup();
    const oldSearch = deferred();
    panel._callWS.mockReturnValueOnce(oldSearch.promise).mockResolvedValueOnce(page([]));
    const pending = panel._findConversationMatches("old");
    await panel._findConversationMatches("new");
    oldSearch.resolve(page([result(9)])); await pending;
    expect(panel._conversationSearchState.query).toBe("new");
    expect(panel.shadowRoot.querySelector('[data-sequence="9"]')).toBeNull();
    const oldMessage = deferred();
    panel._callWS.mockResolvedValueOnce(page([result(8)])).mockReturnValueOnce(oldMessage.promise);
    const jump = panel._findConversationMatches("needle");
    await vi.waitFor(() => expect(panel._callWS).toHaveBeenCalledWith("get_transcript_message", { thread_id: "selected", sequence: 8 }));
    panel._clearConversationSearch(); panel._selectedThreadId = "other";
    oldMessage.resolve({ role: "user", text: "needle" }); await jump;
    expect(panel.shadowRoot.querySelector('[data-sequence="8"]')).toBeNull();
  });
  it("invalidates an in-flight request on disconnect", async () => {
    const panel = setup(); const pending = deferred();
    panel._callWS.mockReturnValue(pending.promise);
    const search = panel._findConversationMatches("needle");
    panel.remove(); pending.resolve(page([result(4)])); await search;
    expect(panel._conversationSearchState).toBeNull();
    expect(panel._callWS).toHaveBeenCalledTimes(1);
  });
  it("retries failures with Enter and clears the error", async () => {
    const panel = setup();
    panel._callWS.mockRejectedValueOnce(new Error("unavailable")).mockResolvedValueOnce(page([]));
    const input = panel.shadowRoot.getElementById("conversation-search-input"); input.value = "needle";
    await panel._findConversationMatches("needle");
    expect(panel._conversationSearchState.error).toBe(true);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await vi.waitFor(() => expect(panel._conversationSearchState.loading).toBe(false));
    expect(panel._conversationSearchState.error).toBeUndefined();
    expect(panel.shadowRoot.getElementById("conversation-search-status").textContent).toBe("0 of 0 matching messages");
  });
  it("preserves exact code and message copy after actual-message highlighting", async () => {
    const panel = setup();
    const text = "```python\nneedle = 'needle'\n  print(needle)\n```";
    const copy = vi.spyOn(panel, "_writeClipboardText").mockResolvedValue();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([result(7)]) : { role: "assistant", text });
    await panel._findConversationMatches("needle");
    const article = panel.shadowRoot.querySelector('[data-sequence="7"]');
    expect(article.querySelectorAll(".code-text mark").length).toBeGreaterThan(0);
    article.querySelector('[aria-label="Copy original code"]').click();
    await vi.waitFor(() => expect(copy).toHaveBeenCalledWith("needle = 'needle'\n  print(needle)\n"));
    [...article.querySelectorAll(".message-actions button")].find((button) => button.getAttribute("aria-label") === "Copy message").click();
    await vi.waitFor(() => expect(copy).toHaveBeenCalledWith(text));
  });
  it("cancels debounce and pending search on input clearing", async () => {
    vi.useFakeTimers();
    const panel = setup();
    const input = panel.shadowRoot.getElementById("conversation-search-input");
    input.value = "needle"; input.dispatchEvent(new Event("input", { bubbles: true }));
    input.value = ""; input.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(300);
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel._conversationSearchState).toBeNull();
  });
  it("restores one selected unloaded message through a forced refresh without refetching or jumping", async () => {
    const panel = setup();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([result(2)]) : { role: "assistant", text: "**needle** original" });
    await panel._findConversationMatches("needle");
    const jump = vi.spyOn(panel, "_jumpToConversationTurn");
    vi.spyOn(panel, "_runActivityForThread").mockReturnValue({ busy: false });
    vi.spyOn(panel, "_renderConversationTimeline").mockImplementation(() => {});
    panel._events = []; panel._forceMessageRebuild = true;
    panel._renderMessages();
    expect(panel._callWS).toHaveBeenCalledTimes(2);
    expect(jump).not.toHaveBeenCalled();
    expect(panel._conversationSearchState.index).toBe(0);
    expect(panel.shadowRoot.querySelectorAll('[data-sequence="2"]')).toHaveLength(1);
    expect(panel.shadowRoot.querySelector('[data-sequence="2"] mark').textContent).toBe("needle");
  });
  it("deduplicates a naturally loaded search message and keeps newer activity from bottom-sticking", async () => {
    const panel = setup();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([result(2)]) : { role: "user", text: "needle original" });
    await panel._findConversationMatches("needle");
    vi.spyOn(panel, "_runActivityForThread").mockReturnValue({ busy: false });
    vi.spyOn(panel, "_renderConversationTimeline").mockImplementation(() => {});
    const bottom = vi.spyOn(panel, "_scrollMessagesToBottom").mockImplementation(() => {});
    panel._renderedThreadId = "selected"; panel._renderedSequence = 0; panel._forceMessageRebuild = false;
    panel._events = [
      { sequence: 2, event_type: "message.created", payload: { role: "user", text: "needle updated" } },
      { sequence: 3, event_type: "message.completed", payload: { role: "assistant", text: "new activity" } },
    ];
    panel._renderMessages();
    expect(panel.shadowRoot.querySelectorAll('[data-sequence="2"]')).toHaveLength(1);
    expect(panel.shadowRoot.querySelector('[data-sequence="2"]').hasAttribute("data-conversation-search-inserted")).toBe(false);
    expect(panel.shadowRoot.querySelector('[data-sequence="2"] .message-content').textContent).toBe("needle updated");
    expect(panel.shadowRoot.querySelector('[data-sequence="2"] mark').textContent).toBe("needle");
    expect(bottom).not.toHaveBeenCalled();
    panel._clearConversationSearch(); panel._renderMessages();
    expect(bottom).toHaveBeenCalledOnce();
    expect(panel.shadowRoot.querySelector("mark.conversation-text-match")).toBeNull();
  });
  it("clears the cached public message and pending query when the HA user changes", async () => {
    const panel = setup();
    panel._hass = { user: { id: "owner-one" } }; panel._loadPreferences();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([result(2)]) : { role: "user", text: "needle" });
    await panel._findConversationMatches("needle");
    panel._hass = { user: { id: "owner-two" } }; panel._loadPreferences();
    expect(panel._conversationSearchState).toBeNull();
    expect(panel.shadowRoot.querySelector("[data-conversation-search-inserted]")).toBeNull();
    expect(panel.shadowRoot.getElementById("conversation-search-input").value).toBe("");
  });
  it("uses the original global anchor when chats interleave instead of a coincident local sequence", async () => {
    const panel = setup();
    const list = panel.shadowRoot.getElementById("message-list");
    list.append(panel._renderMessage("user", "unrelated message", 2), panel._renderMessage("assistant", "needle actual", 27));
    panel._callWS.mockResolvedValue(page([{ ...result(2), anchor_cursor: 27, revision_cursor: 40 }]));
    const jump = vi.spyOn(panel, "_jumpToConversationTurn");
    await panel._findConversationMatches("needle");
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    expect(jump.mock.calls[0][0].dataset.sequence).toBe("27");
    expect(list.querySelector('[data-sequence="27"] mark').textContent).toBe("needle");
    expect(list.querySelector('[data-sequence="2"] mark')).toBeNull();
  });
  it("uses a separate retrieval identity for an unknown legacy anchor", async () => {
    const panel = setup();
    const list = panel.shadowRoot.getElementById("message-list");
    const unrelated = panel._renderMessage("user", "unrelated message", 2); list.append(unrelated);
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([{ ...result(2), anchor_cursor: null, revision_cursor: 40 }]) : { role: "assistant", text: "needle old", anchor_cursor: null, revision_cursor: 40 });
    const jump = vi.spyOn(panel, "_jumpToConversationTurn");
    await panel._findConversationMatches("needle");
    expect(jump).not.toHaveBeenCalled();
    expect(unrelated.querySelector("mark")).toBeNull();
    const earlier = list.querySelector('[data-conversation-search-sequence="2"]');
    expect(earlier.dataset.sequence).toBe("search-2");
    expect(earlier.querySelector(".message-state").textContent).toContain("earlier history");
    expect(earlier.querySelector("mark").textContent).toBe("needle");
  });
  it.each(["message.removed", "message.updated"])("does not resurrect a cached match after observed %s", async (eventType) => {
    const panel = setup();
    panel._callWS.mockImplementation(async (action) => action === "search_conversation" ? page([{ ...result(2), anchor_cursor: 27, revision_cursor: 40 }]) : { role: "user", text: "needle", anchor_cursor: 27, revision_cursor: 40 });
    await panel._findConversationMatches("needle");
    vi.spyOn(panel, "_runActivityForThread").mockReturnValue({ busy: false });
    vi.spyOn(panel, "_renderConversationTimeline").mockImplementation(() => {});
    panel._events = [{ sequence: 41, event_type: eventType, payload: { message_sequence: 2, role: "user", text: "changed" } }];
    panel._forceMessageRebuild = true; panel._renderMessages();
    expect(panel._conversationSearchState.message).toBeNull();
    expect(panel._conversationSearchState.error).toBe(true);
    expect(panel.shadowRoot.querySelector("[data-conversation-search-inserted]")).toBeNull();
    expect(panel.shadowRoot.querySelector("mark.conversation-text-match")).toBeNull();
  });
  it("does not call unsupported capabilities", async () => {
    const panel = setup(); panel._config.capabilities = [];
    await panel._findConversationMatches("needle");
    expect(panel._callWS).not.toHaveBeenCalled();
  });
});
