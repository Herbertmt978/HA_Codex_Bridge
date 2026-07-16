import { describe, expect, it } from "vitest";

import { getRuntimeStripViewModel, renderRuntimeStrip } from "../src/views/runtime-strip.js";

describe("runtime strip view", () => {
  it("projects only safe App, Integration, Bridge, and Codex runtime labels", () => {
    const model = getRuntimeStripViewModel({
      api_version: 1,
      app: { connected: true, version: "2026.7.0" },
      integration: { ready: true, version: "0.6.0" },
      diagnostics: { bridge_version: "0.6.0", app_server_version: "1.2.3" },
      private_origin: "http://private.example:8766/token",
      workspace_path: "C:\\private",
    });

    expect(model.items.map((item) => item.label)).toEqual(["App", "Integration", "Bridge", "Codex"]);
    expect(model.items.map((item) => item.state)).toEqual(["ready", "ready", "ready", "ready"]);
    expect(model.healthy).toBe(true);
    expect(JSON.stringify(model)).not.toContain("private.example");
    expect(JSON.stringify(model)).not.toContain("C:\\private");
  });

  it("shows an external v0 capability-limited deprecation notice", () => {
    const model = getRuntimeStripViewModel({ api_version: 0, connection_type: "external" });

    expect(model.notice).toMatch(/older connection/u);
    expect(model.notice).toMatch(/limited/u);
  });

  it("does not render unsafe version values as markup", () => {
    const container = document.createElement("div");
    renderRuntimeStrip(container, getRuntimeStripViewModel({
      api_version: 1,
      diagnostics: { bridge_version: '<img src=x onerror="alert(1)">' },
    }));

    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).not.toContain("onerror");
  });

  it("hides healthy telemetry from chat while keeping runtime attention visible", () => {
    const healthyContainer = document.createElement("div");
    renderRuntimeStrip(healthyContainer, getRuntimeStripViewModel({
      api_version: 1,
      app: { connected: true, version: "0.8.2" },
      integration: { ready: true, version: "0.8.2" },
      diagnostics: { bridge_version: "0.7.2", app_server_version: "0.144.5" },
    }));
    expect(healthyContainer.hidden).toBe(true);

    const attentionContainer = document.createElement("div");
    renderRuntimeStrip(attentionContainer, getRuntimeStripViewModel({
      api_version: 1,
      app: { connected: true, version: "0.8.2" },
      integration: { ready: true, version: "0.8.2" },
      bridge_ready: false,
      diagnostics: { bridge_version: "0.7.2", app_server_version: "0.144.5" },
    }));
    expect(attentionContainer.hidden).toBe(false);
    expect(attentionContainer.textContent).toContain("Bridge 0.7.2");
  });
});
