import { describe, expect, it } from "vitest";

import {
  ABOUT_SCREENS,
  INFO_TABS,
  KEYBOARD_SHORTCUTS,
  getInfoCenterViewModel,
} from "../src/info-center.js";

describe("information center view model", () => {
  it("projects allowlisted operational information into all four tabs", () => {
    const model = getInfoCenterViewModel({
      panelVersion: "0.7.1",
      status: {
        api_version: 1,
        bridge_ready: true,
        codex_ready: true,
        auth: { state: "ok", auth_required: false },
        account: { auth_mode: "chatgpt", plan_type: "plus" },
        limits: {
          available: true,
          primary: { remaining_percent: 72.4 },
          secondary: { remaining_percent: 41 },
        },
        app: { connected: true, version: "0.7.1" },
        integration: { ready: true, version: "0.7.1" },
        diagnostics: { bridge_version: "0.6.0", app_server_version: "0.144.4" },
      },
      thread: { thread_id: "thread_1", status: "running", mode: "edit" },
      project: { kind: "project" },
      artifacts: [{ size_bytes: 1024 }, { size_bytes: 512 }],
    });

    expect(model.tabs).toBe(INFO_TABS);
    expect(Object.keys(model)).toEqual(["tabs", "activity", "files", "usage", "system"]);
    expect(model.activity).toMatchObject({
      title: "Current activity",
      summary: "This chat is working.",
      rows: expect.arrayContaining([{ label: "Permission", value: "Edit" }]),
    });
    expect(model.files.rows).toEqual(expect.arrayContaining([
      { label: "Available files", value: "2" },
      { label: "Known size", value: "1.5 KB" },
    ]));
    expect(model.usage.rows).toEqual(expect.arrayContaining([
      { label: "Plan", value: "Plus" },
      { label: "5-hour limit", value: "72% remaining" },
    ]));
    expect(model.system.summary).toBe("The private runtime is ready.");
  });

  it("never reflects secrets, paths, URLs, commands, diffs, or unknown nested status fields", () => {
    const forbidden = [
      "token=super-secret",
      "C:\\Users\\Ashby\\private",
      "https://private.example/token",
      "rm -rf /config",
      "+++ private-diff",
      "customer@example.com",
    ];
    const model = getInfoCenterViewModel({
      panelVersion: "https://private.example/token",
      status: {
        api_version: 1,
        bridge_ready: true,
        auth: { state: "ok", auth_required: false, user_code: forbidden[0] },
        account: {
          auth_mode: "chatgpt",
          plan_type: "plus",
          email: forbidden[5],
          access_token: forbidden[0],
        },
        diagnostics: {
          bridge_version: "0.6.0",
          raw_error: forbidden[1],
          command: forbidden[3],
        },
        private_origin: forbidden[2],
        unknown: { diff: forbidden[4] },
      },
      thread: { thread_id: "thread_1", title: forbidden[0], workspace_path: forbidden[1], command: forbidden[3] },
      project: { kind: "project", name: forbidden[5], root_path: forbidden[1] },
      artifacts: [{ filename: forbidden[2], relative_path: forbidden[1], size_bytes: 1, diff: forbidden[4] }],
    });

    const output = JSON.stringify(model);
    for (const value of forbidden) {
      expect(output).not.toContain(value);
    }
    expect(model.system.rows.find((row) => row.label === "Panel")?.value).toBe("Unavailable");
  });

  it("bounds artifact processing and falls back safely for malformed values", () => {
    const artifacts = Array.from({ length: 2000 }, () => ({ size_bytes: Number.MAX_SAFE_INTEGER }));
    const model = getInfoCenterViewModel({
      status: { limits: { available: true, primary: { remaining_percent: 101 } } },
      thread: { status: "arbitrary status", mode: "dangerous" },
      project: { kind: "unknown" },
      artifacts,
      panelVersion: "x".repeat(80),
    });

    expect(model.files.rows.find((row) => row.label === "Available files")?.value).toBe("1000");
    expect(model.files.rows.find((row) => row.label === "Known size")?.value).toBe("1.0 PB");
    expect(model.activity.rows).toEqual(expect.arrayContaining([
      { label: "Run", value: "No active run" },
      { label: "Permission", value: "Unavailable" },
    ]));
    expect(model.usage.rows.find((row) => row.label === "5-hour limit")?.value).toBe("Unavailable");
    expect(JSON.stringify(model).length).toBeLessThan(5000);
  });

  it("exports concise static help for operating, securing, and navigating the panel", () => {
    expect(INFO_TABS.map((tab) => tab.id)).toEqual(["activity", "files", "usage", "system"]);
    expect(KEYBOARD_SHORTCUTS).toHaveLength(4);
    expect(ABOUT_SCREENS.map((screen) => screen.id)).toEqual([
      "how-it-works",
      "privacy-security",
      "keyboard-shortcuts",
      "about-version",
    ]);
    expect(JSON.stringify(ABOUT_SCREENS)).not.toMatch(/token|https?:\/\//i);
  });
});
