/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

function panel() {
  const element = document.createElement("codex-bridge-panel");
  document.body.append(element);
  return element;
}

describe("account details and file messages", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("shows per-account usage and reset expiry in readable cards without leaking opaque credit IDs", () => {
    const element = panel();
    const id = "a".repeat(32);
    element._config = { capabilities: ["account_profiles_v1", "account_profile_details_v1"] };
    element._accountProfiles = [{ id, label: "Personal", plan: "pro", active: true }];
    element._accountProfilesLoaded = true;
    element._accountProfileDetails.set(id, {
      status: "available", plan: "pro",
      windows: [{ name: "5 hours", remaining_percent: 58 }, { name: "Weekly", remaining_percent: 73 }],
      available_resets: 2, next_reset_expiry: 2_000_150_000, expiry_complete: true,
    });
    element._appMenuOpen = true;
    element._renderAppMenu();
    const card = element.shadowRoot.querySelector(".account-menu-row");
    expect(card.textContent).toContain("ChatGPT Pro");
    expect(card.textContent).toContain("58% remaining");
    expect(card.textContent).toContain("73% remaining");
    expect(card.textContent).toContain("Available resets2");
    expect(card.textContent).toContain("Next reset expiry");
    expect(card.textContent).not.toContain("private-credit");
    element._accountProfileDetails.set(id, { status: "available", windows: [{ name: "Weekly", remaining_percent: 73 }] });
    element._renderAppMenu();
    expect(element.shadowRoot.querySelector(".account-menu-row").textContent).toContain("5-hour usageOff");
    element._accountProfileDetails.set(id, {
      status: "stale", updated_at: "2026-09-24T18:00:00Z",
      windows: [{ name: "Weekly", remaining_percent: 73 }],
      available_resets: 1, next_reset_expiry: 2_000_150_000,
    });
    element._renderAppMenu();
    expect(element.shadowRoot.querySelector(".account-menu-row").textContent).toContain("73% remaining");
    expect(element.shadowRoot.querySelector(".account-menu-row").textContent).toContain("figures may have changed");
    element._accountProfileDetails.set(id, { ...element._accountProfileDetails.get(id), reauthentication_required: true });
    element._renderAppMenu();
    expect(element.shadowRoot.querySelector(".account-menu-row").textContent).toContain("Sign in again to refresh this account");
  });

  it("loads every page of a long chat so the latest user prompt is visible", async () => {
    const element = panel();
    element._config = { capabilities: ["api_v1"] };
    const threadId = "thr_test";
    element._callWS = vi.fn(async (_action, options) => options.after === 0
      ? { events: [{ scope: "thread", thread_id: threadId, cursor: 200, event_type: "message.completed", payload: { text: "Earlier" } }], next_cursor: 200, has_more: true }
      : { events: [{ scope: "thread", thread_id: threadId, cursor: 350, event_type: "message.created", payload: { text: "Generate me a word document that just contains hello" } }], next_cursor: 350, has_more: false });
    const events = await element._loadThreadEventHistory(threadId);
    expect(events.map((event) => event.sequence)).toEqual([200, 350]);
    expect(element._callWS).toHaveBeenCalledTimes(2);
    expect(element._renderEvent(events[1]).textContent).toContain("Generate me a word document");
  });

  it("keeps the beginning and end of a chat beyond ten thousand events", async () => {
    const element = panel();
    const threadId = "thr_long";
    element._config = { capabilities: ["api_v1"] };
    element._selectedThreadId = threadId;
    element._render = vi.fn();
    element._startEventSubscription = vi.fn();
    element._listPendingInteractions = vi.fn().mockResolvedValue([]);
    element._callWS = vi.fn(async (action, options = {}) => {
      if (action === "get_thread") return { thread_id: threadId, project_id: "project", status: "idle", attachments: [] };
      if (action === "get_status") return {};
      if (action === "list_artifacts") return [];
      if (action === "get_events") {
        const start = options.after + 1;
        const end = Math.min(start + 255, 10_001);
        return {
          events: Array.from({ length: end - start + 1 }, (_, offset) => ({
            scope: "thread", thread_id: threadId, cursor: start + offset,
            event_type: "message.created", payload: { text: `Message ${start + offset}` },
          })),
          next_cursor: end, has_more: end < 10_001,
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    expect(await element._refreshActiveThread()).toBe(true);
    expect(element._events).toHaveLength(10_001);
    expect(element._eventStream.events).toHaveLength(10_001);
    expect(element._events[0].payload.text).toBe("Message 1");
    expect(element._events.at(-1).payload.text).toBe("Message 10001");
    expect(element._sequence).toBe(10_001);
  });

  it("renders a safe Word file card with file details and authenticated download actions", () => {
    const element = panel();
    element._config = { capabilities: ["office_preview_v1"] };
    const artifact = {
      artifact_id: "file-safe", filename: "/config/workspaces/hello.docx",
      mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      size_bytes: 512, source: "workspace",
    };
    element._artifacts = [artifact];
    const download = vi.spyOn(element, "_downloadArtifact").mockResolvedValue();
    const card = element._renderEvent({
      event_type: "artifact.added", sequence: 42,
      payload: { artifact_id: artifact.artifact_id, relative_path: "<img src=x onerror=alert(1)>.docx" },
    });
    element.shadowRoot.getElementById("message-list").append(card);
    expect(card.textContent).toContain("hello.docx");
    expect(card.textContent).toContain("Word document · 512 B");
    expect(card.textContent).not.toContain("/config/workspaces");
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector(".artifact-file-icon svg")).not.toBeNull();
    expect(card.querySelector('[data-action="open-artifact-preview"]')?.textContent).toBe("Preview");
    card.querySelector('[data-action="download-artifact"]').click();
    expect(download).toHaveBeenCalledWith(artifact.artifact_id);
  });

  it("replaces a standalone raw workspace link with the file card once the file is indexed", () => {
    const element = panel();
    const path = "/config/workspaces/example/hello.docx";
    const message = { event_type: "message.completed", sequence: 2,
      payload: { text: `[Download the Word document](<${path}>).` } };
    expect(element._renderEvent(message)?.textContent).toContain("[Download the Word document]");
    element._artifacts = [{ artifact_id: "document", filename: "hello.docx" }];
    expect(element._renderEvent(message)).toBeNull();
    expect(element._renderEvent({ ...message, payload: { text: `See [Download the Word document](<${path}>).` } })?.textContent)
      .toContain("See [Download");
  });
});
