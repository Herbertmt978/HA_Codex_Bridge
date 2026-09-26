import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

describe("scheduled run history refresh", () => {
  beforeEach(() => document.body.replaceChildren());

  function setup() {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._activeDestination = "scheduled";
    panel._config = { capabilities: ["automations_v1", "automation_text_edits_v1"] };
    const state = panel._desktopFeatures.scheduled;
    const automation = {
      automation_id: "owned-task", name: "Once-only check", prompt: "Original prompt",
      revision: 1, enabled: true, last_status: "idle",
      schedule: { kind: "once", at: "2026-09-26T08:00:00Z" },
      target: { kind: "standalone", project_id: "owned-project" },
    };
    state.loaded = true;
    state.data.automations = [{ ...automation, title: automation.name, status: "idle" }];
    panel._render(true);
    return { panel, state, automation };
  }

  it("reads fresh definitions with history so a completed once-only task no longer offers Pause", async () => {
    const { panel, state, automation } = setup();
    panel._callWS = vi.fn(async (action) => {
      if (action === "list_automation_runs") return [{ status: "completed", due_at: "2026-09-26T08:00:00Z" }];
      if (action === "list_automations") return [{ ...automation, revision: 2, enabled: false, last_status: "completed" }];
      throw new Error("Unexpected mutation");
    });
    await panel._handleDesktopAction("list-automation-runs", { id: "owned-task" }, null);
    expect(panel._callWS.mock.calls.map(([action]) => action)).toEqual(["list_automation_runs", "list_automations"]);
    expect(state.data.automations[0]).toMatchObject({ revision: 2, enabled: false, status: "completed" });
    expect(panel.shadowRoot.querySelector('[data-desktop-action="pause-automation"]')).toBeNull();
    expect(panel.shadowRoot.textContent).toContain("Completed");
  });

  it("preserves a newer open edit draft while the history request completes", async () => {
    const { panel, state, automation } = setup();
    let finishHistory;
    panel._callWS = vi.fn((action) => {
      if (action === "list_automation_runs") return new Promise((resolve) => { finishHistory = resolve; });
      if (action === "list_automations") return Promise.resolve([{ ...automation, revision: 2, last_status: "completed" }]);
      return Promise.reject(new Error("Unexpected mutation"));
    });
    const request = panel._handleDesktopAction("list-automation-runs", { id: "owned-task" }, null);
    state.form = "automation-edit-description";
    state.editingAutomation = automation;
    state.formDraft = { edit_description: "Rename to My newer draft" };
    finishHistory([{ status: "completed" }]);
    await request;
    expect(state.formDraft.edit_description).toBe("Rename to My newer draft");
    expect(state.editingAutomation).toBe(automation);
    expect(state.form).toBe("automation-edit-description");
    expect(panel.shadowRoot.querySelector('[name="edit_description"]').value).toBe("Rename to My newer draft");
    expect(state.data.automations[0].revision).toBe(2);
  });

  it("retains readable history and reports a failed definition refresh without inventing status", async () => {
    const { panel, state } = setup();
    panel._callWS = vi.fn(async (action) => {
      if (action === "list_automation_runs") return [{ status: "completed" }];
      throw new Error("Refresh unavailable");
    });
    await panel._handleDesktopAction("list-automation-runs", { id: "owned-task" }, null);
    expect(state.data.runs).toEqual([{ status: "completed" }]);
    expect(state.data.automations[0].status).toBe("idle");
    expect(state.error).not.toBe("");
    expect(state.loading).toBe(false);
  });
});
