import { describe, it, expect } from "vitest";
import { authenticatedChatUrl, chatResources } from "../src/chat-resources.js";

describe("chat references", () => {
  it("deduplicates real GitHub PR links and excludes credentials and code examples", () => {
    const resources = chatResources([{ event_type: "message.completed", payload: { text: "[Fix login](https://github.com/owner/repo/pull/94) https://github.com/owner/repo/pull/94/files `https://github.com/owner/repo/pull/1` https://user:secret@example.com [guide](https://example.com/docs)" } }]);
    expect(resources.pullRequests).toEqual([{ title: "Fix login", href: "https://github.com/owner/repo/pull/94" }]);
    expect(resources.sources).toEqual([{ title: "guide", href: "https://example.com/docs" }]);
    expect(JSON.stringify(resources)).not.toContain("secret");
  });

  it("copies only a same-origin chat deep link, without existing query data", () => {
    expect(authenticatedChatUrl({ href: "https://ha.example/codex-bridge?private=removed#old" }, "chat-one"))
      .toBe("https://ha.example/codex-bridge?thread=chat-one");
    expect(authenticatedChatUrl({ href: "https://ha.example/codex-bridge" }, "../bad")).toBeNull();
  });
});
