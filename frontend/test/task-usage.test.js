/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { durationLimit, renderUsageHistory, usageLabel } from "../src/task-usage.js";
import "../src/codex-bridge-panel.js";

describe("reported task usage", () => {
  it("distinguishes missing usage, verified zero and partial observations", () => {
    expect(usageLabel({ reported_tokens: null })).toBe("Usage not reported");
    expect(usageLabel({ reported_tokens: 0, coverage: "reported" })).toBe("0 reported tokens");
    expect(usageLabel({ reported_tokens: 10, coverage: "partial" })).toContain("partial coverage");
    for (const count of [-1, "50", Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      expect(usageLabel({ reported_tokens: count })).toBe("Usage not reported");
    }
  });

  it("renders account labels and unconfirmed stops as safe text with accessible navigation", () => {
    const container = document.createElement("div");
    const open = vi.fn();
    renderUsageHistory(container, { items: [{
      thread_id: "chat-one", started_at: "2026-09-27T10:00:00Z", account_label: "<img src=x onerror=evil()>",
      reported_tokens: null, budget_stop_state: "unconfirmed", duration_seconds: null,
    }] }, { openChat: open });
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=evil()>");
    expect(container.textContent).toContain("Stop unconfirmed");
    expect(container.textContent).toContain("Token limits are unavailable");
    expect(container.textContent).toContain("UTC");
    const button = container.querySelector("button");
    expect(button.getAttribute("aria-label")).toContain("Open chat");
    button.click();
    expect(open).toHaveBeenCalledWith("chat-one");
    expect(container.querySelectorAll('th[scope="col"]')).toHaveLength(5);
  });

  it("has truthful empty history and validates opt-in duration values", () => {
    const container = document.createElement("div");
    renderUsageHistory(container, { items: [] });
    expect(container.textContent).toContain("No reported run history");
    for (const value of ["", "0", "-1", "1.5", "unavailable", "86401"]) expect(durationLimit(value)).toBeNull();
    expect(durationLimit("60")).toBe(60);
  });
});

describe("usage panel", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  function panel() {
    const element = document.createElement("codex-bridge-panel");
    document.body.append(element);
    element._selectedThreadId = "one";
    element._activeThread = { thread_id: "one", project_id: "project-one", status: "idle", attachments: [] };
    element._config = { capabilities: ["usage_history_v1", "elapsed_time_limit_v1"] };
    return element;
  }

  it("loads selected project history through HA and preserves loading/error retry states", async () => {
    const element = panel();
    element.shadowRoot.getElementById("task-usage-scope").value = "project";
    element._callWS = vi.fn().mockResolvedValue({ items: [], coverage: "not_reported", max_duration_seconds: 300 });
    await element._loadTaskUsage();
    expect(element._callWS).toHaveBeenCalledWith("usage_history", { project_id: "project-one" });
    expect(element.shadowRoot.getElementById("task-usage").hidden).toBe(false);
    expect(element.shadowRoot.querySelector('#elapsed-time-limit option[value="3600"]').disabled).toBe(true);
    element._callWS.mockRejectedValue(new Error("private details"));
    await element._loadTaskUsage();
    expect(element.shadowRoot.getElementById("task-usage-status").textContent).toContain("Refresh history to retry");
    expect(element.shadowRoot.textContent).not.toContain("private details");
    element.remove();
  });

  it("does not render stale history after selecting another chat", async () => {
    const element = panel();
    let resolve;
    element._callWS = vi.fn().mockImplementation(() => new Promise((done) => { resolve = done; }));
    const loading = element._loadTaskUsage();
    element._selectedThreadId = "two";
    resolve({ items: [{ account_label: "Old account" }] });
    await loading;
    expect(element.shadowRoot.getElementById("task-usage-results").textContent).not.toContain("Old account");
    element.remove();
  });

  function pendingDurationPrompt(initialChoice) {
    const element = panel();
    element._render = vi.fn();
    element._refreshActiveThread = vi.fn(async () => {});
    element._configureDraftRecovery = vi.fn();
    element._hass = { user: { id: "owner-a" } };
    element._loadPreferences();
    element._setDurationChoice("one", initialChoice);
    element.shadowRoot.getElementById("prompt-input").value = "A reviewed bounded turn";
    let resolve;
    element._callWS = vi.fn().mockImplementation(() => new Promise((done) => { resolve = done; }));
    const sending = element._sendPrompt();
    const mutation = element._promptMutationForThread("one");
    return { element, mutation, sending, resolve };
  }

  it.each(["direct", "event"])("consumes only the unchanged reviewed duration choice on %s acknowledgement", async (acknowledgement) => {
    const { element, mutation, sending, resolve } = pendingDurationPrompt(60);
    expect(element._callWS).toHaveBeenCalledWith("send_prompt", expect.objectContaining({ max_duration_seconds: 60 }));
    if (acknowledgement === "event") expect(await element._settlePromptMutation(mutation.clientRequestId)).toBe(true);
    resolve({});
    await sending;
    expect(element._durationChoices.has("one")).toBe(false);
    expect(element._durationChoiceRevisions.get("one")).toBe(2);
    element.remove();
  });

  it.each([
    ["direct", "newer"], ["event", "newer"],
    ["direct", "ABA"], ["event", "ABA"],
    ["direct", "user-switch"], ["event", "user-switch"],
    ["direct", "user-switch-ABA"], ["event", "user-switch-ABA"],
  ])("preserves a %s acknowledged choice after %s edits", async (acknowledgement, change) => {
    const { element, mutation, sending, resolve } = pendingDurationPrompt(60);
    let expected = 300;
    if (change.startsWith("user-switch")) {
      element._hass = { user: { id: "owner-b" } };
      element._loadPreferences();
      element._setDurationChoice("one", 300);
      if (change === "user-switch-ABA") {
        element._hass = { user: { id: "owner-a" } };
        element._loadPreferences();
        element._setDurationChoice("one", 60);
        expected = 60;
      }
    } else {
      element._setDurationChoice("one", 300);
      if (change === "ABA") { element._setDurationChoice("one", 60); expected = 60; }
    }
    expect(element._consumeSubmittedDurationChoice(mutation)).toBe(false);
    if (acknowledgement === "event") await element._settlePromptMutation(mutation.clientRequestId);
    resolve({});
    await sending;
    expect(element._durationChoices.get("one")).toBe(expected);
    element.remove();
  });

  it.each(["direct", "event"])("preserves a newer explicit no-limit choice on %s acknowledgement", async (acknowledgement) => {
    const { element, mutation, sending, resolve } = pendingDurationPrompt(60);
    element._setDurationChoice("one", null);
    if (acknowledgement === "event") await element._settlePromptMutation(mutation.clientRequestId);
    resolve({});
    await sending;
    expect(element._durationChoices.has("one")).toBe(true);
    expect(element._durationChoices.get("one")).toBeNull();
    element.remove();
  });

  it.each(["direct", "event"])("retains a later limit when the %s acknowledged prompt submitted no limit", async (acknowledgement) => {
    const { element, mutation, sending, resolve } = pendingDurationPrompt(null);
    expect(element._callWS.mock.calls[0][1]).not.toHaveProperty("max_duration_seconds");
    expect(mutation.durationChoiceSnapshot.rawChoice).toBeNull();
    element._setDurationChoice("one", 300);
    if (acknowledgement === "event") await element._settlePromptMutation(mutation.clientRequestId);
    resolve({});
    await sending;
    expect(element._durationChoices.get("one")).toBe(300);
    element.remove();
  });

  it("keeps choices for another selected chat while consuming the submitted chat choice", async () => {
    const { element, sending, resolve } = pendingDurationPrompt(60);
    element._selectedThreadId = "two";
    element._setDurationChoice("two", 300);
    resolve({});
    await sending;
    expect(element._durationChoices.has("one")).toBe(false);
    expect(element._durationChoices.get("two")).toBe(300);
    element.remove();
  });
});
