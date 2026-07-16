/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { api_version: 1, connection_type: "supervisor", panel_title: "Codex Bridge" };
  panel._status = {
    auth: { state: "ok", auth_required: false },
    account: { available: true, auth_mode: "chatgpt", plan_type: "plus" },
    diagnostics: { app_version: "0.6.0", bridge_version: "0.6.0", active_codex_version: "1.2.3" },
    model_catalog: { models: [], default_model: "gpt-5.6", default_thinking_level: "medium" },
    limits: { available: true },
  };
  panel._render(true);
  return panel;
}

describe("panel navigation actions", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("requires an accessible in-panel confirmation before deleting a chat, traps focus, and restores it on Escape", async () => {
    const panel = createPanel();
    const trigger = panel.shadowRoot.getElementById("new-direct-chat-button");
    panel._threads = [{ thread_id: "thread-delete", project_id: null, title: "Delete me", archived_at: null }];
    panel._selectedThreadId = "other-thread";
    panel._callWS = vi.fn();

    await panel._deleteThread("thread-delete", trigger);

    const layer = panel.shadowRoot.getElementById("confirmation-layer");
    const dialog = panel.shadowRoot.getElementById("confirmation-dialog");
    const cancel = panel.shadowRoot.getElementById("cancel-delete-button");
    const confirm = panel.shadowRoot.getElementById("confirm-delete-button");
    expect(layer.hidden).toBe(false);
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.textContent).toMatch(/chat record/i);
    expect(dialog.textContent).toMatch(/workspace files remain/i);
    expect(panel.shadowRoot.querySelector(".shell").getAttribute("aria-hidden")).toBe("true");
    expect(panel._callWS).not.toHaveBeenCalled();

    await Promise.resolve();
    expect(panel.shadowRoot.activeElement).toBe(cancel);
    confirm.focus();
    confirm.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    expect(panel.shadowRoot.activeElement).toBe(cancel);

    cancel.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await Promise.resolve();
    expect(layer.hidden).toBe(true);
    expect(panel.shadowRoot.querySelector(".shell").hasAttribute("aria-hidden")).toBe(false);
    expect(panel.shadowRoot.activeElement).toBe(trigger);
    expect(panel._callWS).not.toHaveBeenCalled();
  });

  it("keeps the hidden confirmation layer out of the hit-testing and accessibility tree", () => {
    const panel = createPanel();
    const layer = panel.shadowRoot.getElementById("confirmation-layer");
    expect(layer.hidden).toBe(true);
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(stylesheet).toMatch(/\.confirmation-layer\[hidden\][\s\S]*display:\s*none\s*!important/);
    expect(stylesheet).toMatch(/\.confirmation-layer\[hidden\][\s\S]*pointer-events:\s*none/);
    expect(stylesheet).toMatch(/\.confirmation-delete[\s\S]*background:\s*#111827[\s\S]*color:\s*#ffffff/);
  });

  it("deletes only after confirming a project and communicates that workspace files remain", async () => {
    const panel = createPanel();
    panel._projects = [{ project_id: "project-delete", name: "Workspace", kind: "project", archived_at: null }];
    panel._threads = [];
    panel._selectedProjectId = "project-delete";
    panel._callWS = vi.fn().mockResolvedValue(undefined);

    await panel._deleteProject("project-delete");
    expect(panel.shadowRoot.getElementById("confirmation-description").textContent).toMatch(
      /project and its chat records.*workspace files remain/i
    );
    panel.shadowRoot.getElementById("confirm-delete-button").click();
    await Promise.resolve();

    expect(panel._callWS).toHaveBeenCalledWith("delete_project", { project_id: "project-delete" });
  });

  it("shows a shared bounded tooltip on hover and keyboard focus while retaining label and title fallbacks", () => {
    const panel = createPanel();
    const newChat = panel.shadowRoot.getElementById("new-direct-chat-button");
    const tooltip = panel.shadowRoot.getElementById("tooltip-layer");

    expect(newChat.dataset.tooltip).toBe("New chat");
    expect(newChat.getAttribute("aria-label")).toBe("New chat");
    expect(newChat.getAttribute("title")).toBe("New chat");
    newChat.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    expect(tooltip.hidden).toBe(false);
    expect(tooltip.textContent).toBe("New chat");
    expect(newChat.getAttribute("aria-describedby")).toContain("tooltip-layer");

    newChat.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    expect(tooltip.hidden).toBe(true);
    expect(newChat.hasAttribute("aria-describedby")).toBe(false);

    newChat.focus();
    expect(tooltip.hidden).toBe(false);
    expect(tooltip.textContent).toBe("New chat");

    const longLabel = "x".repeat(180);
    const bounded = panel._actionButton("icon-button", "test-tooltip", longLabel);
    expect(bounded.dataset.tooltip).toHaveLength(120);
    expect(bounded.getAttribute("title")).toBe(longLabel);
  });
});
