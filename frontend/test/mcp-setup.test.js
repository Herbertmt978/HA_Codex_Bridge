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

describe("MCP connection management", () => {
  const capabilities = ["mcp_admin_v1", "mcp_management_v1", "mcp_credentials_v1"];
  const server = { name: "secured", endpoint: "https://mcp.example.com", auth: "bearer", network: "public", credential_configured: true, enabled: false, revision: "a".repeat(64), tool_count: 0, resource_count: 0 };
  async function editForm() {
    const panel = setup(capabilities);
    panel._desktopFeatures.settings.data.mcp_servers = [{ ...server }];
    await panel._handleDesktopAction("edit-mcp-connection", { id: server.name });
    vi.spyOn(panel, "_accessToken").mockReturnValue("ha-fixture-token");
    return panel;
  }
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("keeps existing controls and update guidance for older Apps", () => {
    const panel = setup(["mcp_admin_v1"]);
    panel._desktopFeatures.settings.data.mcp_servers = [server];
    panel._renderDesktopSurface();
    expect(action(panel, "remove-mcp")).toBeTruthy();
    expect(action(panel, "resume-mcp")).toBeNull();
    expect(panel.shadowRoot.getElementById("desktop-feature-surface").textContent).toContain("update both");
  });

  it("requires an explicit authentication decision and fresh destination consent", async () => {
    const panel = await editForm();
    expect(field(panel, "url").value).toBe("");
    expect(field(panel, "credential_action").value).toBe("");
    expect(field(panel, "endpoint_acknowledged").checked).toBe(false);
    field(panel, "url").value = "https://new.example.com/private-fixture";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    field(panel, "credential_action").value = "keep";
    field(panel, "credential_action").dispatchEvent(new Event("change", { bubbles: true }));
    expect(field(panel, "url").value).toBe("https://new.example.com/private-fixture");
    field(panel, "endpoint_acknowledged").checked = true;
    field(panel, "url").value = "https://other.example.com/tools";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    expect(field(panel, "endpoint_acknowledged").checked).toBe(false);
  });

  it("uses private HTTP for pause/resume and preserves the selected revision", async () => {
    const panel = await editForm();
    let request;
    vi.stubGlobal("fetch", vi.fn(async (url, options) => { request = { url, payload: JSON.parse(options.body) }; return { ok: true }; }));
    await panel._handleDesktopAction("resume-mcp", { id: server.name });
    expect(request).toEqual({ url: "/api/codex_bridge/mcp/connections", payload: { operation: "state", name: server.name, revision: server.revision, enabled: true } });
    expect(panel._desktopFeatures.settings.notice).toContain("subsequent turns");
  });

  it("clears endpoint and new credentials after a rejected save without leaking provider errors", async () => {
    const panel = await editForm();
    field(panel, "credential_action").value = "replace";
    field(panel, "credential_action").dispatchEvent(new Event("change", { bubbles: true }));
    field(panel, "url").value = "https://new.example.com/private-fixture";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    const token = panel.shadowRoot.querySelector("[data-mcp-token]");
    token.value = "synthetic-replacement-token";
    field(panel, "auth_acknowledged").checked = true;
    field(panel, "endpoint_acknowledged").checked = true;
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 409, json: async () => ({ code: "conflict", message: "synthetic-replacement-token" }) })));
    await panel._handleDesktopAction("submit-mcp-connection", {}, token);
    expect(token.value).toBe("");
    expect(field(panel, "url").value).toBe("");
    expect(field(panel, "endpoint_acknowledged").checked).toBe(false);
    expect(panel._desktopFeatures.settings.formError).toContain("connection changed");
    expect(JSON.stringify(panel._desktopFeatures)).not.toContain("synthetic-replacement-token");
    expect(panel.shadowRoot.textContent).not.toContain("synthetic-replacement-token");
    vi.unstubAllGlobals();
  });
});

