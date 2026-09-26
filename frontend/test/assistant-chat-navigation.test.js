/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

const project = (id, extra = {}) => ({ project_id: id, kind: "project", name: id, root_path: `/config/workspaces/${id}`, ...extra });
const thread = (id, projectId, extra = {}) => ({ thread_id: id, project_id: projectId, project_kind: projectId === "direct" ? "direct" : "project", title: id, status: "idle", workspace_path: `/config/workspaces/${projectId}`, effective_model: "gpt-5.6", effective_thinking_level: "medium", navigation_revision: 1, ...extra });

function setup({ supported = true } = {}) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", capabilities: supported ? ["chat_operations_v1"] : [] };
  panel._projects = [project("direct", { kind: "direct" }), project("assistant-only"), project("mixed"), project("empty")];
  panel._threads = [
    thread("assistant-direct", "direct", { schedule_eligible: false, pinned: true }),
    thread("assistant-project", "assistant-only", { schedule_eligible: false, section_id: "work" }),
    thread("assistant-mixed", "mixed", { schedule_eligible: false }),
    thread("normal", "mixed", { schedule_eligible: true }),
    thread("legacy", "direct"),
  ];
  panel._chatContextMenu.sections = [{ section_id: "work", name: "Work", revision: 1 }];
  panel._chatContextMenu.sectionsAttempted = true;
  panel._callWS = vi.fn(async (operation) => operation === "list_chat_sections" ? { sections: panel._chatContextMenu.sections } : {});
  render(panel);
  return panel;
}

function render(panel) {
  panel._renderedNavigationKey = null;
  panel._renderNavigationSections();
  panel._renderNavigationEmptyState();
}

const rows = (panel, section) => [...panel.shadowRoot.querySelectorAll(`${section} .chat-row`)].map((row) => row.dataset.chatThreadId);

