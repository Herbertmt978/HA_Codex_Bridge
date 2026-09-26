import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";
import { chatMenuCss, conversationCopy } from "../src/chat-context-menu.js";

const thread = (id, extra = {}) => ({ thread_id: id, title: `Chat ${id}`, status: "idle", project_id: "home", project_kind: "project", mode: "edit", attachments: [], pinned: false, unread: false, section_id: null, navigation_revision: 1, ...extra });

function setup({ supported = true } = {}) {
  const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
  panel._config = { capabilities: supported ? ["chat_operations_v1"] : [] };
  panel._activeDestination = "chats";
  panel._projects = [{ project_id: "home", name: "Home", kind: "project" }, { project_id: "other", name: "Other", kind: "project" }];
  panel._threads = [thread("one"), thread("two")]; panel._selectedThreadId = "one"; panel._activeThread = panel._threads[0];
  panel._callWS = vi.fn(async (operation, payload) => {
    if (operation === "list_chat_sections") return { sections: [{ section_id: "work", name: "Work", revision: 1 }] };
    if (operation === "list_artifacts") return [];
    if (operation === "update_thread" || operation === "move_thread_project") return { ...panel._threads.find((item) => item.thread_id === payload.thread_id), ...payload, navigation_revision: (payload.navigation_revision || 1) + 1 };
    return {};
  });
  panel._renderNavigationSections(); panel._renderChatControls();
  const menu = panel._chatContextMenu;
  return { panel, menu, show: async (id = "two") => menu.show(id, panel.shadowRoot.querySelector(`[data-thread-id="${id}"].thread-actions-toggle`)) };
}

const key = (target, value, extra = {}) => { const event = new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true, ...extra }); target.dispatchEvent(event); return event; };

