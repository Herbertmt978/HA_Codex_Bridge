/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";
import { buildAutomationPayload, buildAutomationUpdatePayload, normalizeDesktopError, normalizeDesktopList, normalizePluginsResponse, normalizeSkillsResponse, renderDesktopFeatureSurface } from "../src/desktop-features.js";

describe("desktop feature surfaces", () => {
  beforeEach(() => document.body.replaceChildren());

  it("normalizes bridge list envelopes and bounds error text", () => {
    expect(normalizeDesktopList({ data: [{ id: "one" }] })).toEqual([{ id: "one" }]);
    expect(normalizeDesktopList({ results: "not-a-list" })).toEqual([]);
    expect(normalizeDesktopError({ message: "x".repeat(800) })).toHaveLength(500);
    expect(normalizeSkillsResponse({ data: [{ cwd: "proj", skills: [{ name: "safe" }] }] })).toEqual([{ name: "safe", scope: "proj" }]);
    expect(normalizePluginsResponse({ marketplaces: [{ name: "official", plugins: [{ id: "p1", name: "Plugin" }] }] })).toEqual([{ id: "p1", name: "Plugin", marketplace_name: "official" }]);
    expect(buildAutomationPayload({ project_id: "p1", schedule_type: "interval", interval_seconds: "60", run_at: "2026-01-01T00:00:00Z", mode: "edit" })).toMatchObject({ target: { kind: "standalone", project_id: "p1" }, schedule: { kind: "interval", seconds: 60, anchor_at: "2026-01-01T00:00:00Z" }, mode: "edit" });
    expect(buildAutomationUpdatePayload({ revision: "3", thread_id: "t1", schedule_type: "once", run_at: "2026-01-01T00:00:00Z" })).toMatchObject({ expected_revision: 3, target: { kind: "continue_thread", thread_id: "t1" } });
    expect(buildAutomationPayload({ project_id: "p1", schedule_type: "rrule", rrule: "RRULE:FREQ=DAILY", run_at: "2026-01-01T00:00:00Z", timezone: "Europe/London" }).schedule).toEqual({ kind: "rrule", rule: "RRULE:FREQ=DAILY", start_at: "2026-01-01T00:00:00Z", timezone: "Europe/London" });
  });

  it("loads plugins and marketplaces from one catalogue request", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._callWS = vi.fn().mockResolvedValue({
      marketplaces: [{ name: "official", plugins: [{ id: "plugin-one", name: "Plugin one" }] }],
    });

    await panel._loadDesktopDestination("plugins");

    expect(panel._callWS).toHaveBeenCalledTimes(1);
    expect(panel._callWS).toHaveBeenCalledWith("list_plugins", { workspace_path: "." });
    expect(panel._callWS).not.toHaveBeenCalledWith("list_marketplaces", { workspace_path: "." });
    expect(panel._desktopFeatures.plugins.data).toEqual({
      plugins: [{ id: "plugin-one", name: "Plugin one", marketplace_name: "official" }],
      marketplaces: [{ name: "official", plugins: [{ id: "plugin-one", name: "Plugin one" }] }],
    });
  });

  it("redacts private details from feature errors before rendering them", () => {
    const message = normalizeDesktopError({
      message: "Failed at C:\\private\\workspace\\file.txt via https://private.example:8766/path token=secret owner@example.com",
    });
    expect(message).toContain("[private path]");
    expect(message).toContain("[private address]");
    expect(message).toContain("[private credential]");
    expect(message).toContain("[private account]");
    expect(message).not.toContain("private.example");
    expect(message).not.toContain("C:\\private");
  });

  it("renders hostile remote values as text, never markup", () => {
    const host = document.createElement("div");
    renderDesktopFeatureSurface(host, {
      destination: "skills",
      state: { data: { skills: [{ name: "<img src=x onerror=alert(1)>", scope: "project", enabled: true }] } },
    });
    expect(host.querySelector("img")).toBeNull();
    expect(host.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("uses backend capability fields in plugin, marketplace, and MCP rows", () => {
    const host = document.createElement("div");
    renderDesktopFeatureSurface(host, { destination: "plugins", state: { data: { plugins: [{ name: "P", enabled: true }], marketplaces: [{ name: "M", plugins: [{ name: "P" }] }] } } });
    expect(host.textContent).toContain("P"); expect(host.textContent).toContain("1"); expect(host.textContent).not.toContain("[object Object]");
    renderDesktopFeatureSurface(host, { destination: "settings", state: { settingsTab: "mcp", data: { mcp_servers: [{ name: "MCP", endpoint: "https://mcp.example", startup: "ready", auth: "oauth" }] } } });
    expect(host.textContent).toContain("https://mcp.example");
  });

  it("exposes accessible destination navigation and calls the bridge suffix", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._config = { panel_title: "Codex Bridge", capabilities: ["mcp_admin_v1"] };
    panel._callWS = vi.fn().mockResolvedValue([]);
    panel._render(true);
    const settings = panel.shadowRoot.querySelector('[data-destination="settings"]');
    expect(settings.getAttribute("aria-current")).toBe("false");
    settings.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(panel.shadowRoot.querySelector(".desktop-feature-surface").classList.contains("visible")).toBe(true);
    expect(panel._callWS).toHaveBeenCalledWith("list_mcp");
    expect(panel._callWS).toHaveBeenCalledWith("get_agents");
  });

  it("renders non-MCP settings when the default MCP capability is disabled", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._config = { panel_title: "Codex Bridge", capabilities: [] };
    panel._callWS = vi.fn().mockImplementation((action) => {
      if (action === "list_mcp") return Promise.reject(new Error("MCP is disabled"));
      return Promise.resolve({});
    });
    panel._render(true);
    panel.shadowRoot.querySelector('[data-destination="settings"]').click();
    await Promise.resolve();
    await Promise.resolve();

    expect(panel._callWS).not.toHaveBeenCalledWith("list_mcp");
    expect(panel._desktopFeatures.settings.error).toBe("");
    expect(panel.shadowRoot.querySelector("[data-settings-tab=general]")).toBeTruthy();
    expect(panel.shadowRoot.textContent).toContain("General");
  });

  it("opens an isolated OAuth popup synchronously and navigates it only after HTTPS validation", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings"; panel._desktopFeatures.settings.loaded = true; panel._desktopFeatures.settings.data = { mcp_servers: [] };
    let resolveLogin;
    panel._callWS = vi.fn().mockImplementation(() => new Promise((resolve) => { resolveLogin = resolve; }));
    const popup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(popup);
    const login = panel._handleDesktopAction("login-mcp", { id: "server-one" }, null);
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(popup.opener).toBeNull();
    expect(popup.location.replace).not.toHaveBeenCalled();
    resolveLogin({ authorization_url: "https://auth.example.test/authorize?state=one-shot" });
    await login;
    expect(panel._callWS).toHaveBeenCalledWith("login_mcp", { name: "server-one" });
    expect(popup.location.replace).toHaveBeenCalledWith("https://auth.example.test/authorize?state=one-shot");
    expect(popup.close).not.toHaveBeenCalled();
    expect(JSON.stringify(panel._desktopFeatures.settings)).not.toContain("one-shot");
  });

  it("closes the OAuth popup when login fails or returns an unsafe URL", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings"; panel._desktopFeatures.settings.loaded = true; panel._desktopFeatures.settings.data = { mcp_servers: [] };
    const firstPopup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    const secondPopup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValueOnce(firstPopup).mockReturnValueOnce(secondPopup);
    panel._callWS = vi.fn().mockResolvedValueOnce({ authorization_url: "java" + "script:alert(1)" }).mockRejectedValueOnce(new Error("login failed"));

    await panel._handleDesktopAction("login-mcp", { id: "unsafe" }, null);
    expect(firstPopup.location.replace).not.toHaveBeenCalled();
    expect(firstPopup.close).toHaveBeenCalledOnce();
    expect(panel._desktopFeatures.settings.error).toMatch(/HTTPS/i);

    await panel._handleDesktopAction("login-mcp", { id: "failed" }, null);
    expect(secondPopup.location.replace).not.toHaveBeenCalled();
    expect(secondPopup.close).toHaveBeenCalledOnce();
  });

  it("disables project instruction scope without an active project and rejects forced project mutations", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.data = { agentsScopes: { global: { content: "global" }, project: {} } };
    panel._projects = [];
    panel._activeThread = { thread_id: "direct", project_id: null, attachments: [] };
    panel._callWS = vi.fn();
    panel._render(true);

    const scope = panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]');
    const projectOption = scope.querySelector('option[value="project"]');
    expect(projectOption.disabled).toBe(true);
    expect(scope.value).toBe("global");

    scope.value = "project";
    await panel._handleDesktopAction("save-agents", {}, scope);
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel._desktopFeatures.settings.error).toMatch(/project/i);

    panel._desktopFeatures.settings.error = "";
    await panel._handleDesktopAction("delete-agents", { agentsScope: "project" }, null);
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(panel._desktopFeatures.settings.error).toMatch(/project/i);
    expect(panel._desktopFeatures.settings.confirmAction).toBeNull();
  });

  it("switches instruction scopes without cross-saving content and preserves scoped drafts", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.data = { agentsScopes: { global: { content: "global server" }, project: { content: "project server" } } };
    panel._projects = [{ project_id: "project-one", kind: "project", name: "Project one" }];
    panel._activeThread = { thread_id: "thread-one", project_id: "project-one", attachments: [] };
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._loadDesktopDestination = vi.fn().mockResolvedValue(undefined);
    panel._render(true);

    const scope = panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]');
    const content = panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]');
    expect(content.value).toBe("project server");
    content.value = "unsaved project";
    scope.value = "global";
    scope.dispatchEvent(new Event("change", { bubbles: true }));
    expect(panel._desktopFeatures.settings.agentsDrafts["project:project-one"]).toBe("unsaved project");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]').value).toBe("global server");

    await panel._handleDesktopAction("save-agents", {}, panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]'));
    expect(panel._callWS).toHaveBeenCalledWith("update_agents", { content: "global server" });

    const globalScope = panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]');
    globalScope.value = "project";
    globalScope.dispatchEvent(new Event("change", { bubbles: true }));
    expect(panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]').value).toBe("unsaved project");
  });

  it("preserves typed instruction edits across rerenders", () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "global";
    panel._desktopFeatures.settings.data = { agentsScopes: { global: { content: "server global" }, project: {} } };
    panel._projects = [];
    panel._render(true);

    const content = panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]');
    content.value = "typed but not saved";
    content.dispatchEvent(new Event("input", { bubbles: true }));
    panel._renderDesktopSurface();
    expect(panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]').value).toBe("typed but not saved");
  });

  it("reloads project instructions when switching projects and binds saves to the displayed project", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.agentsProjectId = "project-a";
    panel._desktopFeatures.settings.data = { agentsScopes: { global: { content: "global" }, project: { content: "Project A" } } };
    panel._projects = [
      { project_id: "project-a", kind: "project", name: "Project A" },
      { project_id: "project-b", kind: "project", name: "Project B" },
    ];
    panel._activeThread = null;
    panel._selectedProjectId = "project-a";
    panel._threads = [];
    panel._callWS = vi.fn().mockImplementation((action, payload = {}) => {
      if (action === "get_agents" && payload.project_id === "project-b") return Promise.resolve({ content: "Project B" });
      if (action === "get_agents" && payload.project_id === "project-a") return Promise.resolve({ content: "Project A" });
      if (action === "get_agents") return Promise.resolve({ content: "global" });
      return Promise.resolve({});
    });
    panel._render(true);

    panel._selectProject("project-b");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(panel._desktopFeatures.settings.agentsProjectId).toBe("project-b");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]').value).toBe("Project B");

    const content = panel.shadowRoot.querySelector('[data-desktop-field="agents_content"]');
    content.value = "edited B";
    content.dispatchEvent(new Event("input", { bubbles: true }));
    await panel._handleDesktopAction("save-agents", {}, panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]'));
    expect(panel._callWS).toHaveBeenCalledWith("update_agents", { content: "edited B", project_id: "project-b" });
  });

  it("discards a stale settings rejection and reloads the newly selected project", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._projects = [
      { project_id: "project-a", kind: "project", name: "Project A" },
      { project_id: "project-b", kind: "project", name: "Project B" },
    ];
    panel._activeThread = null;
    panel._selectedProjectId = "project-a";
    panel._config = { capabilities: [] };
    let rejectProjectA;
    panel._callWS = vi.fn().mockImplementation((action, payload = {}) => {
      if (action === "get_agents" && payload.project_id === "project-a") return new Promise((resolve, reject) => { rejectProjectA = reject; });
      if (action === "get_agents" && payload.project_id === "project-b") return Promise.resolve({ content: "Project B" });
      if (action === "get_agents") return Promise.resolve({ content: "global" });
      return Promise.resolve({});
    });

    const firstLoad = panel._loadDesktopDestination("settings", { force: true });
    await Promise.resolve();
    panel._selectedProjectId = "project-b";
    rejectProjectA(new Error("Project A request failed"));
    await firstLoad;
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(panel._desktopFeatures.settings.error).toBe("");
    expect(panel._desktopFeatures.settings.agentsProjectId).toBe("project-b");
    expect(panel._desktopFeatures.settings.data.agentsScopes.project.content).toBe("Project B");
  });

  it("clears a saved scope draft when the mutation succeeds but refresh fails", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.agentsProjectId = "project-a";
    panel._desktopFeatures.settings.agentsDrafts = { "project:project-a": "draft" };
    panel._desktopFeatures.settings.data = { agentsScopes: { global: {}, project: { content: "server" } } };
    panel._projects = [{ project_id: "project-a", kind: "project", name: "Project A" }];
    panel._activeThread = null;
    panel._selectedProjectId = "project-a";
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._loadDesktopDestination = vi.fn().mockRejectedValue(new Error("refresh failed"));
    panel._render(true);

    await panel._handleDesktopAction("save-agents", {}, panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]'));
    expect(panel._desktopFeatures.settings.agentsDrafts["project:project-a"]).toBeUndefined();
    expect(panel._desktopFeatures.settings.error).toMatch(/refresh failed/i);
  });

  it("clears a deleted scope draft when the mutation succeeds but refresh fails", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.agentsProjectId = "project-a";
    panel._desktopFeatures.settings.agentsDrafts = { "project:project-a": "draft" };
    panel._desktopFeatures.settings.data = { agentsScopes: { global: {}, project: { content: "server" } } };
    panel._projects = [{ project_id: "project-a", kind: "project", name: "Project A" }];
    panel._activeThread = null;
    panel._selectedProjectId = "project-a";
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._loadDesktopDestination = vi.fn().mockRejectedValue(new Error("refresh failed"));
    panel._render(true);

    await panel._handleDesktopAction("delete-agents", {}, panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]'), { confirmed: true });
    expect(panel._desktopFeatures.settings.agentsDrafts["project:project-a"]).toBeUndefined();
    expect(panel._desktopFeatures.settings.error).toMatch(/refresh failed/i);
  });

  it("preserves a scope draft when the mutation write fails", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.agentsProjectId = "project-a";
    panel._desktopFeatures.settings.agentsDrafts = { "project:project-a": "draft" };
    panel._desktopFeatures.settings.data = { agentsScopes: { global: {}, project: { content: "server" } } };
    panel._projects = [{ project_id: "project-a", kind: "project", name: "Project A" }];
    panel._activeThread = null;
    panel._selectedProjectId = "project-a";
    panel._callWS = vi.fn().mockRejectedValue(new Error("write failed"));
    panel._render(true);

    await panel._handleDesktopAction("save-agents", {}, panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]'));
    expect(panel._desktopFeatures.settings.agentsDrafts["project:project-a"]).toBe("draft");
    expect(panel._desktopFeatures.settings.error).toMatch(/write failed/i);
  });

  it("keeps the validated project instruction scope through delete confirmation", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "instructions";
    panel._desktopFeatures.settings.agentsScope = "project";
    panel._desktopFeatures.settings.data = { agentsScopes: { global: {}, project: { content: "project" } } };
    panel._projects = [{ project_id: "project-one", kind: "project", name: "Project one" }];
    panel._activeThread = { thread_id: "thread-one", project_id: "project-one", attachments: [] };
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._render(true);
    panel._loadDesktopDestination = vi.fn().mockResolvedValue(undefined);

    const scope = panel.shadowRoot.querySelector('[data-desktop-field="agents_scope"]');
    await panel._handleDesktopAction("delete-agents", {}, scope);
    expect(panel._desktopFeatures.settings.confirmAction.dataset).toMatchObject({
      agentsScope: "project",
      projectId: "project-one",
    });
    await panel._handleDesktopAction("confirm-desktop", {}, scope);
    expect(panel._callWS).toHaveBeenCalledWith("delete_agents", { project_id: "project-one" });
  });

  it("omits blank optional OAuth fields from MCP create payloads", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.settingsTab = "mcp";
    panel._desktopFeatures.settings.form = "mcp";
    panel._desktopFeatures.settings.data = { mcp_servers: [] };
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._render(true);
    panel._loadDesktopDestination = vi.fn().mockResolvedValue(undefined);

    const name = panel.shadowRoot.querySelector('[data-desktop-field="name"]');
    const url = panel.shadowRoot.querySelector('[data-desktop-field="url"]');
    name.value = "docs";
    url.value = "https://mcp.example.test";
    await panel._handleDesktopAction("submit-mcp", {}, name);

    expect(panel._callWS).toHaveBeenCalledWith("add_mcp", {
      name: "docs",
      url: "https://mcp.example.test",
    });
  });

  it("preserves scheduled form drafts across status renders, including selects and textareas", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled";
    panel._desktopFeatures.scheduled.loaded = true;
    panel._desktopFeatures.scheduled.data = { automations: [] };
    panel._callWS = vi.fn().mockResolvedValue({});
    panel._loadDesktopDestination = vi.fn().mockResolvedValue(undefined);
    panel._render(true);

    await panel._handleDesktopAction("open-schedule-form", {}, null);
    const prompt = panel.shadowRoot.querySelector('[data-desktop-field="prompt"]');
    const scheduleType = panel.shadowRoot.querySelector('[data-desktop-field="schedule_type"]');
    const mode = panel.shadowRoot.querySelector('[data-desktop-field="mode"]');
    prompt.value = "Keep this prompt";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    scheduleType.value = "interval";
    scheduleType.dispatchEvent(new Event("change", { bubbles: true }));
    mode.value = "full-auto";
    mode.dispatchEvent(new Event("change", { bubbles: true }));
    panel.shadowRoot.querySelector('[data-desktop-field="interval_seconds"]').value = "60";
    panel.shadowRoot.querySelector('[data-desktop-field="interval_seconds"]').dispatchEvent(new Event("input", { bubbles: true }));

    panel._renderDesktopSurface();
    expect(panel.shadowRoot.querySelector('[data-desktop-field="prompt"]').value).toBe("Keep this prompt");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="schedule_type"]').value).toBe("interval");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="mode"]').value).toBe("full-auto");

    const submitTarget = panel.shadowRoot.querySelector('[data-desktop-field="prompt"]');
    await panel._handleDesktopAction("submit-schedule", {}, submitTarget);
    expect(panel._callWS).toHaveBeenCalledWith("create_automation", expect.objectContaining({
      prompt: "Keep this prompt",
      schedule: expect.objectContaining({ kind: "interval", seconds: 60 }),
      mode: "full-auto",
    }));
    expect(panel._desktopFeatures.scheduled.formDraft).toEqual({});
  });

  it("preserves skill form drafts across status renders and clears them when cancelled", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "skills";
    panel._desktopFeatures.skills.loaded = true;
    panel._desktopFeatures.skills.data = { skills: [] };
    panel._render(true);

    await panel._handleDesktopAction("open-skill-form", {}, null);
    const name = panel.shadowRoot.querySelector('[data-desktop-field="name"]');
    const instructions = panel.shadowRoot.querySelector('[data-desktop-field="instructions"]');
    name.value = "Release checklist";
    name.dispatchEvent(new Event("input", { bubbles: true }));
    instructions.value = "Check the release notes.";
    instructions.dispatchEvent(new Event("input", { bubbles: true }));
    panel._renderDesktopSurface();

    expect(panel.shadowRoot.querySelector('[data-desktop-field="name"]').value).toBe("Release checklist");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="instructions"]').value).toBe("Check the release notes.");
    await panel._handleDesktopAction("close-form", {}, null);
    await panel._handleDesktopAction("open-skill-form", {}, null);
    expect(panel.shadowRoot.querySelector('[data-desktop-field="name"]').value).toBe("");
    expect(panel.shadowRoot.querySelector('[data-desktop-field="instructions"]').value).toBe("");
  });

  it("fetches the full automation before opening the edit form", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled"; panel._desktopFeatures.scheduled.loaded = true; panel._desktopFeatures.scheduled.data = { automations: [{ automation_id: "a1", revision: 2, name: "Summary" }] };
    panel._callWS = vi.fn().mockResolvedValue({ automation_id: "a1", revision: 2, name: "Full", prompt: "keep me", target: { kind: "standalone", project_id: "p1" }, schedule: { kind: "once", at: "2026-01-01T00:00:00Z" } });
    await panel._handleDesktopAction("update-automation", { id: "a1", revision: "2" }, null);
    expect(panel._callWS).toHaveBeenCalledWith("get_automation", { automation_id: "a1" });
    expect(panel._desktopFeatures.scheduled.editingAutomation.prompt).toBe("keep me");
  });

  it("keeps a schedule draft when loading the automation for edit fails", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled";
    panel._desktopFeatures.scheduled.loaded = true;
    panel._desktopFeatures.scheduled.form = "schedule";
    panel._desktopFeatures.scheduled.formDraft = { prompt: "Do not lose this" };
    panel._desktopFeatures.scheduled.data = { automations: [] };
    panel._callWS = vi.fn().mockRejectedValue(new Error("detail unavailable"));

    await panel._handleDesktopAction("update-automation", { id: "a1" }, null);

    expect(panel._desktopFeatures.scheduled.formDraft).toEqual({ prompt: "Do not lose this" });
    expect(panel._desktopFeatures.scheduled.error).toMatch(/detail unavailable/i);
  });

  it("preserves an interval anchor when saving an unchanged edit", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled";
    panel._desktopFeatures.scheduled.loaded = true;
    panel._desktopFeatures.scheduled.data = { automations: [{ automation_id: "a1", revision: 2, name: "Summary" }] };
    const automation = {
      automation_id: "a1",
      revision: 2,
      name: "Summary",
      prompt: "keep me",
      target: { kind: "standalone", project_id: "p1" },
      schedule: { kind: "interval", seconds: 60, anchor_at: "2026-01-01T00:00:00Z" },
      mode: "observe",
    };
    panel._callWS = vi.fn().mockImplementation((action) => {
      if (action === "get_automation") return Promise.resolve(automation);
      if (action === "list_automations") return Promise.resolve([]);
      return Promise.resolve({});
    });

    await panel._handleDesktopAction("update-automation", { id: "a1", revision: "2" }, null);
    const runAt = panel.shadowRoot.querySelector('[data-desktop-field="run_at"]');
    expect(runAt.value).toBe("2026-01-01T00:00:00Z");

    await panel._handleDesktopAction("submit-schedule-update", {}, runAt);
    expect(panel._callWS).toHaveBeenCalledWith("update_automation", {
      automation_id: "a1",
      expected_revision: 2,
      name: "Summary",
      prompt: "keep me",
      target: { kind: "standalone", project_id: "p1" },
      schedule: { kind: "interval", seconds: 60, anchor_at: "2026-01-01T00:00:00Z" },
      mode: "observe",
      model: null,
      thinking: null,
    });
  });

  it("refreshes a desktop surface after a mutation while the mutation is loading", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "skills";
    panel._desktopFeatures.skills = { loading: false, loaded: true, error: "", data: { skills: [] }, form: "skill", notice: "" };
    panel._renderDesktopSurface = vi.fn();
    panel._callWS = vi.fn().mockImplementation((action) => action === "create_skill" ? Promise.resolve({}) : Promise.resolve({ data: [{ cwd: ".", skills: [{ name: "Refreshed", enabled: true }] }] }));
    await panel._desktopMutation("create_skill", { name: "Refreshed" }, panel._desktopFeatures.skills);
    expect(panel._callWS).toHaveBeenNthCalledWith(1, "create_skill", { name: "Refreshed" });
    expect(panel._callWS).toHaveBeenNthCalledWith(2, "list_skills", expect.any(Object));
    expect(panel._desktopFeatures.skills.data.skills).toEqual([{ name: "Refreshed", enabled: true, scope: "." }]);
    expect(panel._desktopFeatures.skills.loading).toBe(false);
    expect(panel._desktopFeatures.skills.loaded).toBe(true);
  });

  it("refreshes the mutation's originating destination after navigation", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "scheduled";
    const state = panel._desktopFeatures.scheduled;
    let resolveMutation;
    panel._callWS = vi.fn().mockImplementation(() => new Promise((resolve) => { resolveMutation = resolve; }));
    panel._loadDesktopDestination = vi.fn().mockResolvedValue(undefined);

    const mutation = panel._desktopMutation("create_automation", {}, state);
    panel._activeDestination = "skills";
    resolveMutation({});
    await mutation;

    expect(panel._loadDesktopDestination).toHaveBeenCalledWith("scheduled", { force: true });
  });

  it("keeps mobile desktop routes reopenable and supports settings tab keyboard navigation", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "settings";
    panel._desktopFeatures.settings.loaded = true;
    panel._desktopFeatures.settings.data = { mcp_servers: [] };
    panel._render(true);
    const stylesheet = [...panel.shadowRoot.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(stylesheet).toMatch(/\.shell\.desktop-route \.main-pane > \.main-header \{ display: grid !important;/);
    expect(panel.shadowRoot.getElementById("mobile-nav-toggle")).toBeTruthy();
    const general = panel.shadowRoot.querySelector("[data-settings-tab=general]");
    general.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    await Promise.resolve();
    expect(panel._desktopFeatures.settings.settingsTab).toBe("mcp");
    expect(panel.shadowRoot.querySelector("[data-settings-tab=mcp]")).toBe(panel.shadowRoot.activeElement);
  });

  it("submits desktop forms with Enter from a single-line field", async () => {
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel);
    panel._activeDestination = "skills";
    panel._desktopFeatures.skills.loaded = true;
    panel._desktopFeatures.skills.form = "skill";
    panel._desktopFeatures.skills.data = { skills: [] };
    panel._callWS = vi.fn().mockResolvedValue([]);
    panel._render(true);
    const name = panel.shadowRoot.querySelector('[data-desktop-field="name"]');
    name.value = "Keyboard skill";
    name.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await Promise.resolve();
    expect(panel._callWS).toHaveBeenCalledWith("create_skill", expect.objectContaining({ name: "Keyboard skill" }));
  });
});
