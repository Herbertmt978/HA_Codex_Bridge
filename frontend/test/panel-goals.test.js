import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

afterEach(() => { document.body.replaceChildren(); localStorage.clear(); });

describe("panel goal owner boundaries", () => {
  it("inherits goal ineligibility from the active chat's archived project", () => {
    const panel = document.createElement("codex-bridge-panel");
    panel._selectedThreadId = "chat-one";
    panel._selectedProjectId = "other-project";
    panel._activeThread = { thread_id: "chat-one", project_id: "archived-project", archived_at: null };
    panel._projects = [
      { project_id: "archived-project", archived_at: "2026-09-27T09:00:00Z" },
      { project_id: "other-project", archived_at: null },
    ];
    panel._config = { capabilities: ["durable_goals_v1"] };
    panel._callWS = vi.fn().mockResolvedValue({ revision: 0, goal: null, continuation: "manual_turns_only" });
    panel._renderGoals(panel._activeThread);
    expect(panel._goalControls.eligible).toBe(false);
    expect(panel._callWS).not.toHaveBeenCalled();
    const controls = panel.shadowRoot.getElementById("goal-controls");
    expect(controls.textContent).toContain("ordinary, unarchived chats and projects");
    expect(controls.querySelectorAll("button")).toHaveLength(0);
  });

  it("invalidates on each HA owner transition, including returning to the same owner", () => {
    const panel = document.createElement("codex-bridge-panel");
    const invalidate = vi.fn();
    panel._goalControls = { invalidate };
    panel._configureDraftRecovery = vi.fn();
    for (const owner of ["one", "one", "two", "one"]) {
      panel._hass = { user: { id: owner } };
      panel._loadPreferences();
    }
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("passes the HA owner for the same selected chat and invalidates on disconnect", () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "chat-one";
    panel._config = { capabilities: ["durable_goals_v1"] };
    const controls = { setContext: vi.fn(), invalidate: vi.fn() };
    panel._goalControls = controls;
    for (const owner of ["one", "two", "one"]) {
      panel._hass = { user: { id: owner } };
      panel._renderGoals({});
    }
    expect(controls.setContext.mock.calls.map(([, context]) => context.ownerId)).toEqual(["one", "two", "one"]);
    expect(controls.setContext.mock.calls.every(([, context]) => context.threadId === "chat-one")).toBe(true);
    panel.remove();
    expect(controls.invalidate).toHaveBeenCalledOnce();
  });
});
