/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

const capabilities = ["assist_mcp_selection_v1", "mcp_credential_binding_v1", "mcp_local_v1",
  "mcp_credentials_v1", "mcp_admin_v1", "mcp_tool_permissions_v1", "mcp_management_v1",
  "mcp_tool_approval_v1", "community_mcp_quick_connect_v1"];
const nativeName = "ha-assist-123456789abc";
const communityName = "ha-community-123456789abc";
const nativeStatus = (state = "configured", name = nativeName) => ({
  state, code: state, available: false, configured: name !== null,
  server_name: name, requires_tool_selection: true,
});
const communityStatus = () => ({ state: "configured", server_name: communityName, reused: true,
  version: "8.5.0", destination: "http://192.168.1.20:9583", consent_revision: "a".repeat(64) });

function setup({ native = nativeStatus(), community = communityStatus(), servers = [] } = {}) {
  const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
  panel._activeDestination = "settings"; panel._config = { capabilities };
  Object.assign(panel._desktopFeatures.settings, { loaded: true, settingsTab: "mcp",
    data: { ha_mcp_shortcut: native, community_mcp: community, mcp_servers: servers } });
  panel._loadDesktopDestination = vi.fn().mockResolvedValue();
  panel._accessToken = vi.fn(() => "synthetic-token");
  panel._render(true);
  return panel;
}

