import { describe, expect, it } from "vitest";

import { getOnboardingViewModel, renderOnboarding } from "../src/views/onboarding.js";

describe("onboarding view", () => {
  it("marks the HA-first checklist complete only after the first chat", () => {
    const model = getOnboardingViewModel({
      appConnected: true,
      integrationReady: true,
      bridgeReady: true,
      signedIn: true,
      workspaceReady: true,
      threadCount: 1,
    });

    expect(model.complete).toBe(true);
    expect(model.stages.map((stage) => stage.complete)).toEqual([true, true, true, true]);
  });

  it("offers a retry action when the App is disconnected", () => {
    const model = getOnboardingViewModel({ appConnected: false });

    expect(model.stages[0]).toMatchObject({ complete: false, action: "retry-app" });
  });

  it("renders only safe checklist text and no connection details", () => {
    const container = document.createElement("div");
    renderOnboarding(container, getOnboardingViewModel({
      appConnected: false,
      bridgeReady: false,
      connectionUrl: "https://private.example/token?secret=abc",
      workspacePath: "C:\\private\\workspace",
    }));

    expect(container.querySelectorAll("[data-action='retry-app']")).toHaveLength(1);
    expect(container.textContent).not.toContain("private.example");
    expect(container.textContent).not.toContain("secret=abc");
    expect(container.textContent).not.toContain("C:\\private");
  });
});
