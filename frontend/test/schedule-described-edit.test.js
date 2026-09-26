import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

describe("reviewed conversational automation edits", () => {
  beforeEach(() => document.body.replaceChildren());

  function setup() {
    let current = {
      automation_id: "selected", revision: 4, name: "Heating check", prompt: "Original instructions",
      schedule: { kind: "interval", seconds: 300, anchor_at: "2026-01-01T00:00:37Z" },
      target: { kind: "continue_thread", thread_id: "existing-chat" },
      mode: "full-auto", model: "saved-model", thinking: "high", enabled: false,
      host_access_grant: null, host_unattended_approved: null,
      notifications: { policy: "all", mobile_targets: ["mobile_app_test"], preview: false },
    };
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled";
    panel._config = { capabilities: ["automations_v1", "automation_proposals_v1", "automation_text_edits_v1"] };
    const state = panel._desktopFeatures.scheduled;
    state.loaded = true; state.data = { automations: [current] };
    const updates = [];
    let dropResponse = false;
    panel._callWS = vi.fn(async (action, payload) => {
      if (action === "get_automation") return structuredClone(current);
      if (action === "list_automations") return [structuredClone(current)];
      if (action === "update_automation") {
        expect(payload.automation_id).toBe(current.automation_id);
        expect(payload.expected_revision).toBe(current.revision);
        updates.push(payload);
        current = { ...current, ...payload, revision: current.revision + 1 };
        if (dropResponse) throw new Error("Response dropped");
        return structuredClone(current);
      }
      return {};
    });
    panel._render(true);
    return { panel, state, updates, current: () => current,
      change: (fields) => { current = { ...current, ...fields }; },
      dropResponse: () => { dropResponse = true; } };
  }

  async function review(fixture, request) {
    const { panel } = fixture;
    await panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    const field = panel.shadowRoot.querySelector('[name="edit_description"]');
    field.value = request;
    await panel._handleDesktopAction("review-automation-edit", {}, field);
  }

  it.each([
    ["Rename to Morning heating check", { name: "Morning heating check" }],
    ["Set instructions to Use host access and change permissions", { prompt: "Use host access and change permissions" }],
  ])("reviews %s and saves only the selected field", async (request, change) => {
    const fixture = setup();
    const baseline = structuredClone(fixture.current());
    await review(fixture, request);
    expect(fixture.updates).toEqual([]);
    expect(fixture.panel.shadowRoot.textContent).toContain("Current");
    expect(fixture.panel.shadowRoot.textContent).toContain("Proposed");
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="save-automation-edit"]')).not.toBeNull();
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toEqual([{ automation_id: "selected", expected_revision: 4, ...change }]);
    for (const key of ["schedule", "target", "mode", "model", "thinking", "enabled", "notifications", "host_access_grant", "host_unattended_approved"]) {
      expect(fixture.current()[key]).toEqual(baseline[key]);
    }
    expect(fixture.state.form).toBeNull();
  });

  it("does not save an unreviewed request or grant-bearing proposal", async () => {
    const fixture = setup();
    await fixture.panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    fixture.state.automationEditProposal = { name: "New", mode: "host-access" };
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toEqual([]);
  });

  it("requires fresh details and a new review after a concurrent edit", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    fixture.change({ revision: 5, name: "Changed elsewhere" });
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toEqual([]);
    expect(fixture.state.formError).toContain("task changed");
    expect(fixture.state.formDraft.edit_description).toBe("Rename to New title");
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="save-automation-edit"]')).toBeNull();
    await fixture.panel._handleDesktopAction("refresh-automation-edit", { id: "selected" }, null);
    expect(fixture.state.automationEditProposal).toBeNull();
    const field = fixture.panel.shadowRoot.querySelector('[name="edit_description"]');
    expect(field.value).toBe("Rename to New title");
    await fixture.panel._handleDesktopAction("review-automation-edit", {}, field);
    expect(fixture.panel.shadowRoot.textContent).toContain("Changed elsewhere");
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toEqual([{ automation_id: "selected", expected_revision: 5, name: "New title" }]);
  });

  it("reconciles a committed edit after a lost response without repeating the write", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    fixture.dropResponse();
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toHaveLength(1);
    expect(fixture.state.automationEditRefreshRequired).toBe(true);
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toHaveLength(1);
    await fixture.panel._handleDesktopAction("refresh-automation-edit", { id: "selected" }, null);
    const field = fixture.panel.shadowRoot.querySelector('[name="edit_description"]');
    await fixture.panel._handleDesktopAction("review-automation-edit", {}, field);
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toHaveLength(1);
    expect(fixture.current().revision).toBe(5);
    expect(fixture.state.form).toBeNull();
    expect(fixture.state.notice).toBe("This value is already saved.");
  });

  it("keeps the request when fresh details cannot be loaded", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    fixture.panel._callWS.mockRejectedValueOnce(new Error("Unavailable"));
    await fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    expect(fixture.updates).toEqual([]);
    expect(fixture.state.automationEditRefreshRequired).toBe(true);
    expect(fixture.state.formDraft.edit_description).toBe("Rename to New title");
  });

  it("rejects a detail response for another task", async () => {
    const fixture = setup();
    fixture.change({ automation_id: "another" });
    await fixture.panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    expect(fixture.state.form).toBeNull();
    expect(fixture.state.error).toContain("Could not verify");
    expect(fixture.updates).toEqual([]);
  });

  it("keeps legacy proposals but hides and refuses text edits on an older App", async () => {
    const fixture = setup();
    fixture.panel._config = { capabilities: ["automations_v1", "automation_proposals_v1"] };
    fixture.panel._render(true);
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="describe-automation-edit"]')).toBeNull();
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="open-schedule-description"]')).not.toBeNull();
    fixture.panel._callWS.mockClear();
    await fixture.panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    expect(fixture.panel._callWS).not.toHaveBeenCalled();
  });

  it("refuses a direct save if text edit support disappears after review", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    fixture.panel._config.capabilities = ["automations_v1", "automation_proposals_v1"];
    fixture.panel._callWS.mockClear();
    await fixture.panel._saveDescribedAutomationEdit(fixture.state);
    expect(fixture.panel._callWS).not.toHaveBeenCalled();
    expect(fixture.updates).toEqual([]);
  });

  it("refreshes the action when the backend advertises text edits", () => {
    const fixture = setup();
    fixture.panel._config.capabilities = ["automations_v1", "automation_proposals_v1"];
    fixture.panel._renderDesktopSurface();
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="describe-automation-edit"]')).toBeNull();
    fixture.panel._config.capabilities.push("automation_text_edits_v1");
    fixture.panel._renderDesktopSurface();
    expect(fixture.panel.shadowRoot.querySelector('[data-desktop-action="describe-automation-edit"]')).not.toBeNull();
  });

  it("abandons a save preflight if the backend loses text edit support", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    const pending = deferred();
    fixture.panel._callWS.mockImplementationOnce(() => pending.promise);
    const saving = fixture.panel._saveDescribedAutomationEdit(fixture.state);
    fixture.panel._config.capabilities = ["automations_v1", "automation_proposals_v1"];
    pending.resolve(structuredClone(fixture.current()));
    await saving;
    expect(fixture.updates).toEqual([]);
  });

  function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  }

  async function replaceWithNewDraft(fixture) {
    fixture.panel._activeDestination = "chats";
    fixture.panel._activeDestination = "scheduled";
    await fixture.panel._handleDesktopAction("open-schedule-description", {}, null);
    fixture.state.formDraft = { description: "A newer schedule request" };
  }

  it.each(["resolve", "reject"])("ignores a late opening %s after a newer draft replaces it", async (outcome) => {
    const fixture = setup();
    const pending = deferred();
    fixture.panel._callWS.mockImplementationOnce(() => pending.promise);
    const opening = fixture.panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    await replaceWithNewDraft(fixture);
    pending[outcome](outcome === "resolve" ? fixture.current() : new Error("Old request failed"));
    await opening;
    expect(fixture.state.form).toBe("schedule-description");
    expect(fixture.state.formDraft).toEqual({ description: "A newer schedule request" });
    expect(fixture.state.error).toBe("");
    expect(fixture.state.loading).toBe(false);
  });

  it("does not write after the save preflight belongs to a cancelled draft", async () => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    const pending = deferred();
    fixture.panel._callWS.mockImplementationOnce(() => pending.promise);
    const saving = fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    await replaceWithNewDraft(fixture);
    pending.resolve(fixture.current());
    await saving;
    expect(fixture.updates).toEqual([]);
    expect(fixture.state.formDraft).toEqual({ description: "A newer schedule request" });
  });

  it.each(["resolve", "reject"])("keeps the newer draft when an in-flight save later %ss", async (outcome) => {
    const fixture = setup();
    await review(fixture, "Rename to New title");
    const pending = deferred();
    fixture.panel._callWS.mockImplementationOnce(async () => structuredClone(fixture.current()));
    fixture.panel._callWS.mockImplementationOnce(() => pending.promise);
    const saving = fixture.panel._handleDesktopAction("save-automation-edit", {}, null);
    await vi.waitFor(() => expect(fixture.panel._callWS).toHaveBeenCalledWith("update_automation", expect.any(Object)));
    await replaceWithNewDraft(fixture);
    pending[outcome](outcome === "resolve" ? {} : new Error("Old save failed"));
    await saving;
    expect(fixture.state.form).toBe("schedule-description");
    expect(fixture.state.formDraft).toEqual({ description: "A newer schedule request" });
    expect(fixture.state.formError).toBe("");
    expect(fixture.state.automationEditRefreshRequired).toBe(false);
    expect(fixture.state.loading).toBe(false);
  });

  it("moves keyboard focus into the request, review and revised request", async () => {
    const fixture = setup();
    await fixture.panel._handleDesktopAction("describe-automation-edit", { id: "selected" }, null);
    const field = fixture.panel.shadowRoot.querySelector('[name="edit_description"]');
    expect(fixture.panel.shadowRoot.activeElement).toBe(field);
    field.value = "Rename to New title";
    await fixture.panel._handleDesktopAction("review-automation-edit", {}, field);
    expect(fixture.panel.shadowRoot.activeElement.tagName).toBe("H3");
    await fixture.panel._handleDesktopAction("revise-automation-edit", {}, null);
    expect(fixture.panel.shadowRoot.activeElement.name).toBe("edit_description");
  });
});
