/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";
import { HOST_MODE, HOST_INSTALLATION_URL, renderHostAccessDialog } from "../src/host-access.js";
import { buildAutomationPayload } from "../src/scheduled-tasks.js";

const status = (enabled = false) => ({
  state: "ready", enabled, grant_id: enabled ? "a".repeat(32) : null,
  warnings: [{ title: "Files and credentials", description: "Root can read, change or delete HA files, backups and credentials." }, { title: "Network", description: "Internet and local network access." }],
  disclosure: { scope_revision: "b".repeat(64), hostname: "ha-dev", os_version: "18.3", acknowledgement: "I understand the root access warning.", scheduled_acknowledgement: "I allow unattended host access." },
});

describe("explicit HAOS host access", () => {
  beforeEach(() => document.body.replaceChildren());

  it("shows installation guidance without an enable control when the companion is missing", () => {
    const dialog = renderHostAccessDialog(document, { status: { ...status(), state: "not_paired" } });
    expect(dialog.querySelector("a").href).toBe(HOST_INSTALLATION_URL);
    expect(dialog.textContent).toContain("Install the separate");
    expect(dialog.querySelector("#confirm-host-access")).toBeNull();
    expect(dialog.textContent).toContain("credentials");
  });

  it("shows the warning and enable action directly when installed, without installation instructions", () => {
    const dialog = renderHostAccessDialog(document, { status: status(), context: "new-chat" });
    expect(dialog.textContent).toContain("ha-dev");
    expect(dialog.querySelector("a")).toBeNull();
    expect(dialog.querySelector("#host-access-acknowledged").checked).toBe(false);
    expect(dialog.querySelector("#confirm-host-access").disabled).toBe(true);
    expect(dialog.querySelector("#confirm-host-access").textContent).toBe("Enable host access");
  });

  it("requires a separate unattended acknowledgement for schedules", () => {
    const state = { status: status(), context: "schedule", acknowledged: true };
    expect(renderHostAccessDialog(document, state).querySelector("#confirm-host-access").disabled).toBe(true);
    state.unattended = true;
    expect(renderHostAccessDialog(document, state).querySelector("#confirm-host-access").disabled).toBe(false);
  });

  const panelFixture = () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._config = { capabilities: ["host_access_v1"] };
    panel._callWS = vi.fn(async (method) => method === "enable_host_access" ? status(true) : status());
    return panel;
  };

  it("enables only after acknowledgement, keeps the shell inert through refreshes, and applies the grant to the chosen chat", async () => {
    const panel = panelFixture();
    panel._showThreadForm = true;
    panel._render();
    const trigger = panel.shadowRoot.getElementById("thread-mode-select");
    await panel._openHostAccess("new-chat", trigger);
    expect(panel._threadForm.mode).toBe("full-auto");
    await panel._confirmHostAccess();
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    panel._render();
    expect(panel.shadowRoot.querySelector(".shell").inert).toBe(true);
    const check = panel.shadowRoot.getElementById("host-access-acknowledged");
    check.checked = true; check.dispatchEvent(new Event("change", { bubbles: true }));
    await panel._confirmHostAccess();
    expect(panel._callWS).toHaveBeenCalledWith("enable_host_access", { scope_revision: "b".repeat(64), acknowledged: true });
    expect(panel._threadForm.mode).toBe(HOST_MODE);
    expect(panel._threadForm.hostAccessGrant).toBe("a".repeat(32));
    expect(panel.shadowRoot.querySelector(".shell").inert).toBe(false);
    expect(panel.shadowRoot.getElementById("host-access-layer").hidden).toBe(true);
  });

  it("cancels without selecting the mode or enabling anything", async () => {
    const panel = panelFixture();
    await panel._openHostAccess("new-chat");
    panel._closeHostAccess();
    expect(panel._threadForm.mode).toBe("full-auto");
    expect(panel._callWS).toHaveBeenCalledTimes(1);
    expect(panel._hostAccessDialog).toBeNull();
  });

  it("discards an earlier ready result if checking the App again fails", async () => {
    const panel = panelFixture();
    await panel._openHostAccess("new-chat");
    panel._hostAccessDialog.acknowledged = true;
    panel._callWS.mockRejectedValue(new Error("Connection lost"));
    await panel._loadHostAccess();
    expect(panel.shadowRoot.querySelector("#confirm-host-access")).toBeNull();
    expect(panel._hostAccessDialog.acknowledged).toBe(false);
    expect(panel._threadForm.mode).toBe("full-auto");
  });

  it("does not silently replace an existing grant when selecting another task", async () => {
    const panel = panelFixture();
    panel._callWS.mockResolvedValue(status(true));
    await panel._openHostAccess("new-chat");
    panel._hostAccessDialog.acknowledged = true;
    await panel._confirmHostAccess();
    expect(panel._callWS.mock.calls.map(([method]) => method)).toEqual(["host_access", "host_access"]);
    expect(panel._threadForm.hostAccessGrant).toBe("a".repeat(32));
  });

  it("rejects a changed warning without applying the selection", async () => {
    const panel = panelFixture();
    await panel._openHostAccess("new-chat");
    panel._hostAccessDialog.acknowledged = true;
    panel._callWS.mockResolvedValue({ ...status(true), disclosure: { ...status().disclosure, scope_revision: "c".repeat(64) } });
    await panel._confirmHostAccess();
    expect(panel._threadForm.mode).toBe("full-auto");
    expect(panel._hostAccessDialog.error).toContain("changed");
  });

  it("requires the schedule's own consent and forwards it without changing workspace defaults", () => {
    const values = { title: "Probe", prompt: "Read host identity", mode: HOST_MODE, repeat: "daily", date: "2026-09-21", time: "09:00" };
    const context = { projectId: "project", timezone: "UTC" };
    expect(() => buildAutomationPayload(values, context)).toThrow(/acknowledge/);
    expect(buildAutomationPayload(values, { ...context, hostAccessGrant: "a".repeat(32), hostUnattendedApproved: true })).toMatchObject({ host_access_grant: "a".repeat(32), host_unattended_approved: true });
    expect(buildAutomationPayload({ ...values, mode: "full-auto" }, context)).not.toHaveProperty("host_access_grant");
  });
});
