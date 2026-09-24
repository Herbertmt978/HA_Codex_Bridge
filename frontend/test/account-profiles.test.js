/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const CURRENT = { id: "a".repeat(32), label: "Personal", plan: "pro", active: true };
const OTHER = { id: "b".repeat(32), label: "Workspace", plan: "team", active: false };

function panelWithAccounts() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._config = { connection_type: "supervisor", api_version: 1, capabilities: ["account_profiles_v1"] };
  panel._status = {
    auth: { state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" },
    account: { available: true, auth_mode: "chatgpt", plan_type: "pro" },
  };
  return panel;
}

describe("saved Home Assistant accounts", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("shows only safe labels and requires saving the current account before adding another", async () => {
    const panel = panelWithAccounts();
    panel._callWS = vi.fn(async () => []);
    panel.shadowRoot.getElementById("app-menu-toggle").click();
    await vi.waitFor(() => expect(panel._accountProfilesLoaded).toBe(true));
    expect(panel.shadowRoot.getElementById("add-account-profile").disabled).toBe(true);
    expect(panel.shadowRoot.getElementById("account-menu-list").textContent).toContain("No saved accounts");

    panel._accountProfiles = [CURRENT, { ...OTHER, label: "<img src=x onerror=alert(1)>" }];
    panel._renderAppMenu();
    expect(panel.shadowRoot.getElementById("account-menu-list").textContent).toContain("<img src=x");
    expect(panel.shadowRoot.getElementById("account-menu-list").querySelector("img")).toBeNull();
    expect(panel.shadowRoot.innerHTML).not.toContain("account_personal");
  });

  it("switches a saved account and reports the verified state", async () => {
    const panel = panelWithAccounts();
    panel._accountProfiles = [CURRENT, OTHER];
    panel._accountProfilesLoaded = true;
    panel._appMenuOpen = true;
    panel._renderAppMenu();
    panel._callWS = vi.fn(async (action) => {
      if (action === "switch_account_profile") {
        return { state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "team" };
      }
      if (action === "list_account_profiles") return [{ ...CURRENT, active: false }, { ...OTHER, active: true }];
      throw new Error("unexpected action");
    });
    panel._loadStatus = vi.fn(async () => {});
    panel._render = vi.fn();
    panel._applyAuthStatus = vi.fn();

    panel.shadowRoot.querySelector(`[data-action="switch-account-profile"][data-profile-id="${OTHER.id}"]`).click();
    await vi.waitFor(() => expect(panel._accountProfilePending).toBe(false));
    expect(panel._callWS).toHaveBeenCalledWith("switch_account_profile", { profile_id: OTHER.id });
    expect(panel._accountProfiles.find((item) => item.id === OTHER.id).active).toBe(true);
    expect(panel.shadowRoot.getElementById("app-menu").hidden).toBe(true);
  });
});
