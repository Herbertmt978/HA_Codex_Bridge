import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";
import { normalisePreferences, readPreferences } from "../src/panel-preferences.js";
import { renderDesktopFeatureSurface } from "../src/desktop-features.js";

afterEach(() => { document.body.replaceChildren(); localStorage.clear(); });

describe("panel preferences", () => {
  it("persists appearance per HA user and tolerates broken storage", () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._hass = { user: { id: "one" } }; panel._loadPreferences();
    panel._savePreferences({ theme: "dark", textSize: "large", motion: "reduced", mode: "edit" });
    expect(panel.dataset.panelTheme).toBe("dark");
    panel._preferences = {}; panel._loadPreferences();
    const restored = document.createElement("codex-bridge-panel");
    restored._hass = { user: { id: "one" } }; restored._loadPreferences();
    expect(restored._preferences).toMatchObject({ theme: "dark", textSize: "large", motion: "reduced", mode: "edit" });
    restored._hass = { user: { id: "two" } }; restored._loadPreferences();
    expect(restored._preferences.theme).toBe("ha");
    expect(readPreferences({ getItem() { throw new Error("disabled"); } }, "x").theme).toBe("ha");
    expect(normalisePreferences({ mode: "host-root", theme: "malicious", model: "<script>" })).toMatchObject({ mode: "full-auto", theme: "ha", model: "" });
  });

  it("applies defaults only when creating a new chat", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._savePreferences({ mode: "edit", model: "gpt-6-astra", thinking: "high" });
    panel._openThreadFormForProject("project-one");
    expect(panel._threadForm.mode).toBe("edit");
    panel._threadForm.title = "New work";
    panel._callWS = vi.fn().mockResolvedValue({ thread_id: "new", project_id: "project-one" });
    panel._adoptCreatedThread = vi.fn(); panel._loadThreads = vi.fn();
    await panel._createThread();
    expect(panel._callWS).toHaveBeenCalledWith("create_thread", { title: "New work", project_id: "project-one", mode: "edit", model_override: "gpt-6-astra", thinking_override: "high" });
    expect(panel._callWS).not.toHaveBeenCalledWith("update_thread", expect.anything());
  });

  it("groups skills on ES2022 clients without losing names or action identifiers", () => {
    const container = document.createElement("div");
    const sort = Object.getOwnPropertyDescriptor(Array.prototype, "toSorted");
    Object.defineProperty(Array.prototype, "toSorted", { value: undefined, configurable: true });
    try {
      renderDesktopFeatureSurface(container, { destination: "skills", state: { data: { skills: [
        { id: "a", name: "data-analytics:build-report", enabled: true },
        { id: "b", name: "data-analytics:design-kpis", enabled: false },
        { id: "c", name: "aegis:debug", enabled: true },
        { id: "d", name: "standalone", enabled: true },
      ] } } });
    } finally {
      if (sort) Object.defineProperty(Array.prototype, "toSorted", sort);
      else delete Array.prototype.toSorted;
    }
    expect(container.querySelectorAll(".skill-group")).toHaveLength(3);
    const analytics = [...container.querySelectorAll(".skill-group")].find((group) => group.textContent.includes("Data analytics"));
    expect(analytics.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(analytics.querySelector('[data-id="b"][data-desktop-action="toggle-skill"]').textContent).toBe("Enable");
  });
});