describe("isolated stdio MCP packages", () => {
  const capabilities = ["mcp_admin_v1", "mcp_management_v1", "mcp_tool_permissions_v1", "mcp_stdio_v1"];
  const digest = "a".repeat(64);
  const fixture = (revision = "1.0.0") => ({ package_id: "safe-probe", revision, title: "Safe probe",
    source: "https://example.org/probe", licence: "MIT", python: "3.14", digest,
    entrypoint: ["python3.14", "-m", "safe_probe"], tools: ["read_probe"], network: "none", files: "none", environment: [] });
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("keeps package controls absent on an older App", () => {
    const panel = setup(["mcp_admin_v1", "mcp_management_v1"]);
    panel._desktopFeatures.settings.data.stdio_packages = [fixture()];
    panel._desktopFeatures.settings.data.mcp_servers = [{ name: "old-probe", transport: "stdio", enabled: false, package_id: "safe-probe", package_revision: "1.0.0", startup: "paused" }];
    panel._renderDesktopSurface();
    expect(action(panel, "open-stdio-form")).toBeNull();
    expect(action(panel, "resume-mcp")).toBeNull();
    expect(action(panel, "remove-mcp")).toBeTruthy();
    expect(action(panel, "toggle-stdio-details")).toBeTruthy();
    expect(panel.shadowRoot.textContent).toContain("Isolated local packages require a newer App");
    expect(panel._callWS).not.toHaveBeenCalledWith("list_stdio_packages");
  });

  it("shows package provenance and creates a paused server only after consent", async () => {
    const panel = setup(capabilities);
    panel._desktopFeatures.settings.data.stdio_packages = [fixture()];
    panel._loadDesktopDestination = vi.fn().mockResolvedValue();
    panel._renderDesktopSurface();
    await panel._handleDesktopAction("open-stdio-form");
    expect(panel.shadowRoot.textContent).toContain("Fixed command: python3.14 -m safe_probe");
    expect(panel.shadowRoot.textContent).toContain("Package SHA-256: aaaaaaaaaaaaaaaa…");
    expect(panel.shadowRoot.textContent).toContain("no network, no workspace files");
    field(panel, "stdio_name").value = "probe";
    await panel._handleDesktopAction("submit-stdio-add", {}, action(panel, "submit-stdio-add"));
    expect(panel._callWS).not.toHaveBeenCalledWith("add_stdio_mcp", expect.anything());
    field(panel, "stdio_acknowledged").click();
    await panel._handleDesktopAction("submit-stdio-add", {}, action(panel, "submit-stdio-add"));
    expect(panel._callWS).toHaveBeenCalledWith("add_stdio_mcp", { name: "probe", package_id: "safe-probe", revision: "1.0.0", acknowledged: true });
    expect(panel._desktopFeatures.settings.notice).toContain("paused state");
  });

  it("hides malformed packages and clears approval when a revision changes", async () => {
    const panel = setup(capabilities);
    panel._desktopFeatures.settings.data.stdio_packages = [fixture(), fixture("1.1.0"), { ...fixture("bad"), digest: "invalid" }];
    await panel._handleDesktopAction("open-stdio-form");
    const select = field(panel, "stdio_package");
    expect(select.options).toHaveLength(2);
    field(panel, "stdio_acknowledged").click();
    select.value = "safe-probe:1.1.0";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(field(panel, "stdio_acknowledged").checked).toBe(false);
    expect(panel.shadowRoot.textContent).toContain("Version: 1.1.0");
    expect(panel.shadowRoot.textContent).not.toContain("Version: bad");
    field(panel, "stdio_acknowledged").click();
    panel._desktopFeatures.settings.data.stdio_packages[1] = { ...fixture("1.1.0"), digest: "b".repeat(64) };
    panel._renderDesktopSurface();
    expect(field(panel, "stdio_acknowledged").checked).toBe(false);
  });

  it("updates only a paused stdio server and offers rollback with confirmation", async () => {
    const panel = setup(capabilities);
    const state = panel._desktopFeatures.settings;
    state.data.stdio_packages = [fixture(), fixture("1.1.0")];
    state.data.mcp_servers = [{ name: "probe", transport: "stdio", package_id: "safe-probe", package_revision: "1.0.0", enabled: false, revision: "b".repeat(64), startup: "ready", rollback_available: true, failure: "private-worker-output" }];
    panel._loadDesktopDestination = vi.fn().mockResolvedValue();
    panel._renderDesktopSurface();
    expect(action(panel, "edit-mcp-connection")).toBeNull();
    expect(action(panel, "edit-mcp-tools")).toBeTruthy();
    await panel._handleDesktopAction("toggle-stdio-details", { id: "probe" });
    expect(panel.shadowRoot.querySelector(".stdio-server-details").textContent).toContain("Needs attention; check App logs");
    expect(panel.shadowRoot.textContent).not.toContain("private-worker-output");
    await panel._handleDesktopAction("open-stdio-update", { id: "probe" });
    expect(field(panel, "stdio_package").value).toBe("safe-probe:1.1.0");
    field(panel, "stdio_acknowledged").click();
    await panel._handleDesktopAction("submit-stdio-update", {}, action(panel, "submit-stdio-update"));
    expect(panel._callWS).toHaveBeenCalledWith("update_stdio_mcp", { name: "probe", revision: "1.1.0", expected_revision: "b".repeat(64), acknowledged: true });
    await panel._handleDesktopAction("rollback-stdio", { id: "probe" });
    expect(state.confirmAction?.action).toBe("rollback-stdio");
    expect(state.confirmAction?.dataset.expectedRevision).toBe("b".repeat(64));
    expect(panel._callWS).not.toHaveBeenCalledWith("rollback_stdio_mcp", expect.anything());
    state.data.mcp_servers[0].revision = "c".repeat(64);
    await panel._handleDesktopAction("confirm-desktop");
    expect(panel._callWS).toHaveBeenCalledWith("rollback_stdio_mcp", { name: "probe", expected_revision: "b".repeat(64) });
  });
});

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

