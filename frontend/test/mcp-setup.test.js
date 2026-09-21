/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

function setup(capabilities = []) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._activeDestination = "settings";
  panel._config = { capabilities };
  Object.assign(panel._desktopFeatures.settings, { loaded: true, settingsTab: "mcp" });
  panel._callWS = vi.fn().mockResolvedValue({});
  panel._render(true);
  return panel;
}
const field = (panel, name) => panel.shadowRoot.querySelector(`[data-desktop-field="${name}"]`);
const action = (panel, name) => panel.shadowRoot.querySelector(`[data-desktop-action="${name}"]`);

describe("MCP setup and access settings", () => {
  beforeEach(() => document.body.replaceChildren());

  it("requires fresh endpoint consent for local MCP and keeps public payloads compatible", async () => {
    const panel = setup(["mcp_admin_v1", "mcp_local_v1"]);
    await panel._handleDesktopAction("choose-ha-mcp");
    field(panel, "url").value = "http://homeassistant.local:8123/private-test";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    field(panel, "local").click();
    expect(field(panel, "oauth_client_id")).toBeNull();
    expect(panel.shadowRoot.querySelector(".mcp-local-warning").textContent).toContain("without encryption");
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel._callWS).not.toHaveBeenCalled();
    field(panel, "local_acknowledged").click();
    field(panel, "url").value = "http://homeassistant.local:8123/new-path";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    expect(field(panel, "local_acknowledged").checked).toBe(false);
    field(panel, "local_acknowledged").click();
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel._callWS).toHaveBeenCalledWith("add_mcp", {
      name: "home-assistant", url: "http://homeassistant.local:8123/new-path", local: true, local_acknowledged: true,
    });
    await panel._handleDesktopAction("choose-ha-mcp");
    field(panel, "url").value = "https://ha.example.org/mcp";
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel._callWS).toHaveBeenCalledWith("add_mcp", { name: "home-assistant", url: "https://ha.example.org/mcp" });
  });

  it("offers guidance rather than local controls when the App lacks local capability", async () => {
    const panel = setup(["mcp_admin_v1"]);
    await panel._handleDesktopAction("choose-custom-mcp");
    expect(field(panel, "local")).toBeNull();
    expect(field(panel, "oauth_client_id")).toBeTruthy();
    expect(panel.shadowRoot.querySelector(".mcp-setup").textContent).toContain("Enable local MCP connections");
  });

  it("offers HA guidance alongside the custom-server form without granting access", async () => {
    const panel = setup(["mcp_admin_v1"]);
    await panel._handleDesktopAction("open-mcp-form");
    expect(action(panel, "choose-ha-mcp")).toBe(panel.shadowRoot.activeElement);
    expect(action(panel, "choose-custom-mcp").textContent).toContain("Other MCP server");
    await panel._handleDesktopAction("choose-ha-mcp");
    expect(field(panel, "name").value).toBe("home-assistant");
    expect(field(panel, "url").type).toBe("password");
    expect(panel.shadowRoot.querySelector(".mcp-steps").textContent).toContain("Enable MCP");
    expect(panel._callWS).not.toHaveBeenCalled();
    await panel._handleDesktopAction("close-form");
    await panel._handleDesktopAction("open-mcp-form");
    await panel._handleDesktopAction("choose-custom-mcp");
    expect(field(panel, "name").value).toBe("");
    expect(field(panel, "oauth_client_id")).toBeTruthy();
    expect(field(panel, "oauth_resource")).toBeTruthy();
    expect(panel.shadowRoot.querySelector(".mcp-steps")).toBeNull();
  });

  it("blocks disabled MCP and refreshes capabilities without losing the draft", async () => {
    const panel = setup();
    await panel._handleDesktopAction("choose-ha-mcp");
    const url = field(panel, "url");
    url.value = "https://ha.example.org/api/webhook/private-test";
    url.dispatchEvent(new Event("input", { bubbles: true }));
    expect(action(panel, "submit-mcp").disabled).toBe(true);
    await panel._handleDesktopAction("submit-mcp", {}, url);
    expect(panel._callWS).not.toHaveBeenCalled();
    let advertisedCapabilities = [];
    panel._callWS = vi.fn(async (method) => {
      if (method === "get_status") advertisedCapabilities = ["mcp_admin_v1"];
      return method === "get_config" ? { capabilities: advertisedCapabilities } : {};
    });
    await panel._handleDesktopAction("refresh-settings-capabilities");
    expect(panel._callWS.mock.calls.slice(0, 2).map(([method]) => method)).toEqual(["get_status", "get_config"]);
    expect(panel._callWS).toHaveBeenCalledWith("get_config");
    expect(panel._callWS).toHaveBeenCalledWith("list_mcp");
    expect(panel._callWS).not.toHaveBeenCalledWith("host_access");
    expect(action(panel, "submit-mcp").disabled).toBe(false);
    expect(field(panel, "url").value).toBe(url.value);
    expect(field(panel, "url").type).toBe("password");
  });

  it("retains a failed connection draft and retries the same supported API", async () => {
    const panel = setup(["mcp_admin_v1"]);
    await panel._handleDesktopAction("choose-ha-mcp");
    field(panel, "url").value = "https://ha.example.org/api/webhook/private-test";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    panel._callWS.mockRejectedValueOnce(new Error("Cannot reach https://ha.example.org/api/webhook/private-test"));
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel.shadowRoot.querySelector('[role="alert"]').textContent).not.toContain("private-test");
    expect(field(panel, "url").value).toContain("private-test");
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel._callWS).toHaveBeenCalledWith("add_mcp", { name: "home-assistant", url: "https://ha.example.org/api/webhook/private-test" });
    expect(panel._desktopFeatures.settings.formDraft).toEqual({});
    expect(panel._desktopFeatures.settings.notice).toContain("start a new chat");
  });

  it("keeps refresh failures retryable with the private draft intact", async () => {
    const panel = setup();
    await panel._handleDesktopAction("choose-ha-mcp");
    field(panel, "url").value = "https://ha.example.org/mcp";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    panel._callWS.mockRejectedValueOnce(new Error("App restarting"));
    await panel._handleDesktopAction("refresh-settings-capabilities");
    expect(action(panel, "retry-desktop")).toBeTruthy();
    panel._callWS.mockImplementation(async (method) => method === "get_config" ? { capabilities: ["mcp_admin_v1"] } : {});
    await panel._handleDesktopAction("retry-desktop");
    expect(field(panel, "url").value).toBe("https://ha.example.org/mcp");
    expect(action(panel, "submit-mcp").disabled).toBe(false);
  });

  it("shows access update guidance for older Apps and responds to capability changes", async () => {
    const panel = setup();
    await panel._handleDesktopAction("select-settings-tab", { tab: "access" });
    const surface = panel.shadowRoot.getElementById("desktop-feature-surface");
    expect(surface.textContent).toContain("Full access · Home Assistant OS");
    expect(surface.textContent).toContain("Update the Codex Bridge App and HACS Integration");
    expect(action(panel, "review-host-access")).toBeNull();
    expect(panel._callWS).not.toHaveBeenCalled();
    panel._config.capabilities = ["host_access_v1"];
    panel._renderDesktopSurface();
    expect(action(panel, "review-host-access")).toBeTruthy();
    panel._openHostAccess = vi.fn();
    await panel._handleDesktopAction("review-host-access", {}, action(panel, "review-host-access"));
    expect(panel._openHostAccess).toHaveBeenCalledWith("settings", expect.any(HTMLElement));
    expect(panel._callWS).not.toHaveBeenCalled();
    await panel._handleDesktopAction("select-settings-tab", { tab: "general" });
    expect([...panel.shadowRoot.querySelector('[data-preference="mode"] select').options].some((option) => option.value === "haos-full-access")).toBe(false);
  });
});
