/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import requests from "../../tests/fixtures/context_requests.json";
import problems from "../../tests/fixtures/context_problems.json";
import { contextSelection, contextReference } from "../src/workspace-context.js";
import { chatContextSelection, chatContextReference } from "../src/chat-context.js";
import "../src/codex-bridge-panel.js";

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

function panelFixture() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", capabilities: ["workspace_context_v1", "web_search_v1", "elapsed_time_limit_v1"] };
  panel._selectedThreadId = "destination";
  panel._activeThread = { thread_id: "destination", title: "Context", status: "idle", mode: "edit", attachments: [] };
  panel._render = vi.fn(() => panel._renderComposerState(panel._activeThread));
  panel._refreshActiveThread = vi.fn(async () => {});
  panel._workspaceContext.render();
  panel.shadowRoot.getElementById("prompt-input").value = "Keep my draft";
  return panel;
}

describe("panel payloads qualified by the real HA transport", () => {
  it("keeps the shared whole-file and chat-selection fixtures tied to actual producers", () => {
    const file = { ...contextSelection("notes.txt", "", ""), content_revision: "a".repeat(64) };
    const chat = { source_thread_id: "earlier-chat", content_revision: "b".repeat(64) };
    expect(requests.read_file).toEqual({ type: "codex_bridge/read_workspace_context", thread_id: "destination", ...contextSelection("notes.txt", "", "") });
    expect(requests.read_chat).toEqual({ type: "codex_bridge/read_chat_context", thread_id: "destination", ...chatContextSelection("earlier-chat", "", "", "") });
    const reference = chatContextReference(chat);
    expect(requests.refresh_chat).toEqual({ type: "codex_bridge/read_chat_context", thread_id: "destination", ...Object.fromEntries(Object.entries(reference).filter(([key]) => key !== "content_revision")) });
    expect(requests.send_file.workspace_context).toEqual([contextReference(file)]);
    expect(requests.send_chat.chat_context).toEqual([reference]);
    expect(requests.send_message.chat_context).toEqual([chatContextReference({ ...chat, message_sequence: 4 })]);
    expect(requests.send_range.chat_context).toEqual([chatContextReference({ ...chat, message_sequence: 4, start_char: 0, end_char: 3 })]);
  });

  it.each(problems.filter(({ code }) => ["stale_context", "workspace_context_limit_exceeded"].includes(code)))("unlocks explicit context recovery after $code without losing the draft", async (problem) => {
    const panel = panelFixture();
    const item = { path: "notes.txt", start_line: null, end_line: null, text: "Exact source\r\n", content_revision: "a".repeat(64), stale: false };
    panel._workspaceContext.setItems("destination", [item]);
    panel._callWS = vi.fn().mockRejectedValueOnce(Object.assign(new Error(problem.message), { code: problem.code }));
    await panel._sendPrompt();
    const first = panel._callWS.mock.calls[0][1];
    expect(panel._promptMutationForThread("destination")).toBeNull();
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("Keep my draft");
    expect(panel._workspaceContext.current()[0].text).toBe(item.text);
    expect(() => panel._workspaceContext.snapshot()).toThrow(/Refresh/);
    for (const label of ["Refresh excerpt", "Remove context"]) {
      const button = [...panel.shadowRoot.querySelectorAll("#workspace-context button")].find((node) => node.textContent === label);
      expect(button?.disabled).toBe(false);
    }
    panel._callWS.mockResolvedValueOnce({ status: "ready", ...item, text: "Reviewed fresh source\r\n", content_revision: "b".repeat(64) });
    await panel._workspaceContext.refresh(0);
    expect(panel._workspaceContext.current()[0].text).toBe("Reviewed fresh source\r\n");
    panel._callWS.mockResolvedValueOnce({});
    await panel._sendPrompt();
    const next = panel._callWS.mock.calls[2][1];
    expect(next.workspace_context[0].content_revision).toBe("b".repeat(64));
    expect(next.client_request_id).not.toBe(first.client_request_id);
  });

  it("allows an explicit corrected duration choice after a definite refusal", async () => {
    const panel = panelFixture();
    const problem = problems.find(({ code }) => code === "elapsed_time_limit_invalid");
    panel._setDurationChoice("destination", 3600);
    panel._callWS = vi.fn().mockRejectedValueOnce(Object.assign(new Error(problem.message), { code: problem.code })).mockResolvedValueOnce({});
    await panel._sendPrompt();
    expect(panel._promptMutationForThread("destination")).toBeNull();
    expect(panel._durationChoices.get("destination")).toBe(3600);
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("Keep my draft");
    const select = panel.shadowRoot.getElementById("elapsed-time-limit");
    expect(select.disabled).toBe(false);
    select.value = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await panel._sendPrompt();
    expect(panel._callWS.mock.calls[1][1]).not.toHaveProperty("max_duration_seconds");
    expect(panel._callWS.mock.calls[1][1].client_request_id).not.toBe(panel._callWS.mock.calls[0][1].client_request_id);
  });
});
