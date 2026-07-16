/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

describe("focus mode", () => {
  let fullscreenElement;
  let fullscreenDescriptor;

  beforeEach(() => {
    document.body.replaceChildren();
    fullscreenDescriptor = Object.getOwnPropertyDescriptor(document, "fullscreenElement");
    fullscreenElement = null;
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => fullscreenElement,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (fullscreenDescriptor) {
      Object.defineProperty(document, "fullscreenElement", fullscreenDescriptor);
    }
  });

  it("enters standards fullscreen from the menu and restores focus after native exit", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel.requestFullscreen = vi.fn(async () => {
      fullscreenElement = panel;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    const menuToggle = panel.shadowRoot.getElementById("app-menu-toggle");
    menuToggle.click();
    const focusButton = panel.shadowRoot.getElementById("focus-mode-button");
    await Promise.resolve();
    expect(panel.shadowRoot.activeElement).toBe(focusButton);

    focusButton.click();
    await Promise.resolve();
    expect(panel.requestFullscreen).toHaveBeenCalledOnce();
    expect(panel._focusMode).toBe(true);
    expect(focusButton.getAttribute("aria-pressed")).toBe("true");
    expect(focusButton.textContent).toContain("Exit focus mode");

    fullscreenElement = null;
    document.dispatchEvent(new Event("fullscreenchange"));
    await Promise.resolve();
    expect(panel._focusMode).toBe(false);
    expect(panel.shadowRoot.getElementById("app-menu").hidden).toBe(false);
    expect(panel.shadowRoot.activeElement).toBe(focusButton);
  });

  it("keeps normal layout and reports unavailable focus mode locally", async () => {
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: false });
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    const menuToggle = panel.shadowRoot.getElementById("app-menu-toggle");
    menuToggle.click();
    const focusButton = panel.shadowRoot.getElementById("focus-mode-button");
    focusButton.click();
    await Promise.resolve();

    expect(panel._focusMode).toBe(false);
    expect(panel._error).toBe("");
    expect(panel.shadowRoot.getElementById("focus-mode-feedback").textContent).toContain("unavailable");
    expect(panel.shadowRoot.querySelector(".shell").hasAttribute("inert")).toBe(false);
  });

  it("dismisses the app menu on Escape or click-away", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    const menuToggle = panel.shadowRoot.getElementById("app-menu-toggle");
    menuToggle.click();
    const focusButton = panel.shadowRoot.getElementById("focus-mode-button");
    await Promise.resolve();
    expect(panel.shadowRoot.getElementById("app-menu").hidden).toBe(false);

    focusButton.dispatchEvent(new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      key: "Escape",
    }));
    expect(panel.shadowRoot.getElementById("app-menu").hidden).toBe(true);
    expect(panel.shadowRoot.activeElement).toBe(menuToggle);

    menuToggle.click();
    panel.shadowRoot.getElementById("search-input").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(panel.shadowRoot.getElementById("app-menu").hidden).toBe(true);
  });
});