describe("MCP tool permissions", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("shows untrusted descriptions as text and saves an explicit allow-list", async () => {
    const panel = setup(["mcp_admin_v1", "mcp_management_v1", "mcp_tool_permissions_v1"]);
    panel._desktopFeatures.settings.data.mcp_servers = [{ name: "vendor", tool_policy: "all", revision: "a".repeat(64) }];
    const inventory = { server: "vendor", endpoint: "https://mcp.example", mode: "all",
      enabled_tools: [], catalogue_available: true, catalogue_revision: "b".repeat(64), revision: "a".repeat(64),
      tools: [{ name: "read", description: "<img src=x onerror=alert(1)>", read_only: true },
        { name: "delete", description: "Deletes a record", destructive: true }], stale_tools: [] };
    panel._callWS = vi.fn(async (operation) => operation === "list_mcp_tools" ? inventory : {});
    panel._renderDesktopSurface();
    await panel._handleDesktopAction("edit-mcp-tools", { id: "vendor" });
    panel._renderDesktopSurface();
    expect(panel.shadowRoot.querySelector(".mcp-tool-list img")).toBeNull();
    expect(panel.shadowRoot.querySelector(".mcp-tool-list").textContent).toContain("Claims destructive");
    const deleteTool = [...panel.shadowRoot.querySelectorAll("[data-mcp-tool]")].find((input) => input.dataset.mcpTool === "delete");
    deleteTool.click();
    panel._renderDesktopSurface();
    expect([...panel.shadowRoot.querySelectorAll("[data-mcp-tool]:checked")].map((input) => input.dataset.mcpTool)).toEqual(["read"]);
    await panel._handleDesktopAction("submit-mcp-tools", {}, action(panel, "submit-mcp-tools"));
    expect(panel._callWS).toHaveBeenCalledWith("set_mcp_tools", { name: "vendor", enabled_tools: ["read"],
      revision: "a".repeat(64), catalogue_revision: "b".repeat(64) });
  });

  it("starts paired new servers with no allowed tools", async () => {
    const panel = setup(["mcp_admin_v1", "mcp_tool_permissions_v1"]);
    await panel._handleDesktopAction("choose-custom-mcp");
    field(panel, "name").value = "scoped";
    field(panel, "url").value = "https://tools.example.com/mcp";
    await panel._handleDesktopAction("submit-mcp", {}, field(panel, "url"));
    expect(panel._callWS).toHaveBeenCalledWith("add_mcp", {
      name: "scoped", url: "https://tools.example.com/mcp", require_tool_selection: true,
    });
  });

  it("keeps changes unavailable when discovery is stale", async () => {
    const panel = setup(["mcp_admin_v1", "mcp_management_v1", "mcp_tool_permissions_v1"]);
    panel._desktopFeatures.settings.data.mcp_servers = [{ name: "vendor" }];
    panel._callWS = vi.fn().mockResolvedValue({ server: "vendor", catalogue_available: false, tools: [], stale_tools: ["old"] });
    await panel._handleDesktopAction("edit-mcp-tools", { id: "vendor" });
    panel._renderDesktopSurface();
    expect(action(panel, "submit-mcp-tools").disabled).toBe(true);
    expect(panel.shadowRoot.textContent).toContain("previously allowed tool is no longer advertised");
  });
});


