/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function panelWithStore() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._hass = { user: { id: "alice" } };
  panel._loadPreferences();
  panel._selectedThreadId = "chat";
  panel._activeThread = { thread_id: "chat", title: "Chat", status: "idle", mode: "edit", attachments: [] };
  panel._config = { capabilities: [] };
  panel._status = { auth: { state: "ok", auth_required: false }, account: { available: true } };
  panel._preferences.draftRecovery = "on";
  const store = {
    enabled: true, ownerKey: panel._preferenceKey, maxDraftChars: 8192,
    saveDraft: vi.fn(async () => ({ ok: true, revision: { at: 100, writer: "sender", sequence: 1 } })), removeDraft: vi.fn(async () => ({ ok: true })),
    restoreDraft: vi.fn(async () => ({ ok: true, draft: "Recovered text" })),
    clearAll: vi.fn(async () => ({ ok: true })),
  };
  panel._draftRecoveryStore = store;
  panel._callWS = vi.fn(async () => ({}));
  panel._refreshActiveThread = vi.fn(async () => {});
  return { panel, store };
}

describe("opt-in draft recovery panel ownership", () => {
  beforeEach(() => { document.body.replaceChildren(); localStorage.clear(); vi.restoreAllMocks(); });

  it("is disabled by default and restores only into an unchanged composer without sending", async () => {
    const defaultPanel = document.createElement("codex-bridge-panel");
    expect(defaultPanel._preferences.draftRecovery).toBe("off");
    const { panel, store } = panelWithStore();
    await panel._restoreRecoveredDraft("chat");
    expect(panel.shadowRoot.getElementById("prompt-input").value).toBe("Recovered text");
    expect(panel._draftForThread("chat")).toBe("Recovered text");
    expect(panel._callWS).not.toHaveBeenCalled();
    expect(store.saveDraft).not.toHaveBeenCalled();
    expect(panel.shadowRoot.getElementById("draft-recovery-status").textContent).toContain("Review and edit");
  });

  it.each(["edit", "chat", "user", "discard"])("does not overwrite after a late restore and %s", async (change) => {
    const { panel, store } = panelWithStore();
    const pending = deferred();
    store.restoreDraft.mockReturnValue(pending.promise);
    const restoring = panel._restoreRecoveredDraft("chat");
    if (change === "edit") panel._setDraftForThread("chat", "Newer text");
    if (change === "chat") panel._setSelectedThreadId("other");
    if (change === "user") { panel._hass = { user: { id: "bob" } }; panel._loadPreferences(); }
    if (change === "discard") panel._setDraftForThread("chat", "");
    pending.resolve({ ok: true, draft: "Old text" });
    await restoring;
    expect(panel._draftForThread("chat")).toBe(change === "edit" ? "Newer text" : "");
  });

  it("clears sent text even when the response arrives after changing chats", async () => {
    const { panel, store } = panelWithStore();
    const pending = deferred();
    panel._callWS.mockReturnValue(pending.promise);
    panel.shadowRoot.getElementById("prompt-input").value = "Send this";
    const sending = panel._sendPrompt();
    panel._setSelectedThreadId("other");
    panel._setDraftForThread("other", "Other draft");
    pending.resolve({});
    await sending;
    expect(panel._draftForThread("chat")).toBe("");
    expect(panel._draftForThread("other")).toBe("Other draft");
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 100, writer: "sender", sequence: 1 } });
    expect(store.saveDraft).not.toHaveBeenCalledWith("chat", "");
  });

  it("clears owned recovery on disabling, and does not persist without a user", async () => {
    const { panel, store } = panelWithStore();
    panel._savePreferences({ ...panel._preferences, draftRecovery: "off" });
    expect(store.clearAll).toHaveBeenCalledTimes(1);
    expect(store.enabled).toBe(false);
    expect(panel._draftRecoveryStore.enabled).toBe(false);
    panel._hass = { user: null };
    panel._loadPreferences();
    panel._savePreferences({ ...panel._preferences, draftRecovery: "on" });
    expect(panel._draftRecoveryStore.ownerKey).toBeNull();
    await Promise.resolve();
  });

  it("removes a confirmed sent draft from its original user without clearing the new user's text", async () => {
    const { panel, store } = panelWithStore();
    const pending = deferred();
    panel._callWS.mockReturnValue(pending.promise);
    panel.shadowRoot.getElementById("prompt-input").value = "Alice sent text";
    const sending = panel._sendPrompt();
    panel._hass = { user: { id: "bob" } };
    panel._loadPreferences();
    panel._setDraftForThread("chat", "Bob's unsent text");
    pending.resolve({});
    await sending;
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 100, writer: "sender", sequence: 1 } });
    expect(panel._draftForThread("chat")).toBe("Bob's unsent text");
  });

  it.each(["acknowledgement", "event"])("binds %s clearing to the sent revision and preserves a newer local edit", async (kind) => {
    const { panel, store } = panelWithStore();
    const pending = deferred();
    panel._callWS.mockReturnValue(pending.promise);
    panel.shadowRoot.getElementById("prompt-input").value = "Sent text";
    const sending = panel._sendPrompt();
    const mutation = panel._promptMutation;
    panel._setDraftForThread("chat", "Sent text"); // A newer edit can contain identical text.
    if (kind === "event") expect(panel._settlePromptMutation(mutation.clientRequestId)).toBe(true);
    pending.resolve({});
    await sending;
    await mutation.draftRemoval;
    expect(store.removeDraft).toHaveBeenCalledTimes(1);
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 100, writer: "sender", sequence: 1 } });
    expect(store.saveDraft).not.toHaveBeenCalledWith("chat", "");
    expect(panel._draftForThread("chat")).toBe("Sent text");
  });

  it("waits for the captured save result after an early send acknowledgement", async () => {
    const { panel, store } = panelWithStore();
    const save = deferred();
    store.saveDraft.mockReturnValue(save.promise);
    panel.shadowRoot.getElementById("prompt-input").value = "Sent text";
    await panel._sendPrompt();
    expect(store.removeDraft).not.toHaveBeenCalled();
    save.resolve({ ok: true, revision: { at: 200, writer: "sender", sequence: 2 } });
    await save.promise;
    await Promise.resolve();
    await Promise.resolve();
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 200, writer: "sender", sequence: 2 } });
  });

  it("does not remove an unknown stored revision after a failed draft save", async () => {
    const { panel, store } = panelWithStore();
    store.saveDraft.mockResolvedValue({ ok: false, reason: "storage_unavailable" });
    panel.shadowRoot.getElementById("prompt-input").value = "Sent text";
    await panel._sendPrompt();
    expect(store.removeDraft).not.toHaveBeenCalled();
    expect(panel._draftForThread("chat")).toBe("");
  });

  it("retains the saved revision through an uncertain retry without resaving over another tab", async () => {
    const { panel, store } = panelWithStore();
    panel._callWS.mockRejectedValueOnce(new Error("Response lost")).mockResolvedValueOnce({});
    panel.shadowRoot.getElementById("prompt-input").value = "Retry text";
    await panel._sendPrompt();
    const mutation = panel._promptMutation;
    expect(mutation.state).toBe("retryable");
    expect(store.removeDraft).not.toHaveBeenCalled();
    await panel._sendPrompt();
    await mutation.draftRemoval;
    expect(store.saveDraft).toHaveBeenCalledTimes(1);
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 100, writer: "sender", sequence: 1 } });
    expect(panel._callWS.mock.calls[1][1].client_request_id).toBe(panel._callWS.mock.calls[0][1].client_request_id);
  });

  it("preserves the returning user's new draft after switching away during send", async () => {
    const { panel, store } = panelWithStore();
    const pending = deferred();
    panel._callWS.mockReturnValue(pending.promise);
    panel.shadowRoot.getElementById("prompt-input").value = "Original text";
    const sending = panel._sendPrompt();
    panel._hass = { user: { id: "bob" } }; panel._loadPreferences();
    panel._hass = { user: { id: "alice" } }; panel._loadPreferences();
    panel._setDraftForThread("chat", "Returning user's new text");
    pending.resolve({});
    await sending;
    expect(panel._draftForThread("chat")).toBe("Returning user's new text");
    expect(store.removeDraft).toHaveBeenCalledWith("chat", { expectedRevision: { at: 100, writer: "sender", sequence: 1 } });
  });

  it("removes an older saved snapshot as soon as a new draft exceeds the bound", async () => {
    const { panel, store } = panelWithStore();
    panel._setDraftForThread("chat", "x".repeat(8193));
    expect(store.removeDraft).toHaveBeenCalledWith("chat");
    expect(store.saveDraft).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(panel._draftForThread("chat")).toHaveLength(8193);
    expect(panel.shadowRoot.getElementById("draft-recovery-status").textContent).toContain("only for this visit");
  });
});