describe("HA Assistant Chats sidebar group", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
  afterEach(() => document.body.replaceChildren());

  it("groups only the explicit public Assist marker and preserves mixed and empty ordinary projects", () => {
    const panel = setup();
    panel._threads.push(thread("string-marker", "mixed", { schedule_eligible: "false" }));
    render(panel);
    expect(rows(panel, "#assistant-section")).toEqual(["assistant-direct", "assistant-project", "assistant-mixed"]);
    expect(rows(panel, "#direct-section")).toEqual(["legacy"]);
    expect(rows(panel, "#project-section")).toEqual(["normal", "string-marker"]);
    expect(panel.shadowRoot.querySelector('#project-section [data-project-id="assistant-only"]')).toBeNull();
    expect(panel.shadowRoot.querySelector('#project-section [data-project-id="mixed"]')).not.toBeNull();
    expect(panel.shadowRoot.querySelector('#project-section [data-project-id="empty"]')).not.toBeNull();
    expect(panel.shadowRoot.querySelector("#assistant-section .section-name").textContent).toBe("HA Assistant Chats");
    expect(panel.shadowRoot.querySelector("#assistant-section .section-count").textContent.trim()).toBe("3");
  });

  it("gives Assist grouping precedence over pin and custom sections without mutating membership or files", () => {
    const panel = setup();
    const snapshot = structuredClone(panel._threads);
    expect(rows(panel, "#chat-navigation-sections")).toEqual([]);
    for (const item of panel._threads) expect(panel.shadowRoot.querySelectorAll(`[data-chat-thread-id="${item.thread_id}"]`)).toHaveLength(1);
    expect(panel._threads).toEqual(snapshot);
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel._chatContextMenu.isGrouped(panel._threads[0])).toBe(false);
  });

  it("works without chat-operations capability and hides the absent Assist group for legacy data", () => {
    const panel = setup({ supported: false });
    expect(rows(panel, "#assistant-section")).toHaveLength(3);
    panel._threads = panel._threads.map((item) => { const legacy = { ...item }; delete legacy.schedule_eligible; return legacy; });
    render(panel);
    expect(panel.shadowRoot.getElementById("assistant-section").children).toHaveLength(0);
    expect(rows(panel, "#direct-section")).toEqual(["assistant-direct", "legacy"]);
    expect(rows(panel, "#project-section")).toEqual(["assistant-project", "assistant-mixed", "normal"]);
  });

  it("retains explicit and project-inherited archives only in the Assist archive disclosure", () => {
    const panel = setup();
    panel._threads[0].archived_at = "2026-09-26T12:00:00Z";
    panel._projects[1].archived_at = "2026-09-26T12:00:00Z";
    panel._threads.push(thread("normal-archive", "mixed", { archived_at: "2026-09-26T12:00:00Z" }));
    render(panel);
    expect(rows(panel, "#assistant-archived-chat-list")).toEqual(["assistant-direct", "assistant-project"]);
    expect(rows(panel, "#archived-section")).toEqual(["normal-archive"]);
    expect(panel.shadowRoot.getElementById("assistant-archived-chat-list").hidden).toBe(true);
    const restore = panel.shadowRoot.querySelector('#assistant-section [data-action="restore-project"]');
    expect(restore.dataset.projectId).toBe("assistant-only");
    expect(restore.getAttribute("aria-label")).toBe("Restore assistant-only project");
    const invoke = vi.spyOn(panel, "_restoreProject").mockResolvedValue();
    restore.click();
    expect(invoke).toHaveBeenCalledWith("assistant-only");
    panel._projects[1].archived_at = null;
    panel._threads[0].archived_at = null;
    render(panel);
    expect(panel.shadowRoot.getElementById("assistant-archived-chat-list")).toBeNull();
    expect(rows(panel, "#assistant-section")).toHaveLength(3);
  });

  it("searches Assist titles and source projects, expands matching archives, and reports only genuine empty searches", () => {
    const panel = setup();
    panel._threads[1].archived_at = "2026-09-26T12:00:00Z";
    panel._searchQuery = "assistant-project";
    render(panel);
    expect(rows(panel, "#assistant-section")).toEqual(["assistant-project"]);
    expect(panel.shadowRoot.getElementById("assistant-archived-chat-list").hidden).toBe(false);
    expect(panel.shadowRoot.getElementById("rail-search-empty").hidden).toBe(true);
    expect(rows(panel, "#project-section")).toEqual([]);
    panel._searchQuery = "assistant-only";
    render(panel);
    expect(rows(panel, "#assistant-section")).toEqual(["assistant-project"]);
    panel._searchQuery = "not present";
    render(panel);
    expect(panel.shadowRoot.getElementById("assistant-section").children).toHaveLength(0);
    expect(panel.shadowRoot.getElementById("rail-search-empty").hidden).toBe(false);
  });

  it("maintains accessible collapse state across polling renders and temporary search expansion", () => {
    const panel = setup();
    const toggle = () => panel.shadowRoot.querySelector('#assistant-section [data-section="assistant"]');
    expect(toggle().getAttribute("aria-controls")).toBe("assistant-chat-list");
    toggle().click();
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(panel.shadowRoot.getElementById("assistant-chat-list").hidden).toBe(true);
    panel._threads[0].updated_at = "2026-09-26T12:30:00Z";
    render(panel);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    panel._searchQuery = "assistant-direct";
    render(panel);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    panel._searchQuery = "";
    render(panel);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
  });

  it("uses the canonical owned chat menu without selecting or marking the Assist chat read", async () => {
    const panel = setup();
    panel._selectedThreadId = "normal";
    panel._threads[0].unread = true;
    render(panel);
    const select = vi.spyOn(panel, "_selectThread").mockResolvedValue();
    const row = panel.shadowRoot.querySelector('[data-chat-thread-id="assistant-direct"]');
    row.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 40, clientY: 80 }));
    await vi.waitFor(() => expect(panel._chatContextMenu.menu.hidden).toBe(false));
    expect(panel._chatContextMenu.threadId).toBe("assistant-direct");
    expect(select).not.toHaveBeenCalled();
    expect(panel._threads[0].unread).toBe(true);
    expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.anything());
    panel._chatContextMenu.close();
    panel.shadowRoot.querySelector('[data-chat-thread-id="assistant-direct"] .chat-select').click();
    expect(select).toHaveBeenCalledWith("assistant-direct");
  });

  it("renders untrusted chat and archived project names as text", () => {
    const panel = setup();
    panel._threads[1].title = '<img src=x onerror="alert(1)">';
    panel._projects[1].name = '<script>alert(1)</script>';
    panel._projects[1].archived_at = "2026-09-26T12:00:00Z";
    render(panel);
    const section = panel.shadowRoot.getElementById("assistant-section");
    expect(section.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(section.textContent).toContain('<script>alert(1)</script>');
    expect(section.querySelector("img,script")).toBeNull();
  });

  it.each([false, true])("selects only ordinary chats from a mixed project header, archived project: %s", (archived) => {
    const panel = setup();
    if (archived) { panel._projects[2].archived_at = "2026-09-26T12:00:00Z"; panel._collapsedSections.archived = false; }
    render(panel);
    const select = vi.spyOn(panel, "_selectThread").mockResolvedValue();
    panel.shadowRoot.querySelector(`[data-action="select-project"][data-project-id="mixed"]`).click();
    expect(select).toHaveBeenCalledWith("normal");
  });
});
