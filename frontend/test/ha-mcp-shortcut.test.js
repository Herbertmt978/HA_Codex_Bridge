/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";
import { normalizeHaMcpShortcut } from "../src/mcp-setup.js";

const capabilities = ["assist_mcp_selection_v1", "mcp_credential_binding_v1", "mcp_local_v1", "mcp_credentials_v1", "mcp_admin_v1", "mcp_tool_permissions_v1", "mcp_management_v1"];
const ownedName = "ha-assist-123456789abc";
const status = (state = "not_connected", extra = {}) => ({ state, code: state, available: state === "connected", configured: ["paused", "connected", "configured", "expired", "retry"].includes(state), server_name: state === "not_connected" ? null : ownedName, requires_tool_selection: true, ...extra });
const response = (value, ok = true) => ({ ok, json: async () => value });
const control = (panel, action) => panel.shadowRoot.querySelector(`[data-desktop-action="${action}"]`);
const consent = (panel) => panel.shadowRoot.querySelector("[data-ha-mcp-acknowledged]");
const surface = (panel) => panel.shadowRoot.getElementById("desktop-feature-surface");
const acknowledge = (panel) => { consent(panel).checked = true; consent(panel).dispatchEvent(new Event("change", { bubbles: true })); };

function setup(caps = capabilities, value = status()) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._activeDestination = "settings";
  panel._config = { capabilities: caps };
  Object.assign(panel._desktopFeatures.settings, { loaded: true, settingsTab: "mcp", data: { ha_mcp_shortcut: value, mcp_servers: [] } });
  panel._callWS = vi.fn().mockResolvedValue({});
  panel._render(true);
  return panel;
}

