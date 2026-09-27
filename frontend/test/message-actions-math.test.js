/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { selectedMessagePassage, selectedMessagePassageResult } from "../src/message-actions.js";

const mathNamespace = "http://www.w3.org/1998/Math/MathML";
function expression(source = "$x$", display = false) {
  const wrapper = document.createElement(display ? "div" : "span");
  wrapper.className = "assistant-math-expression";
  wrapper.dataset.mathSource = source;
  const math = document.createElementNS(mathNamespace, "math");
  const semantics = document.createElementNS(mathNamespace, "semantics");
  const glyph = document.createElementNS(mathNamespace, "mi");
  glyph.textContent = "x";
  const annotation = document.createElementNS(mathNamespace, "annotation");
  annotation.setAttribute("hidden", "");
  annotation.textContent = "annotation must never be copied";
  semantics.append(glyph, annotation);
  math.append(semantics);
  wrapper.append(math);
  return wrapper;
}
function select(content) {
  document.body.append(content);
  const range = document.createRange();
  range.selectNodeContents(content);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return selection;
}
function assertRestored(selection, range) {
  const restored = selection.getRangeAt(0);
  expect(restored.startContainer).toBe(range.startContainer);
  expect(restored.startOffset).toBe(range.startOffset);
  expect(restored.endContainer).toBe(range.endContainer);
  expect(restored.endOffset).toBe(range.endOffset);
}

