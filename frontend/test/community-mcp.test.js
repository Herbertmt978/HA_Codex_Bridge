/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";
import { normalizeCommunityMcp } from "../src/community-mcp.js";

const capabilities = ["community_mcp_quick_connect_v1", "mcp_local_v1", "mcp_admin_v1", "mcp_tool_permissions_v1", "mcp_management_v1"];
const status = (extra = {}) => ({ state: "not_connected", server_name: null, reused: false,
  destination: "http://192.168.1.20:9583", version: "8.5.0", consent_revision: "a".repeat(64), ...extra });
const response = (value, ok = true) => ({ ok, json: async () => value });
function setup(value = status(), caps = capabilities) {
  const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
  panel._activeDestination = "settings"; panel._config = { capabilities: caps };
  Object.assign(panel._desktopFeatures.settings, { loaded: true, settingsTab: "mcp", data: { community_mcp: value, mcp_servers: [] } });
  panel._callWS = vi.fn().mockResolvedValue({}); panel._render(true);
  return panel;
}
const control = (panel) => panel.shadowRoot.querySelector('[data-desktop-action="community-mcp-connect"]');
const consent = (panel) => panel.shadowRoot.querySelector("[data-community-mcp-acknowledged]");

describe("community HA-MCP quick connect", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
  it("shows the actual destination, requires explicit consent and sends no URL or credential", async () => {
    const panel = setup(); panel._fetchHaApi = vi.fn().mockResolvedValue(response(status({ state: "configured", server_name: "ha-community-123456789abc" })));
    const reload = vi.spyOn(panel, "_loadDesktopDestination").mockResolvedValue();
    expect(control(panel).disabled).toBe(true);
    expect(panel.shadowRoot.textContent).toContain("http://192.168.1.20:9583");
    await panel._handleDesktopAction("community-mcp-connect"); expect(panel._fetchHaApi).not.toHaveBeenCalled();
    consent(panel).checked = true; consent(panel).dispatchEvent(new Event("change", { bubbles: true }));
    expect(control(panel).disabled).toBe(false);
    await panel._handleDesktopAction("community-mcp-connect");
    expect(panel._fetchHaApi).toHaveBeenCalledTimes(1);
    expect(JSON.parse(panel._fetchHaApi.mock.calls[0][1].body)).toEqual({ acknowledged: true, consent_revision: "a".repeat(64) });
    expect(reload).toHaveBeenCalled(); expect(consent(panel)).toBeNull();
    expect(panel._desktopFeatures.settings.notice).toContain("no tools allowed");
  });
  it("does not replay an uncertain connect and requires fresh consent", async () => {
    const panel = setup(); panel._fetchHaApi = vi.fn().mockRejectedValue(new Error("private-fixture"));
    consent(panel).checked = true; consent(panel).dispatchEvent(new Event("change", { bubbles: true }));
    await panel._handleDesktopAction("community-mcp-connect");
    expect(panel._fetchHaApi).toHaveBeenCalledTimes(1); expect(consent(panel).checked).toBe(false);
    expect(panel.shadowRoot.textContent).not.toContain("private-fixture");
  });
  it("keeps exact existing paused connection controls without allowing a duplicate", () => {
    const panel = setup(status({ state: "paused", server_name: "existing-home", reused: true }));
    expect(control(panel)).toBeNull();
    expect(panel.shadowRoot.querySelector('[data-desktop-action="edit-mcp-tools"][data-id="existing-home"]')).toBeTruthy();
    expect(panel.shadowRoot.textContent).toContain("regular chat");
  });
  it.each(capabilities.slice(0, 4))("never calls private discovery without %s", async (missing) => {
    const panel = setup(status(), capabilities.filter((key) => key !== missing)); panel._fetchHaApi = vi.fn();
    await panel._handleDesktopAction("community-mcp-connect"); expect(panel._fetchHaApi).not.toHaveBeenCalled();
    expect(control(panel)).toBeNull();
  });
  it.each(["ambiguous", "stopped", "connection_changed", "endpoint_unavailable", "secret_unavailable"])("offers actionable %s guidance without connecting", (state) => {
    const panel = setup(status({ state })); expect(control(panel)).toBeNull();
    expect(panel.shadowRoot.querySelector(".community-mcp-shortcut [role=status]")).toBeTruthy();
  });
  it("retains only the public projection and refuses secret-bearing destinations", () => {
    expect(normalizeCommunityMcp({ ...status(), url: "private-fixture", token: "private-fixture" })).toEqual(status());
    expect(normalizeCommunityMcp(status({ destination: "http://ha.local:9583/private-fixture" }))).toBeNull();
    expect(normalizeCommunityMcp(status({ destination: "http://user:private-fixture@ha.local:9583" }))).toBeNull();
    expect(normalizeCommunityMcp(status({ state: "private-fixture" }))).toBeNull();
  });
});