describe("MCP write-only credentials", () => {
  const capabilities = ["mcp_admin_v1", "mcp_local_v1", "mcp_credentials_v1"];
  async function credentialForm(mode = "bearer") {
    const panel = setup(capabilities);
    await panel._handleDesktopAction("choose-custom-mcp");
    const state = panel._desktopFeatures.settings;
    state.formDraft = { name: "secured", url: "https://mcp.example.com", auth_mode: mode };
    panel._renderDesktopSurface();
    panel._accessToken = () => "synthetic-ha-token";
    panel._loadDesktopDestination = vi.fn().mockResolvedValue();
    return panel;
  }

  it("keeps secret controls out of generic drafts and browser persistence", async () => {
    const panel = await credentialForm();
    const input = panel.shadowRoot.querySelector("[data-mcp-token]");
    input.value = "synthetic-private-token";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    expect(input.type).toBe("password");
    expect(input.dataset.desktopField).toBeUndefined();
    expect(JSON.stringify(panel._desktopFeatures)).not.toContain(input.value);
    expect(JSON.stringify(localStorage)).not.toContain(input.value);
    expect(JSON.stringify(sessionStorage)).not.toContain(input.value);
    expect(field(panel, "oauth_client_id")).toBeNull();
    expect(panel.shadowRoot.querySelector(".mcp-authentication").textContent).toContain("App backups");
  });

  it("submits the credential through authenticated HTTP and immediately clears it", async () => {
    const panel = await credentialForm();
    const input = panel.shadowRoot.querySelector("[data-mcp-token]");
    input.value = "synthetic-private-token";
    field(panel, "auth_acknowledged").checked = true;
    let captured;
    vi.stubGlobal("fetch", vi.fn(async (url, options) => { captured = { url, options }; return { ok: true }; }));
    try {
      await panel._handleDesktopAction("submit-mcp", {}, input);
      expect(captured.url).toBe("/api/codex_bridge/mcp/credentials");
      expect(captured.options.cache).toBe("no-store");
      expect(captured.options.redirect).toBe("error");
      expect(JSON.parse(captured.options.body)).toEqual({ operation:"create", name:"secured", url:"https://mcp.example.com", authentication:{mode:"bearer",token:"synthetic-private-token"}, auth_acknowledged:true });
      expect(input.value).toBe("");
      expect(JSON.stringify(panel._desktopFeatures)).not.toContain("synthetic-private-token");
      expect(panel._callWS).not.toHaveBeenCalledWith("add_mcp", expect.anything());
      expect(panel._desktopFeatures.settings.form).toBeNull();
    } finally { vi.unstubAllGlobals(); }
  });

  it("clears submitted secrets and displays a fixed error on failure", async () => {
    const panel = await credentialForm();
    const input = panel.shadowRoot.querySelector("[data-mcp-token]");
    input.value = "synthetic-private-token";
    field(panel, "auth_acknowledged").checked = true;
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("synthetic-private-token")));
    try {
      await panel._handleDesktopAction("submit-mcp", {}, input);
      expect(input.value).toBe("");
      expect(panel.shadowRoot.querySelector("[data-mcp-token]").value).toBe("");
      expect(panel._desktopFeatures.settings.formError).toContain("re-enter");
      expect(panel.shadowRoot.textContent).not.toContain("synthetic-private-token");
      expect(JSON.stringify(panel._desktopFeatures)).not.toContain("synthetic-private-token");
      expect(field(panel, "auth_acknowledged").checked).toBe(false);
    } finally { vi.unstubAllGlobals(); }
  });

  it("requires new consent and clears secrets when the destination changes", async () => {
    const panel = await credentialForm();
    const input = panel.shadowRoot.querySelector("[data-mcp-token]"); input.value = "synthetic-private-token";
    field(panel, "auth_acknowledged").checked = true;
    field(panel, "url").value = "https://different.example.com";
    field(panel, "url").dispatchEvent(new Event("input", { bubbles: true }));
    expect(input.value).toBe("");
    expect(field(panel, "auth_acknowledged").checked).toBe(false);
  });

  it("offers replacement without reading an existing credential", async () => {
    const panel = setup(capabilities);
    panel._desktopFeatures.settings.data.mcp_servers = [{ name:"secured", endpoint:"https://mcp.example.com", auth:"headers", credential_configured:true, network:"public" }];
    await panel._handleDesktopAction("edit-mcp-credential", { id:"secured" });
    expect(panel.shadowRoot.querySelector("[data-mcp-header-value]").value).toBe("");
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(field(panel, "url")).toBeNull();
    expect(panel.shadowRoot.querySelector("form").textContent).toContain("remove and add the server again");
  });

  it("preserves custom named headers as separate write-only values", async () => {
    const panel = await credentialForm("headers");
    panel.shadowRoot.querySelector("[data-mcp-header-name]").value = "X-Api-Key";
    panel.shadowRoot.querySelector("[data-mcp-header-value]").value = "synthetic-key";
    field(panel, "auth_acknowledged").checked = true;
    let payload;
    vi.stubGlobal("fetch", vi.fn(async (_, options) => { payload = JSON.parse(options.body); return { ok:true }; }));
    try {
      await panel._handleDesktopAction("submit-mcp", {}, action(panel,"submit-mcp"));
      expect(payload.authentication).toEqual({mode:"headers",headers:[{name:"X-Api-Key",value:"synthetic-key"}]});
    } finally { vi.unstubAllGlobals(); }
  });
});