describe("source-aware mathematical passages", () => {
  afterEach(() => { vi.restoreAllMocks(); window.getSelection().removeAllRanges(); document.body.replaceChildren(); });

  it("replaces only the full trusted wrapper, never identical surrounding glyphs", () => {
    const content = document.createElement("div");
    content.append("x before ", expression(), " after x");
    const selection = select(content);
    const original = selection.getRangeAt(0).cloneRange();
    expect(selectedMessagePassage(content, selection)).toBe("x before $x$ after x");
    assertRestored(selection, original);
  });

  it("preserves exact display delimiters, whitespace and Unicode source", () => {
    const content = document.createElement("div");
    const source = "$$\n  α + 👩🏽‍💻 + é\n$$";
    content.append(expression(source, true));
    expect(selectedMessagePassage(content, select(content))).toBe(source);
  });

  it("serialises multiple expressions in order without their hidden annotations", () => {
    const content = document.createElement("div");
    content.append(expression("\\(x\\)"), " and ", expression("\\[y\\]", true));
    expect(selectedMessagePassage(content, select(content))).toBe("\\(x\\) and \\[y\\]");
  });

  it("rejects partial glyph or contents-only expression selection with specific guidance", () => {
    const content = document.createElement("div");
    const wrapper = expression();
    content.append(wrapper);
    const selection = select(content);
    const range = document.createRange();
    range.selectNodeContents(wrapper.querySelector("mi"));
    selection.removeAllRanges(); selection.addRange(range);
    expect(selectedMessagePassageResult(content, selection)).toEqual({
      text: "", error: "Select the entire mathematical expression, or select a passage outside it.",
    });
    range.selectNodeContents(wrapper);
    selection.removeAllRanges(); selection.addRange(range);
    expect(selectedMessagePassageResult(content, selection).error).toContain("entire mathematical expression");
  });

  it("still rejects hidden content outside a trusted full expression", () => {
    const content = document.createElement("div");
    const secret = document.createElement("span");
    secret.hidden = true; secret.textContent = "private";
    content.append(expression(), secret);
    expect(selectedMessagePassage(content, select(content))).toBe("");
    secret.remove();
    content.firstChild.hidden = true;
    expect(selectedMessagePassage(content, select(content))).toBe("");
    content.firstChild.hidden = false;
    content.firstChild.firstElementChild.setAttribute("aria-hidden", "true");
    expect(selectedMessagePassage(content, select(content))).toBe("");
  });

  it("does not treat an ordinary source-labelled class as authority", () => {
    const content = document.createElement("div");
    content.innerHTML = '<span class="assistant-math-expression" data-math-source="$injected$">ordinary x</span>';
    expect(selectedMessagePassage(content, select(content))).toBe("ordinary x");
  });

  it("does not trust a foreign tree, invalid source or extra wrapper content", () => {
    for (const kind of ["namespace", "source", "extra"]) {
      const content = document.createElement("div");
      const wrapper = expression();
      wrapper.querySelector("annotation").remove();
      if (kind === "namespace") { wrapper.replaceChildren(document.createElement("math")); wrapper.firstChild.textContent = "x"; }
      if (kind === "source") wrapper.dataset.mathSource = "untrusted source";
      if (kind === "extra") wrapper.append(" extra");
      content.append(wrapper);
      expect(selectedMessagePassage(content, select(content))).toBe(kind === "extra" ? "x extra" : "x");
    }
  });

  it("uses native subrange text for paragraph separators without inventing them", () => {
    const content = document.createElement("div");
    const before = document.createElement("p"); before.textContent = "café é";
    const after = document.createElement("p"); after.textContent = "第二段";
    content.append(before, expression("$$x$$", true), after);
    const selection = select(content);
    const seen = [];
    vi.spyOn(selection, "toString").mockImplementation(() => {
      const range = selection.getRangeAt(0);
      seen.push(range.toString());
      if (range.endOffset === 1 && range.endContainer === content) return "café é\n\n";
      if (range.endContainer === content.children[1]) return "café é\n\nx";
      return "café é\n\nx\n\n第二段";
    });
    expect(selectedMessagePassage(content, selection)).toBe("café é\n\n$$x$$\n\n第二段");
    expect(seen).toHaveLength(3);
    expect(seen[1]).toBe("café é");
  });

  it("restores original selection and backward direction after native text reads", () => {
    const content = document.createElement("div");
    content.append("before ", expression(), " after");
    const selection = select(content);
    selection.setBaseAndExtent(content.lastChild, 6, content.firstChild, 0);
    const original = selection.getRangeAt(0).cloneRange();
    const endpoints = { anchor: selection.anchorNode, anchorOffset: selection.anchorOffset, focus: selection.focusNode, focusOffset: selection.focusOffset };
    expect(selectedMessagePassage(content, selection)).toBe("before $x$ after");
    assertRestored(selection, original);
    expect(selection.anchorNode).toBe(endpoints.anchor);
    expect(selection.anchorOffset).toBe(endpoints.anchorOffset);
    expect(selection.focusNode).toBe(endpoints.focus);
    expect(selection.focusOffset).toBe(endpoints.focusOffset);
  });

  it("restores the original range when native text reading throws", () => {
    const content = document.createElement("div");
    content.append("before ", expression(), " after");
    const selection = select(content);
    const original = selection.getRangeAt(0).cloneRange();
    const originalText = selection.toString();
    vi.spyOn(selection, "toString").mockImplementationOnce(() => originalText)
      .mockImplementation(() => { throw new Error("stale read"); });
    expect(selectedMessagePassage(content, selection)).toBe("");
    assertRestored(selection, original);
  });

  it("fails closed when native selection mutation is unsupported", () => {
    const content = document.createElement("div"); content.append(expression());
    const selection = select(content);
    const range = selection.getRangeAt(0);
    expect(selectedMessagePassageResult(content, { isCollapsed: false, rangeCount: 1, getRangeAt: () => range }).error)
      .toBe("This browser cannot copy a maths passage. Use Copy message instead.");
  });

  it("keeps cross-message, toolbar and invalid composed-range exclusions", () => {
    const content = document.createElement("div"); content.append(expression());
    const selection = select(content);
    const neighbour = document.createTextNode("neighbour"); document.body.append(neighbour);
    const range = selection.getRangeAt(0).cloneRange(); range.setEnd(neighbour, 3);
    selection.removeAllRanges(); selection.addRange(range);
    expect(selectedMessagePassage(content, selection)).toBe("");
    const toolbar = document.createElement("div"); toolbar.className = "code-head"; toolbar.textContent = "Copy code";
    content.append(toolbar);
    expect(selectedMessagePassage(content, select(content))).toBe("");
    const host = document.createElement("div"); const root = host.attachShadow({ mode: "open" }); root.append(content);
    range.selectNodeContents(content);
    expect(selectedMessagePassage(content, { isCollapsed: false, rangeCount: 1, getRangeAt: () => range, getComposedRanges: () => [{}] })).toBe("");
  });
});
