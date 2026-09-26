import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

function panel() {
  const element = document.createElement("codex-bridge-panel");
  document.body.append(element);
  element._selectedThreadId = "chat-one";
  element._activeThread = { thread_id: "chat-one", title: "Chat", status: "idle", mode: "edit", attachments: [] };
  element._config = { capabilities: ["workspace_terminal_v1"] };
  element._renderChatControls();
  return element;
}

describe("desktop chat controls", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

  it("copies an authenticated deep link and reports clipboard failure truthfully", async () => {
    const element = panel();
    const copy = vi.spyOn(element, "_writeClipboardText").mockResolvedValue();
    await element._shareChat();
    expect(new URL(copy.mock.calls[0][0]).searchParams.get("thread")).toBe("chat-one");
    expect(element.shadowRoot.getElementById("share-status").textContent).toContain("sign-in is required");
    copy.mockRejectedValue(new Error("denied"));
    await element._shareChat();
    expect(element.shadowRoot.getElementById("error-strip").textContent).toMatch(/could not copy/i);
  });

  it("opens actual file previews and preserves expanded PR references across refreshes", () => {
    const element = panel();
    element._events = [{ event_type: "message.completed", payload: { text: "[Login fix](https://github.com/owner/repo/pull/1)" } }];
    element._artifacts = [{ artifact_id: "file-one", filename: "report.txt" }];
    element._renderActivityCenter();
    const open = vi.spyOn(element, "_openArtifactPreview").mockImplementation(() => {});
    element.shadowRoot.querySelector('[data-section="outputs"] button[data-artifact-id]').click();
    expect(open).toHaveBeenCalledWith("file-one", expect.anything());
    const detail = element.shadowRoot.querySelector(".resource-details");
    detail.open = true;
    element._artifacts.push({ artifact_id: "file-two", filename: "second.txt" });
    element._renderActivityCenter();
    expect(element.shadowRoot.querySelector(".resource-details").open).toBe(true);
    expect(element.shadowRoot.querySelector(".resource-details a").href).toBe("https://github.com/owner/repo/pull/1");
    expect(element.shadowRoot.querySelector('[data-section="pull-requests"]').textContent).not.toContain("Merged");
  });

  it("toggles panels without losing the existing file preview", () => {
    const element = panel();
    const root = element.shadowRoot;
    const preview = root.getElementById("artifact-preview");
    root.getElementById("toggle-bottom-button").click();
    expect(root.getElementById("bottom-preview").contains(preview)).toBe(true);
    root.getElementById("toggle-bottom-button").click();
    expect(root.getElementById("preview-home").contains(preview)).toBe(true);
    root.getElementById("toggle-context-button").click();
    expect(root.querySelector(".shell").classList.contains("context-hidden")).toBe(true);
    element._showSideTab("usage");
    expect(root.querySelector(".shell").classList.contains("context-hidden")).toBe(false);
  });

  it("closes chat actions with Escape and gates the terminal on capability and mode", () => {
    const element = panel();
    const root = element.shadowRoot;
    root.getElementById("chat-menu-button").click();
    expect(root.getElementById("chat-context-menu").hidden).toBe(false);
    root.querySelector("#chat-context-menu button").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(root.getElementById("chat-context-menu").hidden).toBe(true);
    expect(root.activeElement.id).toBe("chat-menu-button");
    element._renderTerminalAvailability();
    expect(root.getElementById("open-terminal-button").disabled).toBe(false);
    element._activeThread.mode = "observe";
    element._renderTerminalAvailability();
    expect(root.getElementById("open-terminal-button").disabled).toBe(true);
  });

  it("opens working chat settings without redundant menu tooltips", async () => {
    const element = panel();
    const root = element.shadowRoot;
    element._callWS = vi.fn().mockResolvedValue({ ...element._activeThread, title: "Renamed chat" });
    root.getElementById("chat-menu-button").click();
    const settings = root.querySelector('#chat-context-menu [data-chat-action="settings"]');
    expect(settings.dataset.tooltip).toBeUndefined();
    expect(settings.hasAttribute("title")).toBe(false);
    settings.click();
    expect(root.getElementById("thread-form-panel").classList.contains("visible")).toBe(true);
    expect(root.getElementById("thread-form-panel").textContent).toContain("Chat settings");
    const title = root.getElementById("thread-title-input");
    title.value = "Renamed chat";
    title.dispatchEvent(new Event("input", { bubbles: true }));
    root.querySelector('#thread-form-panel [data-action="save-thread"]').click();
    await vi.waitFor(() => expect(element._callWS).toHaveBeenCalledWith("update_thread", {
      thread_id: "chat-one",
      title: "Renamed chat",
      mode: "edit",
    }));
    await vi.waitFor(() => expect(root.getElementById("thread-form-panel").classList.contains("visible")).toBe(false));
    expect(element._activeThread.title).toBe("Renamed chat");
  });
});
