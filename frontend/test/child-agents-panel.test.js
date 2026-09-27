import { describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

describe("child panel integration", () => {
  it("renders real children through the panel area without aggregate identities", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._config = { capabilities: ["subagents_v1"] };
    panel._hass = { user: { id: "owner-one" } };
    panel._selectedThreadId = "parent";
    panel._activeThread = { thread_id: "parent", status: "running", attachments: [] };
    panel._callWS = vi.fn().mockResolvedValue({ children: [{
      child_id: "a".repeat(32), revision: 2, label: "Reviewer", status: "completed",
      stale: true, result: "Tests passed", can_stop: false,
    }] });
    panel._renderActivityCenter();
    await panel._childAgentsView.load();
    const area = panel.shadowRoot.querySelector('[data-section="individual-subagents"]');
    expect(area.textContent).toContain("Reviewer · Completed · Last known status");
    expect(area.textContent).toContain("Tests passed");
    expect(area.querySelectorAll("details")).toHaveLength(1);
    panel._stopEventSubscription();
    expect(panel._childAgentsView.rows[0].can_stop).toBe(false);
    panel.remove();
  });
  it("observes same-chat HA owner changes immediately in the setter", () => {
    const panel = document.createElement("codex-bridge-panel");
    panel._config = { capabilities: ["subagents_v1"] };
    panel._selectedThreadId = "parent";
    panel._loadPreferences = vi.fn();
    panel._render = vi.fn();
    panel.hass = { user: { id: "owner-one" } };
    const originalEpoch = panel._childAgentsView.generation;
    panel._childAgentsView.rows = [{ child_id: "a".repeat(32) }];
    panel.hass = { user: { id: "owner-two" } };
    panel.hass = { user: { id: "owner-one" } };
    expect(panel._childAgentsView.generation).toBe(originalEpoch + 2);
    expect(panel._childAgentsView.rows).toEqual([]);
  });
});
