/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { assistantMarkdownMaxLength, renderAssistantMarkdown } from "../src/markdown.js";

describe("assistant Markdown rendering", () => {
  let host;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.replaceChildren(host);
  });

  function render(source, options) {
    const fragment = renderAssistantMarkdown(document, source, options);
    host.append(fragment);
    return fragment;
  }

  it("renders mixed block and inline Markdown with readable copy text", () => {
    const fragment = render([
      "## Summary",
      "A **clear** answer with `inline code` and [Home](https://ha.example/lovelace).",
      "",
      "- first item",
      "- second item",
      "",
      "| Name | Value |",
      "| --- | --- |",
      "| state | `on` |",
      "",
      "```js",
      "const answer = 42;",
      "```",
    ].join("\n"));

    expect(host.querySelector("h2")?.textContent).toBe("Summary");
    expect(host.querySelector("strong")?.textContent).toBe("clear");
    expect(host.querySelector("code")?.textContent).toBe("inline code");
    expect(host.querySelectorAll("ul > li")).toHaveLength(2);
    expect(host.querySelector("table th")?.textContent).toBe("Name");
    expect(host.querySelector("table td code")?.textContent).toBe("on");
    expect(host.querySelector("pre code")?.dataset.language).toBe("js");
    expect(fragment.plainText).toContain("A clear answer with inline code and Home (https://ha.example/lovelace).");
    expect(fragment.plainText).toContain("const answer = 42;");
    expect(fragment.plainText).toContain("state\ton");
  });

  it("passes fenced code to the panel callback so its existing copy control can be retained", () => {
    const createCodeBlock = vi.fn((_document, code, language) => {
      const block = document.createElement("section");
      block.dataset.copyText = code;
      block.dataset.language = language;
      block.append(document.createElement("button"));
      return block;
    });

    render("```python\nprint('safe')\n```", { createCodeBlock });

    expect(createCodeBlock).toHaveBeenCalledWith(document, "print('safe')\n", "python");
    expect(host.querySelector("section")?.dataset.copyText).toBe("print('safe')\n");
    expect(host.querySelector("button")).not.toBeNull();
  });

  it("preserves fenced code trailing whitespace for exact copy", () => {
    const source = "```text\nfirst\nsecond\n```";
    const fragment = render(source);

    expect(host.querySelector("pre code")?.textContent).toBe("first\nsecond\n");
    expect(fragment.plainText).toBe("first\nsecond\n");
  });

  it("keeps raw HTML inert and strips unsafe links and remote image fetching", () => {
    render([
      '<img src=x onerror="alert(1)">',
      '<script>window.pwned = true</script>',
      "[bad scheme](javascript:alert(1))",
      "[credential URL](https://user:pass@ha.example/private)",
      "![remote image](https://tracker.example/pixel.png)",
      "[unsafe http](http://tracker.example/path)",
      "[secure external](https://docs.example/guide)",
    ].join("\n"));

    expect(host.querySelector("img, script, iframe, object, embed, [onerror]")).toBeNull();
    expect(host.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(host.textContent).toContain("<script>window.pwned = true</script>");
    expect(host.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(host.querySelector('a[href*="user"]')).toBeNull();
    expect(host.querySelector('a[href^="http://tracker"]')).toBeNull();
    const external = host.querySelector('a[href="https://docs.example/guide"]');
    expect(external?.target).toBe("_blank");
    expect(external?.rel).toBe("noopener noreferrer");
  });

  it("allows safe Home Assistant routes and preserves their same-origin behaviour", () => {
    render("[Download](/api/codex_bridge/threads/chat/artifacts/file)");
    const link = host.querySelector("a");
    expect(link?.href).toBe(`${window.location.origin}/api/codex_bridge/threads/chat/artifacts/file`);
    expect(link?.target).toBe("");
  });

  it("keeps raw workspace and filesystem links inert, including angle-bracket targets", () => {
    render([
      "[Download the document](</config/workspaces/example/hello.docx>)",
      "[Workspace file](/workspace/project/config.yaml)",
      "[Local file](file:///C:/Users/example/secret.txt)",
      "[Sandbox file](sandbox:/private/file.txt)",
      "[Windows path](C:\\Users\\example\\secret.txt)",
    ].join("\n"));

    expect(host.querySelectorAll("a")).toHaveLength(0);
    expect(host.textContent).toContain("Download the document");
    expect(host.textContent).toContain("Workspace file");
    expect(host.textContent).toContain("Local file");
    expect(host.textContent).toContain("Sandbox file");
    expect(host.textContent).toContain("Windows path");
  });

  it("preserves the full response when formatting input exceeds the limit", () => {
    const source = `**bold** and *italic* and ~~removed~~\n\n${"x".repeat(assistantMarkdownMaxLength + 10)}`;
    const fragment = render(source);
    expect(fragment.plainText.endsWith(source.slice(assistantMarkdownMaxLength))).toBe(true);
    expect(host.querySelector(".assistant-markdown-overflow-notice")?.textContent).toContain("shown as plain text");
    expect(host.querySelector(".assistant-markdown-overflow")?.textContent).toBe(source.slice(assistantMarkdownMaxLength));
  });

  it("finishes large unmatched link delimiters without repeated rescanning", () => {
    const malformed = "[".repeat(40_000);
    const start = performance.now();
    const fragment = render(malformed);
    const elapsed = performance.now() - start;

    expect(host.querySelector(".assistant-markdown-paragraph")?.textContent).toBe(malformed);
    expect(fragment.plainText).toBe(malformed);
    expect(elapsed).toBeLessThan(1_500);
  });

  it("treats escaped Markdown delimiters as literal text", () => {
    const fragment = render("\\*not emphasis\\* and \\`not code\\`");
    expect(host.querySelector("strong, em, code")).toBeNull();
    expect(host.textContent).toBe("*not emphasis* and `not code`");
    expect(fragment.plainText).toBe("*not emphasis* and `not code`");
  });
});