describe("shared chat context actions", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
  afterEach(() => { vi.useRealTimers(); document.body.replaceChildren(); });

  it("opens the owned right-click menu without selecting the chat", async () => {
    const fixture = setup(); const { panel, menu } = fixture;
    const select = vi.spyOn(panel, "_selectThread");
    const row = panel.shadowRoot.querySelector('[data-chat-thread-id="two"]');
    const event = new MouseEvent("contextmenu", { clientX: 850, clientY: 650, bubbles: true, cancelable: true });
    row.dispatchEvent(event);
    await vi.waitFor(() => expect(menu.sectionsLoaded).toBe(true));
    expect(event.defaultPrevented).toBe(true); expect(select).not.toHaveBeenCalled(); expect(panel._selectedThreadId).toBe("one");
    expect(menu.threadId).toBe("two"); expect(menu.menu.hidden).toBe(false);
    expect([...menu.menu.querySelectorAll(".chat-menu-root button")].map((item) => item.textContent.replace(/\s+/g, " ").trim())).toEqual(expect.arrayContaining(["RenameAlt+Ctrl+R", "PinAlt+Ctrl+P", "Permanently delete", "Project", "Section", "Share", "Copy", "Fork", "Open in new window"]));
    for (const item of menu.menu.querySelectorAll("button")) { expect(item.hasAttribute("title")).toBe(false); expect(item.dataset.tooltip).toBeUndefined(); }
  });

  it("keeps established actions on an older App and refuses missing new operations", async () => {
    const { panel, menu, show } = setup({ supported: false }); await show();
    const labels = menu.menu.textContent;
    for (const missing of ["Pin", "Mark as unread", "Project", "Section", "Fork"]) expect(labels).not.toContain(missing);
    for (const action of ["pin", "unread", "section", "move", "fork", "create-section"]) await menu.perform(action, "work");
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(labels).toContain("Rename"); expect(labels).toContain("Archive"); expect(labels).toContain("Copy");
  });

  it("offers only supported project destinations and refuses a stale special destination", async () => {
    const { panel, menu, show } = setup();
    panel._projects.push({ project_id: "direct", name: "Direct chats", kind: "direct" }, { project_id: "imported", name: "Imported", kind: "imported" });
    await show();
    expect(menu.entries("project").filter((item) => item?.action === "move").map((item) => item.value)).toEqual(["other"]);
    panel._callWS.mockClear();
    await menu.perform("move", "direct");
    await menu.perform("move", "imported");
    expect(panel._callWS).not.toHaveBeenCalled();
  });

  it.each([["pin", { pinned: true }], ["unread", { unread: true }], ["section", { section_id: "work" }]])("sends only the chosen revision-bound %s metadata", async (action, update) => {
    const { panel, menu, show } = setup(); await show(); await menu.perform(action, "work");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", navigation_revision: 1, ...update });
    expect(panel._selectedThreadId).toBe("one"); expect(panel._threads[1]).toMatchObject(update);
  });

  it("renames an unselected chat with a bounded, accessible review field", async () => {
    const { panel, menu, show } = setup({ supported: false }); await show(); await menu.perform("rename");
    await vi.waitFor(() => expect(panel.shadowRoot.activeElement?.tagName).toBe("INPUT"));
    const field = menu.dialog.querySelector("input"); expect(field.maxLength).toBe(160);
    field.value = "A renamed chat"; menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", title: "A renamed chat" });
    expect(panel._selectedThreadId).toBe("one");
  });

  it("uses canonical archive/restore and delete confirmation methods", async () => {
    const { panel, menu, show } = setup();
    const archive = vi.spyOn(panel, "_archiveThread").mockResolvedValue(); const restore = vi.spyOn(panel, "_restoreThread").mockResolvedValue();
    const remove = vi.spyOn(panel, "_deleteThread").mockResolvedValue();
    await show(); await menu.perform("archive"); expect(archive).toHaveBeenCalledWith("two");
    panel._threads[1].archived_at = "2026-09-26"; await show(); await menu.perform("archive"); expect(restore).toHaveBeenCalledWith("two");
    await show(); const trigger = menu.trigger; await menu.perform("delete"); expect(remove).toHaveBeenCalledWith("two", trigger); expect(panel._callWS).not.toHaveBeenCalledWith("delete_thread", expect.anything());
  });

  it("reviews moving to the chosen project before copying any ordinary project file", async () => {
    const { panel, menu, show } = setup(); await show(); await menu.perform("submenu", "project");
    expect(menu.menu.textContent).toContain("choose any project files"); expect(menu.menu.querySelector('.chat-menu-submenu [data-chat-action="move"]').textContent.trim()).toBe("Other");
    await menu.perform("move", "other");
    expect(panel._callWS).toHaveBeenCalledWith("list_artifacts", { thread_id: "two" });
    expect(panel._callWS).not.toHaveBeenCalledWith("move_thread_project", expect.anything());
    expect(menu.dialog.textContent).toContain("Other"); expect(menu.dialog.textContent).toContain("copied automatically");
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("move_thread_project", { thread_id: "two", project_id: "other", navigation_revision: 1, workspace_artifact_ids: [] });
  });

  it("copies exactly selected workspace artifact IDs with safe names and excludes private files from the optional list", async () => {
    const { panel, menu, show } = setup(); await show();
    const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "list_artifacts" ? Promise.resolve([
      { artifact_id: "chosen", filename: '<img src=x onerror="boom()">.txt', relative_path: "folder/report.txt", source: "workspace" },
      { artifact_id: "leave", filename: "Unrelated.txt", relative_path: "elsewhere/Unrelated.txt", source: "workspace" },
      { artifact_id: "private", filename: "Capture.png", source: "browser_capture" },
      { artifact_id: "owned-copy", filename: "Earlier.txt", relative_path: "earlier/Earlier.txt", source: "workspace", copied_for_chat: true },
    ]) : original(operation, payload));
    await menu.perform("move", "other");
    const choices = [...menu.dialog.querySelectorAll('input[type="checkbox"]')];
    expect(choices).toHaveLength(2); expect(choices.every((input) => !input.checked)).toBe(true);
    expect(menu.dialog.textContent).not.toContain("Earlier.txt");
    expect(menu.dialog.textContent).toContain("files copied by an earlier move");
    expect(menu.dialog.querySelector("img,script")).toBeNull(); expect(menu.dialog.textContent).toContain("folder/report.txt");
    choices[0].checked = true;
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("move_thread_project", { thread_id: "two", project_id: "other", navigation_revision: 1, workspace_artifact_ids: ["chosen"] });
    expect(panel._selectedThreadId).toBe("one");
  });

  it("refuses malformed workspace copy ownership without moving the chat", async () => {
    const { panel, menu, show } = setup(); await show();
    panel._callWS.mockImplementation(() => Promise.resolve([
      { artifact_id: "invalid", filename: "Report.txt", relative_path: "Report.txt", source: "workspace", copied_for_chat: "true" },
    ]));
    await menu.perform("move", "other");
    expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true);
    expect(menu.dialog.textContent).toContain("could not be loaded or verified");
    expect(panel._callWS).not.toHaveBeenCalledWith("move_thread_project", expect.anything());
  });

  it("cancels a move without mutations and ignores a late file-list reply", async () => {
    const { panel, menu, show } = setup(); await show(); let resolve;
    panel._callWS.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const pending = menu.perform("move", "other");
    expect(menu.dialog.querySelector('button[type="submit"]').disabled).toBe(true);
    menu.dialog.querySelector('button[type="button"]').click();
    resolve([{ artifact_id: "old", filename: "Old.txt", relative_path: "Old.txt", source: "workspace" }]); await pending;
    expect(menu.dialog.hidden).toBe(true); expect(menu.moveReview).toBeNull();
    expect(panel._callWS).not.toHaveBeenCalledWith("move_thread_project", expect.anything());
    expect(panel.shadowRoot.activeElement.dataset.threadId).toBe("two");
  });

  it.each(["capability", "destination", "destination-workspace", "revision", "busy"])("refuses a move when %s changes during review", async (change) => {
    const { panel, menu, show } = setup(); await show(); await menu.perform("move", "other");
    if (change === "capability") panel._config.capabilities = [];
    if (change === "destination") panel._projects[1].archived_at = "today";
    if (change === "destination-workspace") panel._projects[1].root_path = "changed-workspace";
    if (change === "revision") panel._threads[1].navigation_revision += 1;
    if (change === "busy") vi.spyOn(panel, "_runActivityForThread").mockReturnValue({ busy: true });
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true));
    expect(panel._callWS).not.toHaveBeenCalledWith("move_thread_project", expect.anything());
    expect(menu.uncertain.has("two")).toBe(false);
  });

  it("bounds optional move files to 100 unchecked choices and preserves selection during polling", async () => {
    const { panel, menu, show } = setup(); await show(); const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "list_artifacts" ? Promise.resolve(Array.from({ length: 101 }, (_, index) => ({ artifact_id: `file-${index}`, filename: `File ${index}.txt`, relative_path: `File ${index}.txt`, source: "workspace" }))) : original(operation, payload));
    await menu.perform("move", "other"); const choices = [...menu.dialog.querySelectorAll("input")];
    expect(choices).toHaveLength(100); expect(choices.every((input) => !input.checked)).toBe(true); expect(menu.dialog.textContent).toContain("first 100");
    choices[3].checked = true; panel._renderChatControls();
    expect(menu.dialog.querySelectorAll("input")[3]).toBe(choices[3]); expect(choices[3].checked).toBe(true);
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("move_thread_project", { thread_id: "two", project_id: "other", navigation_revision: 1, workspace_artifact_ids: ["file-3"] });
  });

  it("retains a move selection and blocks duplicate writes after lost acknowledgement until explicit refresh", async () => {
    const { panel, menu, show } = setup(); await show(); let reject; const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "list_artifacts" ? Promise.resolve([{ artifact_id: "chosen", filename: "Hello.txt", relative_path: "Hello.txt", source: "workspace" }]) : operation === "move_thread_project" ? new Promise((_, fail) => { reject = fail; }) : original(operation, payload));
    await menu.perform("move", "other"); const choice = menu.dialog.querySelector("input"); choice.checked = true; const form = menu.dialog.querySelector("form");
    form.dispatchEvent(new Event("submit", { cancelable: true })); form.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(panel._callWS.mock.calls.filter(([operation]) => operation === "move_thread_project")).toHaveLength(1);
    reject(new Error("Connection dropped")); await vi.waitFor(() => expect(menu.uncertain.has("two")).toBe(true));
    expect(choice.checked).toBe(true); expect(menu.dialog.textContent).toContain("check the result");
    form.dispatchEvent(new Event("submit", { cancelable: true })); expect(panel._callWS.mock.calls.filter(([operation]) => operation === "move_thread_project")).toHaveLength(1);
    menu.closeDialog(); await show(); await menu.perform("move", "other"); expect(menu.dialog.hidden).toBe(true);
    panel._loadProjects = vi.fn().mockResolvedValue(); panel._loadThreads = vi.fn().mockResolvedValue();
    await menu.perform("refresh"); expect(menu.uncertain.has("two")).toBe(false);
  });

  it.each(["thread_has_scheduled_automation", "runtime_thread_operation_conflict"])("keeps other chat actions available after a definitive %s move rejection", async (code) => {
    const { panel, menu, show } = setup(); await show(); const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "move_thread_project" ? Promise.reject({ code, message: "This move was rejected." }) : original(operation, payload));
    await menu.perform("move", "other");
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true));
    expect(panel._callWS.mock.calls.filter(([operation]) => operation === "move_thread_project")).toHaveLength(1);
    expect(menu.uncertain.has("two")).toBe(false); expect(menu.busy.has("two")).toBe(false);
    menu.closeDialog(); await show();
    expect(menu.menu.querySelector('[data-chat-action="pin"]').disabled).toBe(false);
    await menu.perform("pin");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", pinned: true, navigation_revision: 1 });
  });

  it("keeps chat actions available after a definitive native fork conflict", async () => {
    const { panel, menu, show } = setup(); await show(); const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "fork_thread" ? Promise.reject({ code: "runtime_thread_operation_conflict", message: "The chat changed." }) : original(operation, payload));
    await menu.perform("fork");
    expect(menu.uncertain.has("two")).toBe(false); expect(menu.busy.has("two")).toBe(false);
    expect(menu.menu.querySelector('[data-chat-action="pin"]').disabled).toBe(false);
    await menu.perform("pin");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", pinned: true, navigation_revision: 1 });
  });

  it("keeps the existing source writable when the move destination disappears before server dispatch", async () => {
    const { panel, menu, show } = setup(); await show(); const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => operation === "move_thread_project" ? Promise.reject({ code: "not_found", message: "The destination no longer exists." }) : original(operation, payload));
    await menu.perform("move", "other");
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("move_thread_project", { thread_id: "two", project_id: "other", navigation_revision: 1, workspace_artifact_ids: [] });
    expect(menu.uncertain.has("two")).toBe(false); expect(panel._threads.find((item) => item.thread_id === "two").project_id).toBe("home");
    menu.closeDialog(); await show(); await menu.perform("pin");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", pinned: true, navigation_revision: 1 });
  });

  it.each(["move", "fork"])("blocks duplicate native writes after an explicitly unknown %s outcome", async (action) => {
    const { panel, menu, show } = setup(); await show(); const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation((operation, payload) => ["move_thread_project", "fork_thread"].includes(operation) ? Promise.reject({ code: "runtime_thread_operation_unknown", message: "The native result could not be confirmed." }) : original(operation, payload));
    if (action === "move") {
      await menu.perform("move", "other"); menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
      await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true)); menu.closeDialog();
    } else await menu.perform("fork");
    expect(menu.uncertain.has("two")).toBe(true); await show();
    await menu.perform("fork"); await menu.perform("move", "other"); await menu.perform("pin");
    expect(panel._callWS.mock.calls.filter(([operation]) => operation === "move_thread_project")).toHaveLength(action === "move" ? 1 : 0);
    expect(panel._callWS.mock.calls.filter(([operation]) => operation === "fork_thread")).toHaveLength(action === "fork" ? 1 : 0);
    expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.anything());
  });

  it("provides a trapped accessible move dialog and reachable cancellation while loading on mobile", async () => {
    const originalWidth = window.innerWidth; Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      const { panel, menu, show } = setup(); await show(); let resolve; panel._callWS.mockImplementation(() => new Promise((done) => { resolve = done; }));
      const pending = menu.perform("move", "other"); await Promise.resolve();
      const dialog = menu.dialog.querySelector('[role="dialog"]'); expect(dialog.getAttribute("aria-modal")).toBe("true"); expect(dialog.getAttribute("aria-labelledby")).toBe("chat-menu-dialog-title");
      const cancel = dialog.querySelector('button[type="button"]'); expect(panel.shadowRoot.activeElement).toBe(cancel);
      expect(key(cancel, "Tab").defaultPrevented).toBe(true); expect(panel.shadowRoot.activeElement).toBe(cancel);
      key(cancel, "Escape"); expect(menu.dialog.hidden).toBe(true); resolve([]); await pending;
      expect(chatMenuCss).toContain("max-height:min(36dvh,320px)"); expect(chatMenuCss).toContain("min-height:44px");
    } finally { Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth }); }
  });

  it("keeps a newer move review intact when an abandoned artifact read completes late", async () => {
    const { panel, menu, show } = setup(); await show(); let resolve;
    panel._callWS.mockImplementation((operation) => operation === "list_artifacts" ? new Promise((done) => { resolve = done; }) : Promise.resolve({ sections: menu.sections }));
    const oldRead = menu.perform("move", "other"); const completeOld = resolve;
    menu.closeDialog(); await show("one");
    panel._callWS.mockResolvedValue([{ artifact_id: "new", filename: "New.txt", relative_path: "New.txt", source: "workspace" }]);
    await menu.perform("move", "other"); const choice = menu.dialog.querySelector("input"); choice.checked = true;
    completeOld([{ artifact_id: "old", filename: "Old.txt", relative_path: "Old.txt", source: "workspace" }]); await oldRead;
    expect(menu.moveReview.threadId).toBe("one"); expect(menu.dialog.querySelector("input")).toBe(choice); expect(choice.checked).toBe(true); expect(menu.dialog.textContent).not.toContain("Old.txt");
  });

  it.each([null, [{ source: "workspace", artifact_id: "file", filename: "File.txt" }], [
    { source: "workspace", artifact_id: "same", filename: "File.txt", relative_path: "File.txt" },
    { source: "workspace", artifact_id: "same", filename: "Other.txt", relative_path: "Other.txt" },
  ]])("refuses an unverified optional file list without writing", async (artifacts) => {
    const { panel, menu, show } = setup(); await show(); panel._callWS.mockResolvedValue(artifacts);
    await menu.perform("move", "other"); expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true);
    expect(menu.dialog.textContent).toContain("could not be loaded or verified");
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    expect(panel._callWS).not.toHaveBeenCalledWith("move_thread_project", expect.anything()); expect(menu.uncertain.has("two")).toBe(false);
  });

  it("forks through the native backend and adopts the returned conversation", async () => {
    const { panel, menu, show } = setup(); const created = thread("fork"); const adopt = vi.spyOn(panel, "_adoptCreatedThread").mockImplementation(() => {});
    await show(); panel._callWS.mockResolvedValue(created); await menu.perform("submenu", "fork"); expect(menu.menu.textContent).toContain("existing workspace");
    await menu.perform("fork"); expect(panel._callWS).toHaveBeenCalledWith("fork_thread", { thread_id: "two" }); expect(adopt).toHaveBeenCalledWith(created);
  });

  it("creates, renames and removes persisted sections without deleting chats", async () => {
    const { panel, menu, show } = setup(); await show();
    panel._callWS.mockImplementation(async (operation, payload) => operation === "list_chat_sections" ? { sections: structuredClone(menu.sections) } : operation === "create_chat_section" ? { section_id: "new", name: payload.name, revision: 1 } : operation === "update_thread" ? { ...panel._threads[1], ...payload } : {});
    await menu.perform("create-section"); menu.dialog.querySelector("input").value = "Research"; menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true));
    expect(panel._callWS).toHaveBeenCalledWith("create_chat_section", { name: "Research" }); expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", section_id: "new", navigation_revision: 1 });
    panel._loadThreads = vi.fn().mockResolvedValue();
    panel._callWS.mockImplementation(async (operation, payload) => operation === "list_chat_sections" ? { sections: structuredClone(menu.sections) } : operation === "update_chat_section" ? { section_id: "new", name: payload.name, revision: 2 } : {});
    await show(); await menu.perform("rename-section", "new"); menu.dialog.querySelector("input").value = "Planning"; menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true)); expect(menu.sections.find((item) => item.section_id === "new").name).toBe("Planning");
    await show(); await menu.perform("remove-section", "new"); expect(menu.dialog.textContent).toContain("Chats in this section stay available"); menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.hidden).toBe(true)); expect(panel._callWS).toHaveBeenCalledWith("delete_chat_section", { section_id: "new", revision: 2 }); expect(panel._callWS).not.toHaveBeenCalledWith("delete_thread", expect.anything());
  });

  it.each(["create-section", "rename-section", "remove-section"].flatMap((action) => ["chat_section_conflict", "section_revision_conflict", "chat_section_not_found"].map((code) => [action, code])))("keeps chat actions available after %s receives definitive %s", async (action, code) => {
    const { panel, menu, show } = setup(); await show();
    const operation = action === "create-section" ? "create_chat_section" : action === "rename-section" ? "update_chat_section" : "delete_chat_section";
    const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation(async (method, payload) => {
      if (method === operation) throw Object.assign(new Error("The section changed. Refresh and try again."), { code });
      return original(method, payload);
    });
    await menu.perform(action, "work"); const input = menu.dialog.querySelector("input"); if (input) input.value = "Research";
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true));
    expect(menu.uncertain.has("two")).toBe(false); expect(menu.busy.has("two")).toBe(false);
    menu.dialog.querySelector('button[type="button"]').click(); await show();
    expect(menu.menu.querySelector('[data-chat-action="pin"]').disabled).toBe(false);
    await menu.perform("pin");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", navigation_revision: 1, pinned: true });
    expect(panel._threads[1].pinned).toBe(true); expect(panel._selectedThreadId).toBe("one");
  });

  it.each(["create-section", "rename-section", "remove-section"])("blocks repeated writes after an unknown %s outcome without disabling another chat", async (action) => {
    const { panel, menu, show } = setup(); await show();
    const operation = action === "create-section" ? "create_chat_section" : action === "rename-section" ? "update_chat_section" : "delete_chat_section";
    const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation(async (method, payload) => {
      if (method === operation) throw Object.assign(new Error("The acknowledgement was lost."), { code: "runtime_thread_operation_unknown" });
      return original(method, payload);
    });
    await menu.perform(action, "work"); const input = menu.dialog.querySelector("input"); if (input) input.value = "Research";
    const form = menu.dialog.querySelector("form"); form.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.uncertain.has("two")).toBe(true));
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(panel._callWS.mock.calls.filter(([method]) => method === operation)).toHaveLength(1);
    menu.dialog.querySelector('button[type="button"]').click(); await show();
    expect(menu.menu.querySelector('[data-chat-action="pin"]').disabled).toBe(true);
    await menu.perform("pin"); await menu.perform(action, "work");
    expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.objectContaining({ thread_id: "two" }));
    expect(panel._callWS.mock.calls.filter(([method]) => method === operation)).toHaveLength(1);
    await show("one"); await menu.perform("pin");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "one", navigation_revision: 1, pinned: true });
  });

  it("retains a newly created section without locking the chat when assignment is definitely rejected", async () => {
    const { panel, menu, show } = setup(); await show();
    const original = panel._callWS.getMockImplementation();
    panel._callWS.mockImplementation(async (method, payload) => {
      if (method === "create_chat_section") return { section_id: "new", name: payload.name, revision: 1 };
      if (method === "update_thread" && payload.section_id) throw { body: { detail: { code: "navigation_revision_conflict" } } };
      return original(method, payload);
    });
    await menu.perform("create-section"); menu.dialog.querySelector("input").value = "Research";
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true));
    expect(menu.sections).toContainEqual({ section_id: "new", name: "Research", revision: 1 }); expect(menu.uncertain.has("two")).toBe(false);
    menu.dialog.querySelector('button[type="button"]').click(); await show(); await menu.perform("pin");
    expect(panel._threads[1].pinned).toBe(true);
    expect(panel._callWS.mock.calls.filter(([method]) => method === "create_chat_section")).toHaveLength(1);
  });

  it("shares only an authenticated link and opens with browser isolation", async () => {
    const { panel, menu, show } = setup(); const copy = vi.spyOn(panel, "_writeClipboardText").mockResolvedValue(); const open = vi.spyOn(window, "open").mockReturnValue(null);
    await show(); await menu.perform("copy-link"); expect(new URL(copy.mock.calls[0][0]).searchParams.get("thread")).toBe("two"); expect(panel.shadowRoot.getElementById("share-status").textContent).toContain("sign-in is required");
    await show(); await menu.perform("open"); expect(open).toHaveBeenCalledWith(expect.stringContaining("thread=two"), "_blank", "noopener,noreferrer");
  });

  it("copies title or bounded conversation history without switching chats or including tool data", async () => {
    const { panel, menu, show } = setup(); const copy = vi.spyOn(panel, "_writeClipboardText").mockResolvedValue();
    const history = vi.spyOn(panel, "_loadThreadEventHistory").mockResolvedValue([
      { event_type: "message.created", payload: { role: "user", text: "**Hello**" } },
      { event_type: "tool.completed", payload: { text: "private command output" } },
      { event_type: "message.completed", payload: { text: "Reply" } },
    ]);
    await show(); await menu.perform("copy-title"); expect(copy).toHaveBeenLastCalledWith("Chat two");
    await show(); await menu.perform("copy-markdown"); expect(copy).toHaveBeenLastCalledWith("## You\n**Hello**\n\n## Codex\nReply");
    await show(); await menu.perform("copy-text"); expect(copy.mock.calls.at(-1)[0]).toContain("You:\n**Hello**"); expect(copy.mock.calls.at(-1)[0]).not.toContain("private command"); expect(history).toHaveBeenCalledWith("two"); expect(panel._selectedThreadId).toBe("one");
  });

  it("roves with arrows/Home/End, opens and exits submenus, and restores focus", async () => {
    const { panel, menu, show } = setup(); await show();
    const first = menu.menu.querySelector("button"); expect(panel.shadowRoot.activeElement).toBe(first);
    key(first, "End"); expect(panel.shadowRoot.activeElement.textContent.trim()).toBe("Open in new window"); key(panel.shadowRoot.activeElement, "Home"); expect(panel.shadowRoot.activeElement).toBe(first);
    const submenu = [...menu.menu.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === "copy"); submenu.focus(); key(submenu, "ArrowRight"); expect(menu.page).toBe("copy"); expect(panel.shadowRoot.activeElement.textContent.trim()).toBe("Chat title");
    key(panel.shadowRoot.activeElement, "ArrowLeft"); expect(menu.page).toBe(null); expect(panel.shadowRoot.activeElement._chatValue).toBe("copy");
    key(panel.shadowRoot.activeElement, "Escape"); expect(menu.menu.hidden).toBe(true); expect(panel.shadowRoot.activeElement.dataset.threadId).toBe("two");
  });

  it("opens hover submenus after a short delay, with reduced-motion styling", async () => {
    const { menu, show } = setup(); await show(); vi.useFakeTimers();
    const submenu = [...menu.menu.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === "copy"); submenu.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    vi.advanceTimersByTime(219); expect(menu.page).toBe(null); vi.advanceTimersByTime(1); expect(menu.page).toBe("copy");
    expect(chatMenuCss).toContain("prefers-reduced-motion"); expect(chatMenuCss).toContain("min-height:44px");
  });

  it("clamps the portal inside the visible viewport and closes on outside pointer", async () => {
    const { panel, menu } = setup(); vi.spyOn(menu.menu, "getBoundingClientRect").mockReturnValue({ width: 348, height: 500 });
    await menu.show("two", panel.shadowRoot.querySelector('[data-thread-id="two"].thread-actions-toggle'), { x: 9999, y: 9999 });
    expect(parseInt(menu.menu.style.left)).toBe(window.innerWidth - 348 - 8); expect(parseInt(menu.menu.style.top)).toBe(window.innerHeight - 500 - 8);
    document.body.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true })); expect(menu.menu.hidden).toBe(true);
  });

  it("uses touch ellipsis and Back instead of hover-only interaction on a narrow viewport", async () => {
    const original = window.innerWidth; Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      const { panel, menu } = setup(); panel.shadowRoot.querySelector('[data-thread-id="two"].thread-actions-toggle').click();
      await vi.waitFor(() => expect(menu.sectionsLoaded).toBe(true)); await menu.perform("submenu", "copy"); expect(menu.menu.querySelector('[data-chat-action="back"]').hidden).toBe(false);
      menu.menu.querySelector('[data-chat-action="back"]').click(); expect(menu.page).toBe(null);
    } finally { Object.defineProperty(window, "innerWidth", { configurable: true, value: original }); }
  });

  it("keeps a touch Back control on a coarse-pointer tablet wider than the narrow breakpoint", async () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn((query) => ({ matches: query === "(pointer:coarse)", addEventListener() {}, removeEventListener() {} }));
    try {
      const { menu, show } = setup(); await show(); await menu.perform("submenu", "copy");
      expect(window.innerWidth).toBeGreaterThan(640); expect(menu.compact).toBe(true); expect(menu.menu.querySelector('[data-chat-action="back"]').hidden).toBe(false);
      menu.menu.querySelector('[data-chat-action="back"]').click(); expect(menu.page).toBe(null);
    } finally { window.matchMedia = original; }
  });

  it("never parses hostile chat or section names as markup", async () => {
    const { panel, menu, show } = setup(); panel._threads[1].title = '<img src=x onerror="boom()">'; panel._callWS.mockResolvedValue({ sections: [{ section_id: "hostile", name: '<script>boom()</script>', revision: 1 }] }); await show(); await menu.perform("submenu", "section");
    expect(menu.menu.querySelector("script,img")).toBeNull(); expect(menu.menu.textContent).toContain("<script>boom()</script>"); expect(menu.menu.getAttribute("aria-label")).toContain("<img");
  });

  it("scopes the advertised shortcuts and excludes editable fields and other destinations", async () => {
    const { panel, menu } = setup(); const perform = vi.spyOn(menu, "perform").mockResolvedValue(); const target = panel.shadowRoot.getElementById("thread-title-label");
    key(target, "p", { altKey: true, ctrlKey: true }); expect(perform).toHaveBeenCalledWith("pin"); expect(menu.threadId).toBe("one");
    perform.mockClear(); const input = panel.shadowRoot.getElementById("prompt-input"); expect(key(input, "a", { ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(false); expect(perform).not.toHaveBeenCalled();
    panel._activeDestination = "settings"; key(target, "r", { ctrlKey: true, altKey: true }); expect(perform).not.toHaveBeenCalled();
    panel._activeDestination = "chats"; panel._pendingDeletion = { targetId: "two" }; key(target, "u", { ctrlKey: true, shiftKey: true }); expect(perform).not.toHaveBeenCalled();
  });

  it("disables pending operations and prevents a second uncertain write", async () => {
    const { panel, menu, show } = setup(); await show(); let reject;
    panel._callWS.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    const pending = menu.perform("pin"); await menu.perform("pin"); expect(panel._callWS).toHaveBeenCalledTimes(2); // one section read, one write
    expect([...menu.menu.querySelectorAll("button")].every((item) => item.disabled)).toBe(true);
    reject(new Error("Connection dropped")); await pending; expect(menu.uncertain.has("two")).toBe(true); expect(menu.menu.textContent).toContain("check the result");
    await menu.perform("pin"); expect(panel._callWS).toHaveBeenCalledTimes(2);
  });

  it("clears unread only on explicit opening, retaining it during rendering", async () => {
    const { panel, menu } = setup(); panel._threads[1].unread = true; panel._renderNavigationSections(); expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.anything());
    await menu.markOpened("two"); expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "two", unread: false, navigation_revision: 1 });
  });

  it("shows pinned and custom groups without duplicate project rows", async () => {
    const { panel, menu, show } = setup(); await show(); panel._threads[0].pinned = true; panel._threads[1].section_id = "work"; panel._renderedNavigationKey = null; panel._renderNavigationSections();
    const groups = panel.shadowRoot.getElementById("chat-navigation-sections"); expect(groups.textContent).toContain("Pinned"); expect(groups.textContent).toContain("Work"); expect(groups.querySelectorAll(".chat-row")).toHaveLength(2); expect(panel.shadowRoot.querySelectorAll(".chat-row")).toHaveLength(2); expect(menu.isGrouped(panel._threads[1])).toBe(true);
  });

  it("can manage an empty section after its chat has moved out", async () => {
    const { menu, show } = setup(); await show(); await menu.perform("submenu", "section"); await menu.perform("manage-sections");
    expect(menu.menu.querySelector('.chat-menu-submenu [data-chat-action="manage-section"]').textContent).toContain("Work");
    await menu.perform("manage-section", "work"); expect(menu.menu.querySelector('[data-chat-action="remove-section"]')).not.toBeNull();
    await menu.perform("back"); expect(menu.page).toBe("manage-sections"); await menu.perform("back"); expect(menu.page).toBe("section");
  });

  it("updates a changed navigation projection without losing its owned source or menu focus", async () => {
    const { panel, menu, show } = setup(); await show(); const pin = menu.menu.querySelector('[data-chat-action="pin"]'); pin.focus();
    panel._threads[1] = { ...panel._threads[1], pinned: true, navigation_revision: 2 }; panel._renderChatControls();
    expect(menu.threadId).toBe("two"); expect(panel._selectedThreadId).toBe("one"); expect(panel.shadowRoot.activeElement.dataset.chatAction).toBe("pin"); expect(panel.shadowRoot.activeElement.textContent).toContain("Unpin");
    expect(panel.shadowRoot.activeElement).toBe(pin);
  });

  it("does not redraw hovered controls for unrelated runtime and navigation snapshots", async () => {
    const { panel, menu, show } = setup(); await show(); await menu.perform("submenu", "copy");
    const root = [...menu.rootPage.querySelectorAll("button")], submenu = menu.submenu;
    const copy = submenu.querySelector('[data-chat-action="copy-text"]'); copy.focus();
    const icon = copy.querySelector("svg"), render = vi.spyOn(menu, "render");
    panel._threads[1] = { ...panel._threads[1], updated_at: "later", navigation_revision: 99, context_usage: { used: 500 }, attachments: [{ filename: "file.txt" }] };
    panel._threads[0].updated_at = "unrelated";
    panel._renderChatControls();
    expect(render).not.toHaveBeenCalled(); expect(menu.submenu).toBe(submenu);
    expect([...menu.rootPage.querySelectorAll("button")]).toEqual(root);
    expect(submenu.querySelector('[data-chat-action="copy-text"]')).toBe(copy);
    expect(copy.querySelector("svg")).toBe(icon); expect(panel.shadowRoot.activeElement).toBe(copy);
  });

  it("retains every root control when switching submenus and every same-page control on render", async () => {
    const { menu, show } = setup(); await show();
    const root = [...menu.rootPage.children];
    for (const page of ["project", "section", "copy", "fork", "manage-sections", "manage-section"]) {
      menu.page = page; menu.managedSection = "work"; menu.render();
      expect([...menu.rootPage.children]).toEqual(root);
      const submenu = menu.submenu, controls = [...submenu.children];
      menu.render({ preserveFocus: true });
      expect(menu.submenu).toBe(submenu); expect([...submenu.children]).toEqual(controls);
    }
  });

  it("opens each desktop submenu once and ignores repeated hover and label/icon crossings", async () => {
    const { menu, show } = setup(); await show(); vi.useFakeTimers();
    const render = vi.spyOn(menu, "render");
    for (const page of ["project", "section", "copy", "fork"]) {
      const button = [...menu.rootPage.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === page);
      button.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(220);
      expect(menu.page).toBe(page); const count = render.mock.calls.length, submenu = menu.submenu;
      for (const child of [button, button.querySelector("svg"), button.querySelector(".chat-menu-label")]) {
        child.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: button }));
        await vi.advanceTimersByTimeAsync(300);
      }
      button.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(300);
      expect(render).toHaveBeenCalledTimes(count); expect(menu.submenu).toBe(submenu); expect(button.isConnected).toBe(true);
    }
  });

  it("cancels a pending close while returning to the active submenu trigger", async () => {
    const { menu, show } = setup(); await show(); await menu.perform("submenu", "copy"); vi.useFakeTimers();
    const submenu = menu.submenu, rename = menu.rootPage.querySelector('[data-chat-action="rename"]');
    rename.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(100);
    const copy = [...menu.rootPage.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === "copy");
    copy.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(300);
    expect(menu.page).toBe("copy"); expect(menu.submenu).toBe(submenu);
    rename.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(220);
    expect(menu.page).toBeNull(); expect(rename.isConnected).toBe(true);
  });

  it("cancels stale hover transitions when keyboard or touch navigation takes over", async () => {
    const { menu, show } = setup(); await show(); vi.useFakeTimers();
    const project = [...menu.rootPage.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === "project");
    project.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    key(menu.rootPage.querySelector('[data-chat-action="rename"]'), "ArrowDown");
    await vi.advanceTimersByTimeAsync(300); expect(menu.page).toBeNull();
    project.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    await menu.perform("submenu", "copy"); await vi.advanceTimersByTimeAsync(300); expect(menu.page).toBe("copy");
  });

  it("updates visible section names and runtime disabling in place", async () => {
    const { panel, menu, show } = setup(); await show(); await menu.perform("submenu", "section");
    const section = menu.submenu.querySelector('[data-chat-action="section"]:not(:first-child)'), controls = [...menu.rootPage.children];
    const work = [...menu.submenu.querySelectorAll('[data-chat-action="section"]')].find((button) => button._chatValue === "work");
    menu.sections[0] = { ...menu.sections[0], name: "Updated section", revision: 2 }; menu.sync();
    expect(menu.submenu.contains(section)).toBe(true); expect(work.textContent).toContain("Updated section"); expect([...menu.rootPage.children]).toEqual(controls);
    await menu.perform("submenu", "fork"); const fork = menu.submenu.querySelector('[data-chat-action="fork"]');
    vi.spyOn(panel, "_runActivityForThread").mockReturnValue({ busy: true }); menu.sync(); expect(fork.disabled).toBe(true);
    panel._runActivityForThread.mockReturnValue({ busy: false }); menu.sync(); expect(fork.disabled).toBe(false);
    expect(menu.submenu.querySelector('[data-chat-action="fork"]')).toBe(fork);
  });

  it("does not open submenus from hover in narrow or coarse layouts", async () => {
    const { menu, show } = setup(); await show(); vi.useFakeTimers();
    const originalWidth = window.innerWidth, originalMedia = window.matchMedia;
    try {
      for (const [width, coarse] of [[390, false], [900, true]]) {
        Object.defineProperty(window, "innerWidth", { configurable: true, value: width }); window.matchMedia = () => ({ matches: coarse });
        const copy = [...menu.rootPage.querySelectorAll('[data-chat-action="submenu"]')].find((item) => item._chatValue === "copy");
        copy.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); await vi.advanceTimersByTimeAsync(300);
        expect(menu.page).toBeNull(); copy.click(); expect(menu.page).toBe("copy");
        menu.submenu.querySelector('[data-chat-action="back"]').click(); expect(menu.page).toBeNull();
      }
    } finally { Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth }); window.matchMedia = originalMedia; }
  });

  it("cancels a rename without writing, and refuses a section save after capability loss", async () => {
    const { panel, menu, show } = setup(); await show(); await menu.perform("rename"); menu.dialog.querySelector('button[type="button"]').click(); expect(menu.dialog.hidden).toBe(true);
    expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.anything());
    await show(); await menu.perform("create-section"); menu.dialog.querySelector("input").value = "Test"; panel._config.capabilities = [];
    menu.dialog.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true })); await vi.waitFor(() => expect(menu.dialog.textContent).toContain("no longer available"));
    expect(panel._callWS).not.toHaveBeenCalledWith("create_chat_section", expect.anything()); expect(menu.dialog.querySelector('button[type="submit"]').hidden).toBe(true);
  });

  it("does not copy code-block action labels into conversation text", async () => {
    const { panel, menu, show } = setup(); vi.spyOn(panel, "_loadThreadEventHistory").mockResolvedValue([{ event_type: "message.completed", payload: { text: "A code block:\n```js\nconst value = 1;\n```" } }]);
    const copy = vi.spyOn(panel, "_writeClipboardText").mockResolvedValue(); await show(); await menu.perform("copy-text"); expect(copy.mock.calls[0][0]).toContain("const value = 1;"); expect(copy.mock.calls[0][0]).not.toContain("Copy code");
  });

  it("runs a metadata shortcut before any menu has been opened", async () => {
    const { panel } = setup(); const target = panel.shadowRoot.getElementById("chat-menu-button");
    key(target, "p", { ctrlKey: true, altKey: true }); await vi.waitFor(() => expect(panel._callWS).toHaveBeenCalledWith("update_thread", { thread_id: "one", pinned: true, navigation_revision: 1 }));
    expect(panel._chatContextMenu.menu.hidden).toBe(true);
  });

  it("does not close another owned menu or reopen an abandoned menu after a late action reply", async () => {
    const { panel, menu, show } = setup(); await show(); let resolve;
    panel._callWS.mockImplementation((operation) => operation === "update_thread" ? new Promise((done) => { resolve = done; }) : Promise.resolve({ sections: menu.sections }));
    const pending = menu.perform("pin"); await show("one"); resolve({ ...panel._threads[1], pinned: true, navigation_revision: 2 }); await pending;
    expect(menu.threadId).toBe("one"); expect(menu.menu.hidden).toBe(false); expect(panel._threads[1].pinned).toBe(true);
    menu.close(); let reject; panel._callWS.mockImplementation((operation) => operation === "update_thread" ? new Promise((_, fail) => { reject = fail; }) : Promise.resolve({ sections: menu.sections }));
    await show(); const failed = menu.perform("pin"); menu.close(); reject(new Error("Connection dropped")); await failed;
    expect(menu.menu.hidden).toBe(true); expect(menu.uncertain.has("two")).toBe(true);
  });
});

describe("conversation clipboard projection", () => {
  it("excludes tool roles and raw events even when they resemble message text", () => {
    expect(conversationCopy([{ event_type: "message.completed", payload: { role: "tool", text: "private" } }, { event_type: "run.completed", payload: { text: "private" } }, { event_type: "message.created", payload: { text: "hello" } }])).toBe("You:\nhello");
  });
  it("rejects an oversized conversation before building its clipboard projection", () => {
    const renderText = vi.fn(); expect(() => conversationCopy([{ event_type: "message.completed", payload: { text: "a".repeat(2_000_001) } }], { renderText })).toThrow("too large"); expect(renderText).not.toHaveBeenCalled();
  });
});