describe("installed Home Assistant MCP shortcut", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it.each(capabilities.slice(0, 6))("never calls the shortcut API without %s", async (missing) => {
    const panel = setup(capabilities.filter((capability) => capability !== missing));
    panel._fetchHaApi = vi.fn();
    await panel._loadDesktopDestination("settings", { force: true });
    await panel._handleDesktopAction("ha-mcp-connect");
    expect(panel._fetchHaApi).not.toHaveBeenCalled();
    expect(control(panel, "ha-mcp-connect")).toBeNull();
    expect(surface(panel).textContent).toContain("Manual server setup remains available");
    expect(control(panel, "open-mcp-form")).toBeTruthy();
  });

  it("loads status with HA-auth HTTP only, without authorising on page load", async () => {
    const panel = setup();
    const requests = vi.fn().mockResolvedValue(response(status()));
    panel._hass = { fetchWithAuth: requests };
    await panel._loadDesktopDestination("settings", { force: true });
    expect(requests).toHaveBeenCalledTimes(1);
    const [url, init] = requests.mock.calls[0];
    expect(url).toBe("/api/codex_bridge/mcp/home_assistant");
    expect(init.method).toBeUndefined();
    expect(init.cache).toBe("no-store");
    expect(init.redirect).toBe("error");
    expect(init.headers.Authorization).toBeUndefined();
    expect(consent(panel).checked).toBe(false);
    expect(control(panel, "ha-mcp-connect").disabled).toBe(true);
    expect(panel._callWS.mock.calls.some(([operation]) => operation.includes("home_assistant"))).toBe(false);
  });

  it("keeps consent and mounted controls stable across unrelated HA renders", () => {
    const panel = setup();
    const checkbox = consent(panel);
    acknowledge(panel);
    checkbox.focus();
    panel._render(true);
    expect(consent(panel)).toBe(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(control(panel, "ha-mcp-connect").disabled).toBe(false);
    expect(panel.shadowRoot.activeElement).toBe(checkbox);
    expect(surface(panel).textContent).toContain("administrator identity");
    expect(surface(panel).textContent).toContain("beyond Assist’s exposed entities");
    expect(surface(panel).textContent).toContain("renews short-lived access tokens hourly");
    expect(surface(panel).textContent).toContain("normal activity expiry");
    expect(checkbox.getAttribute("aria-describedby")).toBe("ha-mcp-consent-detail");
  });

  it("requires explicit consent and sends one connect mutation for repeated clicks", async () => {
    const panel = setup();
    let finish;
    panel._fetchHaApi = vi.fn().mockImplementation((_url, init) => init.method === "POST"
      ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(response(status("configured"))));
    await panel._handleDesktopAction("ha-mcp-connect");
    expect(panel._fetchHaApi).not.toHaveBeenCalled();
    acknowledge(panel);
    const pending = panel._handleDesktopAction("ha-mcp-connect");
    await panel._handleDesktopAction("ha-mcp-connect");
    expect(panel._fetchHaApi).toHaveBeenCalledTimes(1);
    const init = panel._fetchHaApi.mock.calls[0][1];
    expect(JSON.parse(init.body)).toEqual({ operation: "connect", acknowledged: true });
    expect(consent(panel).checked).toBe(false);
    expect(control(panel, "ha-mcp-connect").disabled).toBe(true);
    finish(response(status("configured")));
    await pending;
    expect(consent(panel)).toBeNull();
    expect(control(panel, "edit-mcp-tools").dataset.id).toBe(ownedName);
    expect(surface(panel).textContent).toContain("no tools allowed");
    expect(surface(panel).textContent).toContain("does not enable the Assist conversation agent");
  });

  it("does not replay a failed mutation or retain provider messages and credentials", async () => {
    const panel = setup();
    const secret = "private-fixture-secret";
    panel._fetchHaApi = vi.fn().mockResolvedValue(response({ code: "unexpected", message: secret, token: secret }, false));
    acknowledge(panel);
    await panel._handleDesktopAction("ha-mcp-connect");
    expect(panel._fetchHaApi).toHaveBeenCalledTimes(1);
    expect(consent(panel).checked).toBe(false);
    expect(surface(panel).textContent).toContain("not replayed");
    expect(surface(panel).textContent).not.toContain(secret);
    expect(JSON.stringify(panel._desktopFeatures)).not.toContain(secret);
  });

  it("retains only the fixed status projection even if a response contains secrets", async () => {
    const panel = setup();
    panel._fetchHaApi = vi.fn().mockResolvedValue(response({ ...status("paused"), message: "private-fixture", authentication: { token: "private-fixture" }, url: "https://private-fixture" }));
    await panel._loadDesktopDestination("settings", { force: true });
    expect(panel._desktopFeatures.settings.data.ha_mcp_shortcut).toEqual(status("paused"));
    expect(JSON.stringify(panel._desktopFeatures)).not.toContain("private-fixture");
    expect(surface(panel).textContent).not.toContain("private-fixture");
  });

  it.each(["unknown", "<img onerror=alert(1)>"])("rejects unrecognised status %s without reflecting it", async (state) => {
    const panel = setup();
    panel._fetchHaApi = vi.fn().mockResolvedValue(response(status(state)));
    await panel._loadDesktopDestination("settings", { force: true });
    expect(panel._desktopFeatures.settings.data.ha_mcp_shortcut).toBeNull();
    expect(control(panel, "ha-mcp-connect")).toBeNull();
    expect(surface(panel).innerHTML).not.toContain("onerror");
  });

  it("rejects arbitrary server names and non-boolean flags", () => {
    expect(normalizeHaMcpShortcut(status("connected", { server_name: "manual-server" }))).toBeNull();
    expect(normalizeHaMcpShortcut(status("connected", { configured: "true" }))).toBeNull();
    expect(normalizeHaMcpShortcut(status("connected", { state: { toString: () => "connected" } }))).toBeNull();
  });

  it.each(["unavailable", "reauthorise", "expired", "retry", "cleanup_pending", "invalid_journal"])("shows fixed actionable %s guidance", (state) => {
    const panel = setup(capabilities, status(state, { configured: state === "expired", server_name: ["expired", "cleanup_pending"].includes(state) ? ownedName : null }));
    expect(surface(panel).querySelector('[role="status"]')).toBeTruthy();
    if (state === "expired") expect(control(panel, "ha-mcp-refresh")).toBeTruthy();
    if (state === "cleanup_pending") expect(control(panel, "ha-mcp-disconnect").textContent).toContain("Retry");
    if (state === "reauthorise") expect(control(panel, "ha-mcp-connect").disabled).toBe(true);
    if (["unavailable", "retry", "invalid_journal"].includes(state)) expect(control(panel, "ha-mcp-connect")).toBeNull();
  });

  it("uses existing tool permissions and resume; preserves manual server controls", async () => {
    const panel = setup(capabilities, status("paused"));
    const state = panel._desktopFeatures.settings;
    state.data.mcp_servers = [{ name: ownedName, enabled: false, auth: "bearer", tool_policy: "selected", revision: "owned" }, { name: "manual", enabled: false, auth: "bearer", revision: "manual" }];
    panel._callWS.mockResolvedValue({ server: ownedName, catalogue_available: true, tools: [], enabled_tools: [], mode: "selected" });
    panel._renderDesktopSurface();
    expect(panel.shadowRoot.querySelector(`[data-desktop-action="remove-mcp"][data-id="${ownedName}"]`)).toBeNull();
    expect(panel.shadowRoot.querySelector('[data-desktop-action="remove-mcp"][data-id="manual"]')).toBeTruthy();
    expect(panel.shadowRoot.querySelector('[data-desktop-action="edit-mcp-credential"][data-id="manual"]')).toBeTruthy();
    await panel._handleDesktopAction("edit-mcp-tools", { id: ownedName });
    expect(panel._callWS).toHaveBeenCalledWith("list_mcp_tools", { name: ownedName });
    expect(state.form).toBe("mcp-tools");
    const mutation = vi.spyOn(panel, "_mcpConnectionMutation").mockResolvedValue(true);
    await panel._handleDesktopAction("resume-mcp", { id: ownedName });
    expect(mutation).toHaveBeenCalledWith({ operation: "state", name: ownedName, revision: "owned", enabled: true }, state);
  });

  it.each(["refresh", "disconnect"])("sends only the fixed %s operation and keeps drafts/manual servers", async (operation) => {
    const panel = setup(capabilities, status("expired"));
    const state = panel._desktopFeatures.settings;
    const manual = { name: "manual", enabled: false };
    state.formDraft = { name: "unsaved-custom", local: true };
    state.agentsDrafts = { global: "unsaved instructions" };
    panel._callWS.mockImplementation(async (name) => name === "list_mcp" ? [manual] : {});
    panel._fetchHaApi = vi.fn().mockResolvedValue(response(operation === "disconnect" ? status() : status("paused")));
    await panel._handleDesktopAction(`ha-mcp-${operation}`);
    expect(JSON.parse(panel._fetchHaApi.mock.calls[0][1].body)).toEqual({ operation });
    expect(state.data.mcp_servers).toEqual([manual]);
    expect(state.formDraft.name).toBe("unsaved-custom");
    expect(state.agentsDrafts.global).toBe("unsaved instructions");
    if (operation === "disconnect") expect(surface(panel).textContent).toContain("Home authorisation revoked");
  });
});
