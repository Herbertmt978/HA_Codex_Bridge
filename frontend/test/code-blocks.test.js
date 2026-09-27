/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { codeBlockLimits, renderCodeBlock } from "../src/code-blocks.js";

describe("bounded code block renderer", () => {
  let host;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.replaceChildren(host);
  });

  it("highlights known JavaScript tokens without changing displayed or copied source", () => {
    const source = "const answer = 42;\n// keep exact spacing\n";
    const onCopy = vi.fn();
    const block = renderCodeBlock(document, source, "js", { onCopy, lineNumbers: true });
    host.append(block);

    expect(block.dataset.highlighted).toBe("true");
    expect(block.querySelector(".code-text").textContent).toBe(source);
    expect(block.querySelector(".code-token-keyword")?.textContent).toBe("const");
    expect(block.querySelectorAll(".code-line[data-line-number]")).toHaveLength(3);
    block.querySelector(".copy-button").click();
    expect(onCopy).toHaveBeenCalledWith(source);
  });

  it("keeps malicious code inert and caps untrusted language labels", () => {
    const source = "<img src=x onerror=alert(1)>\n<script>bad()</script>";
    const block = renderCodeBlock(document, source, "x".repeat(500));
    host.append(block);

    expect(block.querySelector("img, script")).toBeNull();
    expect(block.querySelector(".code-text").textContent).toBe(source);
    expect(block.querySelector(".code-language").textContent).toHaveLength(32);
    expect(block.dataset.highlighted).toBe("false");
  });

  it("keeps markup-like source inert inside highlighted strings", () => {
    const source = `const payload = "<img src=x onerror=alert(1)>";`;
    const block = renderCodeBlock(document, source, "javascript");
    host.append(block);

    expect(block.dataset.highlighted).toBe("true");
    expect(block.querySelector("img")).toBeNull();
    expect(block.querySelector(".code-text").textContent).toBe(source);
    expect(block.querySelector(".code-token-string")?.textContent).toBe('"<img src=x onerror=alert(1)>"');
  });

  it("uses plain text for unknown languages and does not enable absent copy authority", () => {
    const source = "select <value> from example;";
    const block = renderCodeBlock(document, source, "not-a-language");
    host.append(block);

    expect(block.dataset.highlighted).toBe("false");
    expect(block.querySelector(".code-text").textContent).toBe(source);
    expect(block.querySelectorAll("[class^='code-token-']")).toHaveLength(0);
    expect(block.querySelector(".copy-button").disabled).toBe(true);
  });

  it("toggles line numbers and wrapping with native keyboard-operable buttons", () => {
    const block = renderCodeBlock(document, "one\ntwo", "text");
    host.append(block);
    const pre = block.querySelector(".code-text");
    const numbers = block.querySelector("button.code-toggle");
    const wrap = block.querySelectorAll("button.code-toggle")[1];

    expect(numbers.getAttribute("aria-pressed")).toBe("false");
    numbers.click();
    expect(numbers.getAttribute("aria-pressed")).toBe("true");
    expect(pre.querySelectorAll(".code-line[data-line-number]")).toHaveLength(2);
    expect(pre.textContent).toBe("one\ntwo");
    wrap.click();
    expect(wrap.getAttribute("aria-pressed")).toBe("true");
    expect(pre.classList.contains("is-wrapped")).toBe(true);
    wrap.click();
    expect(wrap.getAttribute("aria-pressed")).toBe("false");
    expect(pre.classList.contains("is-wrapped")).toBe(false);
  });

  it("falls back to plain text when the highlighting character budget is exceeded", () => {
    const source = `const value = 1;${" ".repeat(codeBlockLimits.maxHighlightChars)}`;
    const block = renderCodeBlock(document, source, "javascript");

    expect(block.dataset.highlighted).toBe("false");
    expect(block.querySelectorAll("[class^='code-token-']")).toHaveLength(0);
    expect(block.querySelector(".code-text").textContent).toBe(source);
  });

  it("falls back when token count would create too many syntax nodes", () => {
    const source = Array.from({ length: codeBlockLimits.maxHighlightTokens + 1 }, (_, index) => `let n${index};`).join(" ");
    const block = renderCodeBlock(document, source, "javascript");

    expect(block.dataset.highlighted).toBe("false");
    expect(block.querySelectorAll("[class^='code-token-']")).toHaveLength(0);
    expect(block.querySelector(".code-text").textContent).toBe(source);
  });

  it("disables line numbering when the line budget is exceeded but keeps wrapped text readable", () => {
    const source = Array.from({ length: codeBlockLimits.maxHighlightLines + 1 }, () => "long-line").join("\n");
    const block = renderCodeBlock(document, source, "plain", { wrap: true });
    const pre = block.querySelector(".code-text");
    const numbers = block.querySelector("button.code-toggle");

    expect(numbers.disabled).toBe(true);
    expect(pre.textContent).toBe(source);
    expect(pre.classList.contains("is-wrapped")).toBe(true);
    expect(block.querySelectorAll(".code-line")).toHaveLength(0);
  });

  it.each(["\n", "\r\n", "\r"])("preserves %j source and numbers logical lines after toggling", (ending) => {
    const source = `// comment${ending}const message = "🌍";${ending}\t${ending}`;
    const onCopy = vi.fn();
    const block = renderCodeBlock(document, source, "js", { onCopy });
    const numbers = block.querySelector(".code-toggle");
    numbers.click();
    block.querySelectorAll(".code-toggle")[1].click();
    expect(block.querySelectorAll(".code-line")).toHaveLength(4);
    expect(block.querySelectorAll(".code-token-comment")).toHaveLength(1);
    expect(block.querySelector(".code-token-keyword").textContent).toBe("const");
    expect(block.querySelector(".code-text").textContent).toBe(source);
    block.querySelector(".copy-button").click();
    expect(onCopy).toHaveBeenCalledWith(source);
    numbers.click();
    expect(block.querySelectorAll("[data-line-number]")).toHaveLength(0);
    expect(block.querySelector(".code-text").textContent).toBe(source);
  });

  it.each(["\n", "\r\n", "\r"])("applies the line bound to %j without truncating fallback source", (ending) => {
    const source = `const n = 1;${ending}`.repeat(codeBlockLimits.maxHighlightLines);
    const onCopy = vi.fn();
    const block = renderCodeBlock(document, source, "js", { onCopy, lineNumbers: true });
    expect(block.querySelector(".code-toggle").disabled).toBe(true);
    expect(block.querySelectorAll(".code-line, [class^='code-token-']")).toHaveLength(0);
    expect(block.querySelector(".code-text").textContent).toBe(source);
    block.querySelector(".copy-button").click();
    expect(onCopy).toHaveBeenCalledWith(source);
  });

  it("retains multiline tokens, blank lines and the accepted limit boundaries", () => {
    const source = `/* first\r\nsecond\rthird */\nconst text = \`one\ntwo\`;`;
    const block = renderCodeBlock(document, source, "js", { lineNumbers: true });
    expect(block.querySelectorAll(".code-line")).toHaveLength(5);
    expect(block.querySelector(".code-text").textContent).toBe(source);
    const atCharacters = renderCodeBlock(document, `const n = 1;${" ".repeat(codeBlockLimits.maxHighlightChars - 12)}`, "js");
    expect(atCharacters.dataset.highlighted).toBe("true");
    const atLines = renderCodeBlock(document, "\n".repeat(codeBlockLimits.maxHighlightLines - 1), "text", { lineNumbers: true });
    expect(atLines.querySelectorAll(".code-line")).toHaveLength(codeBlockLimits.maxHighlightLines);
    const atTokens = renderCodeBlock(document, "let x;".repeat(codeBlockLimits.maxHighlightTokens), "js");
    expect(atTokens.querySelectorAll(".code-token-keyword")).toHaveLength(codeBlockLimits.maxHighlightTokens);
  });

  it("copies captured source even if presentation nodes change", () => {
    const onCopy = vi.fn();
    const source = "const original = 1;\r\n";
    const block = renderCodeBlock(document, source, "js", { onCopy });
    block.querySelector(".code-text").textContent = "presentation text";
    block.querySelector(".copy-button").click();
    expect(onCopy).toHaveBeenCalledWith(source);
  });
});
