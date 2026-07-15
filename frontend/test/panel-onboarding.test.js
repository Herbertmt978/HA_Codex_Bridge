/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function status(overrides = {}) {
  return {
    account: { available: false, auth_mode: null, plan_type: null },
    auth: { state: "logged_out", auth_required: true },
    diagnostics: {
      app_version: "0.6.0",
      bridge_version: "0.6.0",
      active_codex_version: "1.2.3",
    },
    model_catalog: { models: [], default_model: "gpt-5.6-sol", default_thinking_level: "medium" },
    limits: { available: false },
    ...overrides,
  };
}

function createPanel() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  return panel;
}

describe("HA-first panel integration", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("establishes the zero-chat auth/runtime subscription before loading chats", async () => {
    const order = [];
    let subscriptionPayload;
    const panel = createPanel();
    panel._hass = {
      connection: {
        subscribeMessage: vi.fn(async (_callback, payload) => {
          order.push("subscribe");
          subscriptionPayload = payload;
          return vi.fn();
        }),
        sendMessagePromise: vi.fn(async ({ type }) => {
          const action = type.replace("codex_bridge/", "");
          order.push(action);
          if (action === "get_config") return { panel_title: "Codex", connection_type: "supervisor", api_version: 1 };
          if (action === "get_status") return status();
          return [];
        }),
      },
    };

    await panel._bootstrap();

    expect(subscriptionPayload).toEqual({
      type: "codex_bridge/subscribe_events",
      after: 0,
      scopes: ["auth", "runtime"],
    });
    expect(order.indexOf("subscribe")).toBeGreaterThan(order.indexOf("get_config"));
    expect(order.indexOf("subscribe")).toBeLessThan(order.indexOf("list_projects"));
    expect(order.indexOf("subscribe")).toBeLessThan(order.indexOf("list_threads"));
  });

  it("starts sign-in idempotently and keeps cancel and sign-out explicit", async () => {
    const calls = [];
    const panel = createPanel();
    panel._hass = {
      connection: {
        sendMessagePromise: vi.fn(async (payload) => {
          calls.push(payload);
          return { state: payload.type.endsWith("logout_auth") ? "logged_out" : "login_starting" };
        }),
      },
    };

    await panel._startAuthLogin();
    await panel._cancelAuthLogin();
    panel._confirmSignOut = true;
    await panel._logoutAuth();

    expect(calls).toEqual([
      { type: "codex_bridge/start_auth_login" },
      { type: "codex_bridge/cancel_auth_login" },
      { type: "codex_bridge/logout_auth" },
    ]);
  });

  it("copies only the device code and opens only approved HTTPS sign-in hosts", async () => {
    const panel = createPanel();
    const copy = vi.spyOn(panel, "_writeClipboardText").mockResolvedValue();
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    panel._status = {
      auth: {
        state: "login_running",
        user_code: "ABCD-EFGH",
        verification_uri: "https://auth.openai.com/codex/device",
      },
    };

    await panel._copyAuthCode();
    panel._openChatGptSignIn();
    panel._status.auth.verification_uri = "https://evil.example/collect?token=secret";
    panel._openChatGptSignIn();

    expect(copy).toHaveBeenCalledWith("ABCD-EFGH");
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(
      "https://auth.openai.com/codex/device",
      "_blank",
      "noopener,noreferrer"
    );
  });

  it.each([
    "https://auth.openai.com/",
    "https://auth.openai.com:8443/codex/device",
    "https://auth.openai.com/codex/device?token=secret",
    "https://user:secret@auth.openai.com/codex/device",
  ])("rejects an unsafe ChatGPT verification URL: %s", (verificationUri) => {
    const panel = createPanel();
    panel._status = {
      auth: {
        state: "login_running",
        user_code: "ABCD-EFGH",
        verification_uri: verificationUri,
      },
    };

    expect(panel._safeAuthVerificationUrl()).toBeNull();
  });

  it("keeps the newest auth revision and clears a stale device code", async () => {
    const panel = createPanel();
    panel._callWS = vi.fn(async (action) => {
      if (action === "get_auth_status") {
        return { revision: 6, state: "signed_out", auth_required: true };
      }
      return status({
        auth: {
          revision: 5,
          state: "login_running",
          auth_required: true,
          user_code: "STALE-CODE",
          verification_uri: "https://auth.openai.com/codex/device",
        },
      });
    });

    await panel._refreshAuthStatus();

    expect(panel._status.auth).toMatchObject({ revision: 6, state: "signed_out", user_code: null });
    expect(panel._status.auth.verification_uri).toBeNull();
  });

  it("uses the status snapshot when it carries a newer auth revision", async () => {
    const panel = createPanel();
    panel._callWS = vi.fn(async (action) => {
      if (action === "get_auth_status") {
        return { revision: 6, state: "signed_out", auth_required: true };
      }
      return status({ auth: { revision: 7, state: "ok", auth_required: false } });
    });

    await panel._refreshAuthStatus();

    expect(panel._status.auth).toMatchObject({ revision: 7, state: "ok", auth_required: false });
  });

  it("polls a pending device sign-in until the account becomes ready", async () => {
    vi.useFakeTimers();
    try {
      const panel = createPanel();
      panel._config = { connection_type: "supervisor", api_version: 1 };
      panel._hass = {};
      panel._callWS = vi.fn(async (action) => {
        if (action === "get_auth_status") {
          return { revision: 3, state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" };
        }
        if (action === "get_status") {
          return status({ auth: { revision: 3, state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" } });
        }
        throw new Error(`Unexpected action: ${action}`);
      });

      panel._applyAuthStatus({
        revision: 2,
        state: "login_running",
        auth_required: true,
        user_code: "ABCD-EFGH",
      });
      await vi.advanceTimersByTimeAsync(2500);

      expect(panel._callWS).toHaveBeenCalledWith("get_auth_status");
      expect(panel._callWS).toHaveBeenCalledWith("get_status");
      expect(panel._status.auth).toMatchObject({ state: "ok", auth_mode: "chatgpt", plan_type: "pro" });
      expect(panel._authPollTimer).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a dismissed pending-login banner dismissed during silent polling", async () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._status = status({
      auth: { revision: 2, state: "login_running", auth_required: true, user_code: "ABCD-EFGH" },
    });
    panel._dismissedBannerKey = "dismissed-login-banner";
    panel._callWS = vi.fn(async (action) => {
      if (action === "get_auth_status") return panel._status.auth;
      if (action === "get_status") return panel._status;
      throw new Error(`Unexpected action: ${action}`);
    });

    await panel._refreshAuthStatus({ silent: true });

    expect(panel._dismissedBannerKey).toBe("dismissed-login-banner");
  });

  it("restarts pending-login polling after reconnecting during an old request", async () => {
    vi.useFakeTimers();
    try {
      const panel = createPanel();
      panel._config = { connection_type: "supervisor", api_version: 1 };
      panel._hass = {};
      panel._status = status({
        auth: { revision: 2, state: "login_running", auth_required: true, user_code: "ABCD-EFGH" },
      });
      let resolveOldRequest;
      const oldRequest = new Promise((resolve) => {
        resolveOldRequest = resolve;
      });
      panel._refreshAuthStatus = vi.fn()
        .mockReturnValueOnce(oldRequest)
        .mockResolvedValue(undefined);

      panel._syncAuthPolling();
      await vi.advanceTimersByTimeAsync(2500);
      expect(panel._authPollInFlight).toBe(true);

      panel.remove();
      document.body.append(panel);
      await vi.advanceTimersByTimeAsync(2500);

      expect(panel._refreshAuthStatus).toHaveBeenCalledTimes(2);
      resolveOldRequest();
      await Promise.resolve();
    } finally {
      vi.useRealTimers();
    }
  });

  it("discards an obsolete auth poll response after reconnecting", async () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._hass = {};
    panel._status = status({
      auth: { revision: 2, state: "login_running", auth_required: true },
      model_catalog: {
        default_model: "gpt-5.5",
        default_thinking_level: "medium",
        models: [{ model: "gpt-5.5", thinking_levels: ["medium"] }],
      },
    });
    let resolveAuth;
    let resolveStatus;
    const authResponse = new Promise((resolve) => {
      resolveAuth = resolve;
    });
    const statusResponse = new Promise((resolve) => {
      resolveStatus = resolve;
    });
    panel._callWS = vi.fn((action) => (
      action === "get_auth_status" ? authResponse : statusResponse
    ));
    const stalePoll = panel._refreshAuthStatus({
      silent: true,
      pollGeneration: panel._authPollGeneration,
    });

    panel._authPollGeneration += 1;
    panel._status = status({
      auth: { revision: 3, state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" },
      model_catalog: {
        default_model: "gpt-5.6-sol",
        default_thinking_level: "max",
        models: [{ model: "gpt-5.6-sol", thinking_levels: ["max", "ultra"] }],
      },
    });
    resolveAuth({ revision: 2, state: "login_running", auth_required: true });
    resolveStatus(status({
      auth: { revision: 2, state: "login_running", auth_required: true },
      model_catalog: {
        default_model: "gpt-5.5",
        default_thinking_level: "medium",
        models: [{ model: "gpt-5.5", thinking_levels: ["medium"] }],
      },
    }));
    await stalePoll;

    expect(panel._status.auth).toMatchObject({ revision: 3, state: "ok", auth_required: false });
    expect(panel._status.model_catalog.default_model).toBe("gpt-5.6-sol");
  });

  it("allows only one account mutation at a time", async () => {
    let resolveLogin;
    const login = new Promise((resolve) => {
      resolveLogin = resolve;
    });
    const panel = createPanel();
    panel._callWS = vi.fn(() => login);

    const first = panel._startAuthLogin();
    const duplicate = panel._startAuthLogin();
    expect(panel._callWS).toHaveBeenCalledOnce();
    expect(panel._authActionPending).toBe(true);

    resolveLogin({ revision: 2, state: "login_starting", auth_required: true });
    await Promise.all([first, duplicate]);

    expect(panel._authActionPending).toBe(false);
  });

  it("does not let a late account action response replace a newer event revision", async () => {
    let resolveLogin;
    const panel = createPanel();
    panel._callWS = vi.fn(() => new Promise((resolve) => {
      resolveLogin = resolve;
    }));

    const login = panel._startAuthLogin();
    panel._applyAuthStatus({ revision: 6, state: "signed_out", auth_required: true });
    resolveLogin({
      revision: 5,
      state: "login_running",
      auth_required: true,
      user_code: "STALE-CODE",
      verification_uri: "https://auth.openai.com/codex/device",
    });
    await login;

    expect(panel._status.auth).toMatchObject({ revision: 6, state: "signed_out", user_code: null });
    expect(panel._status.auth.verification_uri).toBeNull();
  });

  it("does not let an in-flight status refresh replace a newer local auth revision", async () => {
    let resolveAuth;
    let resolveStatus;
    const panel = createPanel();
    panel._callWS = vi.fn((action) => new Promise((resolve) => {
      if (action === "get_auth_status") resolveAuth = resolve;
      else resolveStatus = resolve;
    }));

    const refresh = panel._refreshAuthStatus();
    panel._applyAuthStatus({ revision: 9, state: "signed_out", auth_required: true });
    resolveAuth({
      revision: 8,
      state: "login_running",
      auth_required: true,
      user_code: "STALE-CODE",
    });
    resolveStatus(status({
      auth: {
        revision: 7,
        state: "login_running",
        auth_required: true,
        user_code: "OLDER-CODE",
      },
    }));
    await refresh;

    expect(panel._status.auth).toMatchObject({ revision: 9, state: "signed_out", user_code: null });
  });

  it("refreshes account state from auth/runtime events without a selected chat", () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._systemEventCursor = 4;
    const refresh = vi.spyOn(panel, "_scheduleSystemRefresh").mockImplementation(() => {});

    panel._handleSystemEvent({
      type: "event",
      event: {
        cursor: 5,
        scope: "auth",
        event_type: "auth.status_changed",
        payload: { state: "login_running" },
      },
    });
    panel._handleSystemEvent({
      type: "event",
      event: {
        cursor: 5,
        scope: "auth",
        event_type: "auth.status_changed",
        payload: { state: "login_running" },
      },
    });

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(panel._systemEventCursor).toBe(5);
  });

  it("renders safe setup/account/runtime surfaces without private connection data", () => {
    const panel = createPanel();
    panel._config = {
      panel_title: "Codex",
      connection_type: "supervisor",
      api_version: 1,
      bridge_url: "http://private.example:8766/?token=secret",
    };
    panel._status = status({
      auth: {
        state: "login_running",
        auth_required: true,
        auth_mode: "chatgpt",
        user_code: "CODE-1234",
        verification_uri: "https://auth.openai.com/codex/device?token=secret",
      },
      diagnostics: {
        app_version: "0.6.0",
        bridge_version: "0.6.0",
        active_codex_version: "1.2.3",
        python_executable: "C:\\private\\python.exe",
        git_branch: "private-branch",
      },
    });
    panel._projects = [{
      project_id: "prj_direct",
      kind: "direct",
      name: "Direct chats",
      root_path: "C:\\private\\workspace",
      default_model: "gpt-5.6-sol",
      default_thinking_level: "medium",
    }];

    panel._render(true);

    const text = panel.shadowRoot.textContent;
    expect(panel.shadowRoot.getElementById("onboarding")).not.toBeNull();
    expect(panel.shadowRoot.getElementById("auth-panel")).not.toBeNull();
    expect(panel.shadowRoot.getElementById("runtime-strip")).not.toBeNull();
    expect(text).toContain("ChatGPT sign-in");
    expect(text).toContain("CODE-1234");
    expect(text).not.toContain("private.example");
    expect(text).not.toContain("token=secret");
    expect(text).not.toContain("C:\\private");
    expect(text).not.toContain("private-branch");
    expect(text).not.toMatch(/\bVM\b/u);
  });

  it("limits account and workspace controls on an older external connection", () => {
    const panel = createPanel();
    panel._config = { panel_title: "Codex", connection_type: "external", api_version: 0 };
    panel._status = status();
    panel._projects = [{
      project_id: "prj_private",
      kind: "project",
      name: "Private project",
      root_path: "C:\\private\\workspace",
      default_model: "gpt-5.6-sol",
      default_thinking_level: "medium",
    }];
    panel._selectedProjectId = "prj_private";

    panel._render(true);
    panel._openProjectFormForEdit("prj_private");

    expect(panel.shadowRoot.getElementById("auth-panel").textContent).toContain("Home Assistant App");
    expect(panel.shadowRoot.getElementById("auth-panel").querySelector("button")).toBeNull();
    expect(panel.shadowRoot.querySelector("[data-action='edit-project']")).toBeNull();
    expect(panel._showProjectForm).toBe(false);
    expect(panel.shadowRoot.textContent).not.toContain("C:\\private");
  });

  it("does not seed a supervisor project form with a migrated absolute path", () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._status = status();
    panel._projects = [{
      project_id: "prj_migrated",
      kind: "project",
      name: "Migrated project",
      root_path: "/srv/private/workspace",
      default_model: "gpt-5.6-sol",
      default_thinking_level: "medium",
    }];

    panel._openProjectFormForEdit("prj_migrated");

    expect(panel.shadowRoot.getElementById("project-root-input").value).toBe("");
    expect(panel.shadowRoot.textContent).not.toContain("/srv/private");
  });

  it("retries the global auth/runtime stream after a transient subscription failure", async () => {
    vi.useFakeTimers();
    try {
      const panel = createPanel();
      const subscribeMessage = vi
        .fn()
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValueOnce(vi.fn());
      panel._config = { connection_type: "supervisor", api_version: 1 };
      panel._hass = { connection: { subscribeMessage } };

      await panel._startSystemEventSubscription();
      expect(subscribeMessage).toHaveBeenCalledOnce();

      await vi.advanceTimersByTimeAsync(500);

      expect(subscribeMessage).toHaveBeenCalledTimes(2);
      expect(panel._systemEventSubscriptionActive).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores a late thread subscription after the selected chat changes", async () => {
    let resolveFirst;
    const firstSubscription = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    const callbacks = [];
    const firstUnsubscribe = vi.fn();
    const panel = createPanel();
    panel._hass = {
      connection: {
        subscribeMessage: vi.fn((callback) => {
          callbacks.push(callback);
          return callbacks.length === 1 ? firstSubscription : Promise.resolve(vi.fn());
        }),
      },
    };
    panel._selectedThreadId = "thr_first";
    panel._startEventSubscription();
    panel._selectedThreadId = "thr_second";
    panel._startEventSubscription();

    resolveFirst(firstUnsubscribe);
    await Promise.resolve();
    await Promise.resolve();
    callbacks[0]({ sequence: 99, thread_id: "thr_first" });

    expect(firstUnsubscribe).toHaveBeenCalledOnce();
    expect(panel._sequence).toBe(0);
  });

  it("redacts private paths, addresses, credentials, and accounts from UI errors", () => {
    const panel = createPanel();

    panel._setError(
      new Error(
        "Failed at C:\\private\\workspace\\file.txt and /srv/bridge/private.json via https://private.example:8766/path token=secret owner@example.com"
      )
    );

    expect(panel._error).toContain("[private path]");
    expect(panel._error).toContain("[private address]");
    expect(panel._error).toContain("[private credential]");
    expect(panel._error).toContain("[private account]");
    expect(panel._error).not.toContain("private.example");
    expect(panel._error).not.toContain("C:\\private");
    expect(panel._error).not.toContain("/srv/bridge");
    expect(panel._error).not.toContain("owner@example.com");
  });

  it("gives every dynamic project, chat, model, and thinking control an accessible name", () => {
    const panel = createPanel();
    const project = {
      project_id: "prj_accessible",
      kind: "project",
      name: "Accessible project",
      root_path: "team/accessible",
      default_model: "gpt-5.6-sol",
      default_thinking_level: "medium",
    };
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._status = status();
    panel._projects = [project];
    panel._selectedProjectId = project.project_id;
    panel._activeThread = {
      thread_id: "thr_accessible",
      project_id: project.project_id,
      title: "Accessible chat",
      status: "idle",
      attachments: [],
      effective_model: "gpt-5.6-sol",
      effective_thinking_level: "medium",
    };

    panel._openProjectFormForEdit(project.project_id);
    for (const id of [
      "project-name-input",
      "project-root-input",
      "project-model-select",
      "project-thinking-select",
      "folder-name-input",
      "thread-model-select",
      "thread-thinking-select",
    ]) {
      expect(panel.shadowRoot.getElementById(id)?.getAttribute("aria-label"), id).toBeTruthy();
    }

    panel._openThreadFormForProject(project.project_id);
    for (const id of ["thread-title-input", "thread-mode-select"]) {
      expect(panel.shadowRoot.getElementById(id)?.getAttribute("aria-label"), id).toBeTruthy();
    }
  });

  it("renders a disabled short window separately from a full weekly allowance", () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._status = status({
      limits: {
        available: true,
        plan_type: "pro",
        primary: null,
        secondary: { remaining_percent: 100, window_minutes: 10080, resets_at: null },
      },
      model_catalog: {
        default_model: "gpt-5.6-sol",
        default_thinking_level: "max",
        models: [
          { model: "gpt-5.6-sol", display_name: "GPT-5.6 Sol", thinking_levels: ["medium", "max", "ultra"] },
          { model: "gpt-5.6-terra", display_name: "GPT-5.6 Terra", thinking_levels: ["high", "max"] },
        ],
      },
    });
    panel._projects = [{
      project_id: "prj_direct",
      kind: "direct",
      name: "Direct chats",
      root_path: ".",
      default_model: "gpt-5.6-sol",
      default_thinking_level: "max",
    }];
    panel._selectedProjectId = "prj_direct";
    panel._activeThread = {
      thread_id: "thr_limits",
      project_id: "prj_direct",
      title: "Limits",
      status: "idle",
      attachments: [],
      effective_model: "gpt-5.6-sol",
      effective_thinking_level: "max",
    };

    panel._renderToolbar();

    const limitCards = [...panel.shadowRoot.querySelectorAll(".mini-limit")];
    expect(limitCards[0].textContent).toContain("5h");
    expect(limitCards[0].textContent).toContain("Off");
    expect(limitCards[1].textContent).toContain("Week");
    expect(limitCards[1].textContent).toContain("100%");
    expect([...panel.shadowRoot.getElementById("thread-model-select").options].map((option) => option.value)).toContain("gpt-5.6-terra");
    expect([...panel.shadowRoot.getElementById("thread-thinking-select").options].map((option) => option.value)).toEqual(
      expect.arrayContaining(["max", "ultra"])
    );
  });

  it("applies a refreshed model catalogue after a focused picker is closed", async () => {
    vi.useFakeTimers();
    try {
      const panel = createPanel();
      panel._config = { connection_type: "supervisor", api_version: 1 };
      panel._status = status({
        model_catalog: {
          default_model: "gpt-5.5",
          default_thinking_level: "medium",
          models: [{ model: "gpt-5.5", display_name: "GPT-5.5", thinking_levels: ["medium", "xhigh"] }],
        },
      });
      panel._projects = [{
        project_id: "prj_direct",
        kind: "direct",
        name: "Direct chats",
        root_path: ".",
        default_model: "gpt-5.5",
        default_thinking_level: "medium",
      }];
      panel._selectedProjectId = "prj_direct";
      panel._activeThread = {
        thread_id: "thr_focus",
        project_id: "prj_direct",
        title: "Focused picker",
        status: "idle",
        attachments: [],
        effective_model: "gpt-5.5",
        effective_thinking_level: "medium",
      };
      panel._render(true);
      const originalSelect = panel.shadowRoot.getElementById("thread-model-select");
      originalSelect.focus();

      panel._status = status({
        model_catalog: {
          default_model: "gpt-5.6-sol",
          default_thinking_level: "max",
          models: [{ model: "gpt-5.6-sol", display_name: "GPT-5.6 Sol", thinking_levels: ["max", "ultra"] }],
        },
      });
      panel._activeThread = {
        ...panel._activeThread,
        effective_model: "gpt-5.6-sol",
        effective_thinking_level: "max",
      };
      panel._render();
      expect([...originalSelect.options].map((option) => option.value)).not.toContain("gpt-5.6-sol");

      originalSelect.blur();
      await vi.runAllTimersAsync();

      expect([...panel.shadowRoot.getElementById("thread-model-select").options].map((option) => option.value)).toContain("gpt-5.6-sol");
      expect([...panel.shadowRoot.getElementById("thread-thinking-select").options].map((option) => option.value)).toEqual(
        expect.arrayContaining(["max", "ultra"])
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes thinking choices immediately when a focused model picker changes model", async () => {
    const panel = createPanel();
    panel._config = { connection_type: "supervisor", api_version: 1 };
    panel._status = status({
      model_catalog: {
        default_model: "model-a",
        default_thinking_level: "medium",
        models: [
          { model: "model-a", display_name: "Model A", thinking_levels: ["medium"] },
          { model: "model-b", display_name: "Model B", thinking_levels: ["max", "ultra"] },
        ],
      },
    });
    panel._projects = [{
      project_id: "prj_direct",
      kind: "direct",
      name: "Direct chats",
      root_path: ".",
      default_model: "model-a",
      default_thinking_level: "medium",
    }];
    panel._selectedProjectId = "prj_direct";
    panel._selectedThreadId = "thr_model_change";
    panel._activeThread = {
      thread_id: "thr_model_change",
      project_id: "prj_direct",
      title: "Model change",
      status: "idle",
      attachments: [],
      model_override: "model-a",
      thinking_override: "medium",
      effective_model: "model-a",
      effective_thinking_level: "medium",
    };
    panel._callWS = vi.fn(async () => ({
      ...panel._activeThread,
      model_override: "model-b",
      thinking_override: "max",
      effective_model: "model-b",
      effective_thinking_level: "max",
    }));
    panel._render(true);
    const modelSelect = panel.shadowRoot.getElementById("thread-model-select");
    modelSelect.focus();
    modelSelect.value = "model-b";

    modelSelect.dispatchEvent(new Event("change", { bubbles: true }));

    const thinkingValues = [...panel.shadowRoot.getElementById("thread-thinking-select").options]
      .map((option) => option.value);
    expect(thinkingValues).toEqual(["", "max", "ultra"]);
    expect(thinkingValues).not.toContain("medium");
    expect(panel.shadowRoot.getElementById("thread-thinking-select").value).toBe("max");
    expect(panel._callWS).toHaveBeenCalledWith("update_thread", {
      thread_id: "thr_model_change",
      model_override: "model-b",
      thinking_override: "max",
    });
  });
});
