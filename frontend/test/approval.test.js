import { describe, expect, it } from "vitest";

import { getApprovalViewModel, renderApproval } from "../src/views/approval.js";

const NOW = Date.parse("2026-07-14T12:00:00Z");

function approval(overrides = {}) {
  return {
    interaction_id: "interaction-1",
    kind: "command_approval",
    status: "pending",
    expires_at: "2026-07-14T12:05:00Z",
    allowed_actions: ["accept", "decline", "cancel"],
    display: {
      title: "Run focused tests",
      summary: "Codex wants to run a workspace command.",
      command: "python -m pytest -q",
      workspace_paths: ["src/app.py", "tests/test_app.py"],
    },
    ...overrides,
  };
}

describe("approval view", () => {
  it("projects command scope and all allowed decisions into an actionable card", () => {
    const model = getApprovalViewModel(approval(), { now: NOW });

    expect(model).toMatchObject({ state: "ready", disabled: false, command: "python -m pytest -q" });
    expect(model.scope).toEqual(["src/app.py", "tests/test_app.py"]);
    expect(model.actions.map((action) => action.id)).toEqual(["accept", "decline", "cancel"]);
    expect(model.expiry).toContain("2026-07-14T12:05:00Z");
  });

  it("uses text-only rendering for hostile command and patch scope data", () => {
    const container = document.createElement("div");
    const model = getApprovalViewModel(approval({
      kind: "file_change_approval",
      display: {
        title: '<img src=x onerror="window.__approvalXss=1">',
        summary: "<script>window.__approvalXss=2</script>",
        command: '</pre><svg onload="window.__approvalXss=3">',
        workspace_paths: ["src/<img>.py", "../private", "C:\\outside\\secret"],
      },
    }), { now: NOW });
    renderApproval(container, model);

    expect(container.querySelectorAll("img, script, svg, [onerror], [onload]")).toHaveLength(0);
    expect(container.textContent).toContain("<script>");
    expect(container.textContent).toContain("src/<img>.py");
    expect(container.textContent).not.toContain("../private");
    expect(container.textContent).not.toContain("outside\\secret");
  });

  it.each([
    [{ pending: true }, "submitting"],
    [{ stale: true }, "stale"],
    [{ now: Date.parse("2026-07-14T12:06:00Z") }, "expired"],
  ])("immediately disables every decision while %s", (state, expected) => {
    const model = getApprovalViewModel(approval(), { now: NOW, ...state });
    const container = document.createElement("div");
    renderApproval(container, model);

    expect(model.state).toBe(expected);
    expect(container.querySelectorAll("button:disabled")).toHaveLength(3);
    expect(container.querySelector("[role='status']")?.getAttribute("aria-live")).toBe("polite");
  });

  it("fails closed when a request is no longer pending or lacks an expiry", () => {
    const completed = getApprovalViewModel(approval({ status: "accepted" }), { now: NOW });
    const missingExpiry = getApprovalViewModel(approval({ expires_at: null }), { now: NOW });

    expect(completed.disabled).toBe(true);
    expect(missingExpiry.disabled).toBe(true);
    expect(missingExpiry.expiry).toBe("Expiry unavailable");
  });

  it("renders accessible decision controls only for server-authorized actions", () => {
    const container = document.createElement("div");
    renderApproval(container, getApprovalViewModel(approval({ allowed_actions: ["decline"] }), { now: NOW }));

    const controls = container.querySelectorAll("button");
    expect(controls).toHaveLength(1);
    expect(controls[0].dataset.action).toBe("decline-interaction");
    expect(controls[0].getAttribute("aria-disabled")).toBe("false");
  });
});
