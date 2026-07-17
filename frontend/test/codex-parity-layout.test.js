/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it } from "vitest";

import "../src/codex-bridge-panel.js";

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { panel_title: "Codex Bridge", web_search_mode: "live" };
  panel._status = {
    auth: { state: "ok", auth_required: false },
    account: { auth_mode: "chatgpt", plan_type: "pro" },
    provider_capabilities: { web_search: true },
    limits: { available: true },
  };
  panel._selectedThreadId = "thread-parity";
  panel._activeThread = {
    thread_id: "thread-parity",
    title: "Parity",
    status: "idle",
    mode: "edit",
    attachments: [],
  };
  panel._render(true);
  return panel;
}

describe("Codex desktop parity layout", () => {
  beforeEach(() => document.body.replaceChildren());

  it("uses the Codex desktop proportions, floating context card, and one transcript scroller", () => {
    const panel = createPanel();
    const root = panel.shadowRoot;
    const stylesheet = [...root.querySelectorAll("style")].map((style) => style.textContent).join("\n");

    expect(stylesheet).toMatch(/--conversation-width:\s*840px/);
    expect(stylesheet).toMatch(/grid-template-columns:\s*clamp\(300px,\s*20vw,\s*330px\)\s+minmax\(0,\s*1fr\)\s+clamp\(342px,\s*calc\(22vw \+ 12px\),\s*372px\)/);
    expect(stylesheet).toMatch(/\.main-header\s*\{[^}]*calc\(\(100% - var\(--conversation-width\)\) \/ 2\)/s);
    expect(stylesheet).toMatch(/\.side-pane\s*\{[^}]*margin:\s*64px 12px 12px 0;[^}]*border-radius:\s*18px;/s);
    expect(stylesheet).toMatch(/\.shell\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\);[^}]*overflow:\s*hidden;/s);
    expect(stylesheet).toMatch(/\.pane\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s);
    expect(stylesheet).toMatch(/\.conversation-scroll\s*\{[^}]*overflow:\s*auto;/s);
    expect(stylesheet).toMatch(/\.interaction-region\s*\{[^}]*max-height:\s*none;[^}]*overflow:\s*visible;/s);

    const scroller = root.getElementById("conversation-scroll");
    expect([...scroller.children]).toEqual(expect.arrayContaining([
      root.getElementById("message-list"),
      root.getElementById("run-activity"),
      root.getElementById("interaction-region"),
    ]));
  });

  it("keeps the transcript as the only narrow-screen scrollport", () => {
    const panel = createPanel();
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");

    expect(stylesheet).toMatch(/@media\s*\(max-width:\s*880px\)\s*\{[\s\S]*?\n\s+\.main-pane\s*\{[^}]*overflow-y:\s*hidden;/);
    expect(stylesheet).not.toMatch(/@media\s*\(max-width:\s*880px\)\s*\{[\s\S]*\n\s+\.main-pane\s*\{[^}]*overflow-y:\s*auto;/);
  });

  it("keeps create-chat actions intact and uses quiet native-feeling rail controls", () => {
    const panel = createPanel();
    panel._showThreadForm = true;
    panel._threadForm = { title: "", mode: "full-auto", projectId: null };
    panel._renderThreadForm();
    const root = panel.shadowRoot;
    const stylesheet = [...root.querySelectorAll("style")].map((style) => style.textContent).join("\n");

    expect(root.querySelector('[data-action="save-thread"]')?.textContent).toContain("Create chat");
    expect(stylesheet).toMatch(/\.panel-form\s+\.form-actions\s*>\s*button\s*\{[^}]*flex:\s*0\s+0\s+auto;[^}]*white-space:\s*nowrap;/s);
    expect(stylesheet).toMatch(/\.panel-form\s+\.send-button\s*\{[^}]*width:\s*auto;/s);
    expect(stylesheet).toMatch(/#refresh-thread-button\s*\{[^}]*background:\s*transparent;[^}]*border-color:\s*transparent;/s);
    expect(stylesheet).toMatch(/\.section-scroll\s*\{[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s);
    expect(stylesheet).toMatch(/\.section-scroll::-webkit-scrollbar-button\s*\{[^}]*display:\s*none;/s);
    expect(stylesheet).toMatch(/\.run-step-failure\s*\{[^}]*display:\s*block;[^}]*color:\s*var\(--error-color\);/s);
  });

  it("renders a compact footer composer and Codex-style activity sections", () => {
    const panel = createPanel();
    const root = panel.shadowRoot;
    const stylesheet = [...root.querySelectorAll("style")].map((style) => style.textContent).join("\n");

    expect(stylesheet).toMatch(/\.composer-shell\s*\{[^}]*width:\s*min\(calc\(100% - 32px\),\s*var\(--conversation-width\)\);/s);
    expect(stylesheet).toMatch(/\.composer-shell \.composer\s*\{\s*display:\s*contents;/);
    expect(root.getElementById("attachment-meta").hidden).toBe(true);
    expect([...root.querySelectorAll("#activity-center [data-section]")].map((item) => item.dataset.section)).toEqual([
      "outputs", "subagents",
    ]);
    expect(root.querySelector('[data-section="outputs"] [data-action="select-side-tab"]')).not.toBeNull();
    expect(root.querySelector('[data-section="outputs"] .activity-center-summary')?.textContent).toBe("Create a file or site");
    expect(root.querySelector('[data-section="outputs"] .activity-center-rows')).toBeNull();
  });

  it("adds only the activity sections that have current run details", () => {
    const panel = createPanel();
    panel._runActivityForThread = () => ({
      runId: "run-parity",
      state: "running",
      currentActivity: "Searching the web",
      webSearchActive: true,
    });
    panel._renderActivityCenter();

    expect([...panel.shadowRoot.querySelectorAll("#activity-center [data-section]")].map((item) => item.dataset.section)).toEqual([
      "outputs", "subagents", "background", "browser",
    ]);
  });

  it("shows compact Codex-style subagent status only when agents are reported", () => {
    const panel = createPanel();
    panel._runActivityForThread = () => ({
      state: "running",
      subagents: { total: 5, active: 2, completed: 3, attention: 0 },
    });
    panel._renderActivityCenter();

    const strip = panel.shadowRoot.querySelector('[data-section="subagents"] .activity-agent-strip');
    expect(strip?.getAttribute("aria-label")).toBe("2 subagents working, 3 done");
    expect(strip?.textContent).toMatch(/2 working.*3 done/);
    expect(strip?.querySelectorAll(".activity-agent-marker")).toHaveLength(4);
  });

  it("keeps subagent failures visible in the compact status strip", () => {
    const panel = createPanel();
    panel._runActivityForThread = () => ({
      state: "failed",
      subagents: { total: 1, active: 0, completed: 0, attention: 1 },
    });
    panel._renderActivityCenter();

    const strip = panel.shadowRoot.querySelector('[data-section="subagents"] .activity-agent-strip');
    expect(strip?.getAttribute("aria-label")).toBe("0 subagents working, 0 done, 1 needs attention");
    expect(strip?.querySelector(".activity-agent-attention")?.textContent).toBe("1 needs attention");
  });

  it("maps every information tab to exactly one labelled panel", () => {
    const panel = createPanel();
    const root = panel.shadowRoot;
    const tabs = [...root.querySelectorAll('[role="tab"][data-side-tab]')];
    const panels = [...root.querySelectorAll('[role="tabpanel"][data-side-tab-panel]')];

    expect(panels).toHaveLength(tabs.length);
    for (const tab of tabs) {
      const panelElement = root.getElementById(tab.getAttribute("aria-controls"));
      expect(panelElement).not.toBeNull();
      expect(panelElement.getAttribute("aria-labelledby")).toBe(tab.id);
      expect(panelElement.dataset.sideTabPanel).toBe(tab.dataset.sideTab);
    }
  });
});
