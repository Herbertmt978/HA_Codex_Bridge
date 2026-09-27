/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

const assist = { thread_id: "assist", project_id: "direct", schedule_eligible: false };
const ordinary = { thread_id: "ordinary", project_id: "direct" };

function setup(threads) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { capabilities: [] };
  panel._projects = [{ project_id: "direct", kind: "direct" }];
  panel._threads = threads;
  panel._callWS = vi.fn(async (action, payload) => {
    if (action === "list_threads") return threads;
    if (action === "archive_thread") return { ...threads.find((t) => t.thread_id === payload.thread_id), archived_at: "2026-09-27T08:00:00Z" };
    return {};
  });
  vi.spyOn(panel, "_refreshSelectedThreadAndStartPolling").mockResolvedValue(true);
  return panel;
}

describe("deliberate Assist selection", () => {
  beforeEach(() => { document.body.replaceChildren(); window.history.replaceState({}, "", "/"); });
  afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("prefers an ordinary active chat when Assist comes first", async () => {
    const panel = setup([assist, ordinary]);
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
  });

  it.each([{ threads: [assist] }, { threads: [] }])("leaves a usable empty selection with inventory %j", async ({ threads }) => {
    const panel = setup(threads);
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBeNull();
    expect(panel._refreshSelectedThreadAndStartPolling).not.toHaveBeenCalled();
    expect(panel._callWS).toHaveBeenCalledExactlyOnceWith("list_threads", { include_archived: true });
  });

  it("discards an Assist selection restored without a deliberate current-visit choice", async () => {
    const panel = setup([assist, ordinary]);
    panel._selectedThreadId = "assist";
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
  });

  it("preserves manual Assist selection through subsequent refreshes", async () => {
    const panel = setup([assist, ordinary]);
    await panel._selectThread("assist");
    await panel._loadThreads();
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("assist");
  });

  it("honours a deliberate Assist deep link", async () => {
    window.history.replaceState({}, "", "/?thread=assist");
    const panel = setup([assist, ordinary]);
    await panel._loadThreads();
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("assist");
  });

  it("does not choose an archived ordinary project as the default", async () => {
    const panel = setup([assist, { ...ordinary, project_id: "archived" }]);
    panel._projects.push({ project_id: "archived", archived_at: "2026-09-27T08:00:00Z" });
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBeNull();
  });

  it("uses an ordinary replacement after removal rather than Assist", async () => {
    const panel = setup([assist, ordinary, { ...ordinary, thread_id: "replacement" }]);
    panel._selectedThreadId = "ordinary";
    await panel._deleteThread("ordinary", null, true);
    expect(panel._selectedThreadId).toBe("replacement");
  });

  it("leaves no selected chat after archiving the only ordinary chat", async () => {
    const panel = setup([assist, ordinary]);
    panel._selectedThreadId = "ordinary";
    await panel._archiveThread("ordinary");
    expect(panel._selectedThreadId).toBeNull();
  });

  it.each(["stale", "archived"])("replaces a restored %s choice with an active ordinary chat", async (id) => {
    const panel = setup([assist, { ...ordinary, thread_id: "archived", archived_at: "2026-09-27T08:00:00Z" }, ordinary]);
    panel._selectedThreadId = id;
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
  });

  it("preserves deliberate archived viewing on refresh", async () => {
    const panel = setup([{ ...assist, archived_at: "2026-09-27T08:00:00Z" }, ordinary]);
    await panel._selectThread("assist");
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("assist");
  });

  it("replaces a non-deliberate choice in an archived project", async () => {
    const panel = setup([assist, { ...ordinary, thread_id: "saved", project_id: "archived" }, ordinary]);
    panel._projects.push({ project_id: "archived", archived_at: "2026-09-27T08:00:00Z" });
    panel._selectedThreadId = "saved";
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
  });

  it("preserves an ordinary restored choice rather than choosing the first chat", async () => {
    const panel = setup([assist, ordinary, { ...ordinary, thread_id: "saved" }]);
    panel._selectedThreadId = "saved";
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("saved");
  });

  it("does not let an older inventory eject a deliberate choice made during refresh", async () => {
    const panel = setup([ordinary, assist]);
    let finishList;
    panel._callWS.mockImplementationOnce(() => new Promise((resolve) => { finishList = resolve; }));
    const loading = panel._loadThreads();
    await panel._selectThread("assist");
    finishList([ordinary]);
    await loading;
    expect(panel._selectedThreadId).toBe("assist");
    expect(panel._threads).toContainEqual(assist);
    expect(panel._threadSelectionDeliberate).toBe(true);
    panel._callWS.mockResolvedValueOnce([ordinary]);
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
    expect(panel._threadSelectionDeliberate).toBe(false);
  });

  it("falls back after a deliberately selected chat is absent from a subsequent inventory", async () => {
    const panel = setup([assist, ordinary]);
    await panel._selectThread("assist");
    panel._callWS.mockResolvedValueOnce([ordinary]);
    await panel._loadThreads();
    expect(panel._selectedThreadId).toBe("ordinary");
    expect(panel._threadSelectionDeliberate).toBe(false);
  });

  it.each(["_deleteThread", "_archiveThread"])("clears a deliberate Assist choice with %s and no ordinary replacement", async (operation) => {
    const panel = setup([assist, { ...assist, thread_id: "other-assist" }]);
    await panel._selectThread("assist");
    await panel[operation]("assist", null, true);
    expect(panel._selectedThreadId).toBeNull();
    expect(panel._threadSelectionDeliberate).toBe(false);
  });

  it("uses an ordinary preferred-project replacement when a selected project is removed", async () => {
    const panel = setup([assist, { ...ordinary, project_id: "preferred" }, { ...ordinary, thread_id: "removed", project_id: "removed" }]);
    panel._projects.push({ project_id: "preferred" }, { project_id: "removed", archived_at: "2026-09-27T08:00:00Z" });
    await panel._selectThread("removed");
    panel._clearSelectionForProject("removed", { preferProjectId: "preferred" });
    expect(panel._selectedThreadId).toBe("ordinary");
    expect(panel._selectedProjectId).toBe("preferred");
    expect(panel._threadSelectionDeliberate).toBe(false);
  });

  it.each(["_archiveProject", "_deleteProject"])("offers an empty selection after %s with only Assist remaining", async (operation) => {
    const panel = setup([assist, { ...ordinary, project_id: "removed" }]);
    panel._projects.push({ project_id: "removed" });
    panel._selectedProjectId = "removed";
    await panel._selectThread("ordinary");
    panel._callWS.mockImplementation(async (action) => action === "archive_project"
      ? { project_id: "removed", archived_at: "2026-09-27T08:00:00Z" } : {});
    await panel[operation]("removed", null, true);
    expect(panel._selectedThreadId).toBeNull();
    expect(panel._threadSelectionDeliberate).toBe(false);
  });

  it.each([{ threads: [assist] }, { threads: [] }])("renders a new-chat action without creating a chat for %j", async ({ threads }) => {
    const panel = setup(threads);
    await panel._loadThreads();
    panel._render();
    const action = panel.shadowRoot.querySelector('#message-list [data-action="new-direct-chat"]');
    expect(action?.textContent).toContain("New chat");
    expect(action?.disabled).toBe(false);
    action.click();
    expect(panel._showThreadForm).toBe(true);
    expect(panel._threadForm.projectId).toBeNull();
    expect(panel._callWS.mock.calls.some(([operation]) => operation === "create_thread")).toBe(false);
  });
});