describe("one-switch Home Assistant MCP setup", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("shows saved enablement even when tool status is unavailable", () => {
    const servers = [nativeName, communityName].map((name) => ({
      name, enabled: true, tool_policy: "selected", tool_approval_mode: "approve",
      tool_count: 0, status_unavailable: true,
    }));
    const panel = setup({ servers });
    expect(panel.shadowRoot.querySelector('[data-home-mcp-toggle="native"]').checked).toBe(true);
    expect(panel.shadowRoot.querySelector('[data-home-mcp-toggle="community"]').checked).toBe(true);
    expect(panel.shadowRoot.textContent.match(/Tool status is temporarily unavailable/g)).toHaveLength(2);
  });

  it("shows one native switch and selects the current Assist tools on first use", async () => {
    const server = { name: nativeName, enabled: true, tool_policy: "selected", tool_approval_mode: "auto", tool_count: 0, revision: "r1" };
    const panel = setup({ servers: [server] });
    const toggle = panel.shadowRoot.querySelector('[data-home-mcp-toggle="native"]');
    expect(toggle?.checked).toBe(false);
    expect(panel.shadowRoot.querySelector('[data-ha-mcp-acknowledged]')).toBeNull();
    panel._callWS = vi.fn().mockImplementation((operation) => {
      if (operation === "list_mcp") return Promise.resolve([server]);
      if (operation === "list_mcp_tools") return Promise.resolve({ catalogue_available: true,
        catalogue_truncated: false, tools: [{ name: "intent__HassTurnOn" }, { name: "intent__HassTurnOff" }],
        enabled_tools: [], revision: "r1", catalogue_revision: "c1" });
      if (operation === "set_mcp_tools") return Promise.resolve({});
      throw new Error("Unexpected operation");
    });
    toggle.checked = true; toggle.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", {
      name: nativeName, enabled_tools: ["intent__HassTurnOn", "intent__HassTurnOff"],
      revision: "r1", catalogue_revision: "c1", approval_mode: "approve",
    }));
  });

  it("keeps the community server's selected tools fixed and turns it off by pausing", async () => {
    const server = { name: communityName, enabled: true, tool_policy: "selected", tool_approval_mode: "auto", tool_count: 77, revision: "r2" };
    const panel = setup({ servers: [server] });
    panel._callWS = vi.fn().mockImplementation((operation) => {
      if (operation === "list_mcp") return Promise.resolve([server]);
      if (operation === "list_mcp_tools") return Promise.resolve({ catalogue_available: true,
        catalogue_truncated: false, tools: [{ name: "ha_call_service" }, { name: "ha_bulk_control" }],
        enabled_tools: ["ha_call_service"], revision: "r2", catalogue_revision: "c2" });
      if (operation === "set_mcp_tools") return Promise.resolve({});
      throw new Error("Unexpected operation");
    });
    await panel._toggleHomeMcp("community", true, panel._desktopFeatures.settings);
    expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", {
      name: communityName, enabled_tools: ["ha_call_service"],
      revision: "r2", catalogue_revision: "c2", approval_mode: "approve",
    });
    panel._fetchHaApi = vi.fn().mockResolvedValue({ ok: true });
    await panel._toggleHomeMcp("community", false, panel._desktopFeatures.settings);
    expect(JSON.parse(panel._fetchHaApi.mock.calls[0][1].body)).toEqual({
      operation: "state", name: communityName, revision: "r2", enabled: false,
    });
  });

  it("connects an installed built-in server and allows its current tools with one tick", async () => {
    const server = { name: nativeName, enabled: true, tool_policy: "selected", tool_approval_mode: "auto", tool_count: 0, revision: "r4" };
    const panel = setup({ native: nativeStatus("not_connected", null) });
    panel._fetchHaApi = vi.fn().mockResolvedValue({ ok: true, json: async () => nativeStatus() });
    panel._callWS = vi.fn().mockImplementation((operation) => {
      if (operation === "list_mcp") return Promise.resolve([server]);
      if (operation === "list_mcp_tools") return Promise.resolve({ catalogue_available: true,
        catalogue_truncated: false, tools: [{ name: "intent__HassTurnOn" }],
        enabled_tools: [], revision: "r4", catalogue_revision: "c4" });
      if (operation === "set_mcp_tools") return Promise.resolve({});
      throw new Error("Unexpected operation");
    });
    const result = await panel._toggleHomeMcp("native", true, panel._desktopFeatures.settings);
    expect(result).toBe(true);
    expect(JSON.parse(panel._fetchHaApi.mock.calls[0][1].body)).toEqual({ operation: "connect", acknowledged: true });
    expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", {
      name: nativeName, enabled_tools: ["intent__HassTurnOn"], revision: "r4",
      catalogue_revision: "c4", approval_mode: "approve",
    });
  });

  it("fails closed when discovery is incomplete", async () => {
    const server = { name: nativeName, enabled: true, tool_policy: "selected", tool_approval_mode: "auto", tool_count: 0, revision: "r3" };
    const panel = setup({ servers: [server] });
    panel._callWS = vi.fn().mockImplementation((operation) => operation === "list_mcp"
      ? Promise.resolve([server]) : Promise.resolve({ catalogue_available: true, catalogue_truncated: true,
        tools: [{ name: "intent__HassTurnOn" }], enabled_tools: [], revision: "r3", catalogue_revision: "c3" }));
    const saved = await panel._toggleHomeMcp("native", true, panel._desktopFeatures.settings);
    expect(saved).toBe(false);
    expect(panel._callWS.mock.calls.some(([operation]) => operation === "set_mcp_tools")).toBe(false);
  });

  it("locks down a paused unrestricted server before resuming it", async () => {
    const paused = { name: communityName, enabled: false, tool_policy: "all", revision: "r5" };
    const locked = { ...paused, tool_policy: "selected", tool_count: 0, revision: "r6" };
    const resumed = { ...locked, enabled: true, revision: "r7" };
    const panel = setup({ servers: [paused] });
    let listCount = 0;
    const calls = [];
    panel._callWS = vi.fn().mockImplementation((operation, payload) => {
      calls.push(operation);
      if (operation === "list_mcp") return Promise.resolve([[paused], [locked], [resumed]][listCount++]);
      if (operation === "lock_down_mcp_tools") {
        expect(payload).toEqual({ name: communityName, revision: "r5" });
        return Promise.resolve({});
      }
      if (operation === "list_mcp_tools") return Promise.resolve({ catalogue_available: true,
        catalogue_truncated: false, tools: [{ name: "ha_call_service" }],
        enabled_tools: [], revision: "r7", catalogue_revision: "c7" });
      if (operation === "set_mcp_tools") return Promise.resolve({});
      throw new Error("Unexpected operation");
    });
    panel._fetchHaApi = vi.fn().mockImplementation(() => { calls.push("resume"); return Promise.resolve({ ok: true }); });
    expect(await panel._toggleHomeMcp("community", true, panel._desktopFeatures.settings)).toBe(true);
    expect(calls.indexOf("lock_down_mcp_tools")).toBeLessThan(calls.indexOf("resume"));
    expect(calls.indexOf("resume")).toBeLessThan(calls.indexOf("list_mcp_tools"));
    expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", {
      name: communityName, enabled_tools: ["ha_call_service"], revision: "r7",
      catalogue_revision: "c7", approval_mode: "approve",
    });
  });

  it("pauses an active unrestricted server before locking down and discovering tools", async () => {
    const active = { name: communityName, enabled: true, tool_policy: "all", revision: "r8" };
    const paused = { ...active, enabled: false, revision: "r9" };
    const locked = { ...paused, tool_policy: "selected", tool_count: 0, revision: "r10" };
    const resumed = { ...locked, enabled: true, revision: "r11" };
    const panel = setup({ servers: [active] });
    const calls = [];
    let listCount = 0;
    panel._callWS = vi.fn().mockImplementation((operation) => {
      calls.push(operation);
      if (operation === "list_mcp") return Promise.resolve([[active], [paused], [locked], [resumed]][listCount++]);
      if (operation === "lock_down_mcp_tools" || operation === "set_mcp_tools") return Promise.resolve({});
      if (operation === "list_mcp_tools") return Promise.resolve({ catalogue_available: true,
        catalogue_truncated: false, tools: [{ name: "ha_call_service" }],
        enabled_tools: [], revision: "r11", catalogue_revision: "c11" });
      throw new Error("Unexpected operation");
    });
    panel._fetchHaApi = vi.fn().mockImplementation((_url, options) => {
      calls.push(JSON.parse(options.body).enabled ? "resume" : "pause");
      return Promise.resolve({ ok: true });
    });
    expect(await panel._toggleHomeMcp("community", true, panel._desktopFeatures.settings)).toBe(true);
    expect(calls.indexOf("pause")).toBeLessThan(calls.indexOf("lock_down_mcp_tools"));
    expect(calls.indexOf("lock_down_mcp_tools")).toBeLessThan(calls.indexOf("resume"));
    expect(calls.indexOf("resume")).toBeLessThan(calls.indexOf("list_mcp_tools"));
    expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", {
      name: communityName, enabled_tools: ["ha_call_service"], revision: "r11",
      catalogue_revision: "c11", approval_mode: "approve",
    });
  });

  it("leaves an active unrestricted server paused if lockdown fails", async () => {
    const active = { name: communityName, enabled: true, tool_policy: "all", revision: "r8" };
    const paused = { ...active, enabled: false, revision: "r9" };
    const panel = setup({ servers: [active] });
    let listCount = 0;
    panel._callWS = vi.fn().mockImplementation((operation) => {
      if (operation === "list_mcp") return Promise.resolve([[active], [paused]][listCount++]);
      if (operation === "lock_down_mcp_tools") return Promise.reject(new Error("lockdown failed"));
      throw new Error("Unexpected operation");
    });
    panel._fetchHaApi = vi.fn().mockResolvedValue({ ok: true });
    expect(await panel._toggleHomeMcp("community", true, panel._desktopFeatures.settings)).toBe(false);
    expect(panel._fetchHaApi).toHaveBeenCalledTimes(1);
    expect(JSON.parse(panel._fetchHaApi.mock.calls[0][1].body).enabled).toBe(false);
    expect(panel._callWS.mock.calls.some(([operation]) => operation === "list_mcp_tools" || operation === "set_mcp_tools")).toBe(false);
  });

  it("leaves a paused unrestricted server off if it cannot be locked down", async () => {
    const paused = { name: communityName, enabled: false, tool_policy: "all", revision: "r5" };
    const panel = setup({ servers: [paused] });
    panel._callWS = vi.fn().mockImplementation((operation) => operation === "list_mcp"
      ? Promise.resolve([paused]) : Promise.reject(new Error("lockdown failed")));
    panel._fetchHaApi = vi.fn();
    expect(await panel._toggleHomeMcp("community", true, panel._desktopFeatures.settings)).toBe(false);
    expect(panel._fetchHaApi).not.toHaveBeenCalled();
    expect(panel._callWS.mock.calls.some(([operation]) => operation === "set_mcp_tools")).toBe(false);
  });
});
