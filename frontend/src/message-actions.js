const PARTIAL_MATH_MESSAGE = "Select the entire mathematical expression, or select a passage outside it.";
const emptyPassage = () => ({ text: "", error: "" });

function trustedMathExpression(node) {
  if (!["SPAN", "DIV"].includes(node.tagName) || node.children.length !== 1) return false;
  const math = node.firstElementChild;
  if (math.localName !== "math" || math.namespaceURI !== "http://www.w3.org/1998/Math/MathML") return false;
  // Only the renderer's single MathML tree may sit inside the source wrapper.
  if ([...node.childNodes].some((child) => child !== math)) return false;
  const source = node.dataset.mathSource;
  return typeof source === "string" && /^(?:\$\$[\s\S]+\$\$|\$(?!\$)[\s\S]+\$|\\\([\s\S]+\\\)|\\\[[\s\S]+\\\])$/u.test(source);
}

function containsWholeNode(range, node) {
  const nodeRange = node.ownerDocument.createRange();
  nodeRange.selectNode(node);
  return range.compareBoundaryPoints(0, nodeRange) <= 0 && range.compareBoundaryPoints(2, nodeRange) >= 0;
}

function serialiseMathPassage(range, expressions, selection) {
  if (typeof selection.removeAllRanges !== "function" || typeof selection.addRange !== "function") {
    return { text: "", error: "This browser cannot copy a maths passage. Use Copy message instead." };
  }
  const original = range.cloneRange();
  const originalText = selection.toString();
  const { anchorNode, anchorOffset, focusNode, focusOffset } = selection;
  const root = range.commonAncestorContainer;
  const canRestoreDirection = typeof selection.setBaseAndExtent === "function"
    && root.contains(anchorNode) && root.contains(focusNode);
  let backward = false;
  if (canRestoreDirection) {
    const anchor = range.cloneRange(); anchor.setStart(anchorNode, anchorOffset); anchor.collapse(true);
    const focus = range.cloneRange(); focus.setStart(focusNode, focusOffset); focus.collapse(true);
    backward = anchor.compareBoundaryPoints(0, focus) > 0;
  }
  const readRange = (part) => {
    if (part.collapsed) return "";
    selection.removeAllRanges();
    selection.addRange(part);
    return selection.toString();
  };
  try {
    let text = "";
    let consumed = 0;
    for (const expression of expressions) {
      const before = original.cloneRange();
      before.setEndBefore(expression);
      // End inside the wrapper: ending after a display node includes its
      // trailing layout separator, which belongs to the surrounding passage.
      const through = original.cloneRange(); through.setEnd(expression, expression.childNodes.length);
      const beforeText = readRange(before);
      const throughText = readRange(through);
      // Prefix offsets identify the selected node even when ordinary prose has
      // identical glyphs. Slice the original native text so block separators at
      // the expression's trailing boundary are retained, rather than invented.
      if (!originalText.startsWith(beforeText) || !originalText.startsWith(throughText)
        || beforeText.length < consumed || throughText.length < beforeText.length) {
        return { text: "", error: "This browser cannot copy a maths passage. Use Copy message instead." };
      }
      text += originalText.slice(consumed, beforeText.length) + expression.dataset.mathSource;
      consumed = throughText.length;
    }
    return { text: text + originalText.slice(consumed), error: "" };
  } finally {
    // Selection text is read synchronously; the user's original range and,
    // where exposed across Shadow DOM, its direction are restored before return.
    selection.removeAllRanges();
    if (backward) selection.setBaseAndExtent(original.endContainer, original.endOffset, original.startContainer, original.startOffset);
    else selection.addRange(original);
  }
}

/** Return a validated public passage, with guidance for unsupported maths selection. */
export function selectedMessagePassageResult(content, selection) {
  try {
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return emptyPassage();
    let range = selection.getRangeAt(0);
    const root = content.getRootNode();
    if (selection.getComposedRanges && root.host) {
      const composed = selection.getComposedRanges({ shadowRoots: [root] });
      if (composed.length !== 1) return emptyPassage();
      range = content.ownerDocument.createRange();
      range.setStart(composed[0].startContainer, composed[0].startOffset);
      range.setEnd(composed[0].endContainer, composed[0].endOffset);
    }
    if (!content.contains(range.startContainer) || !content.contains(range.endContainer)) return emptyPassage();
    const expressions = [...content.querySelectorAll(".assistant-math-expression[data-math-source]")]
      .filter((node) => trustedMathExpression(node) && range.intersectsNode(node));
    if (expressions.some((node) => !containsWholeNode(range, node))) return { text: "", error: PARTIAL_MATH_MESSAGE };
    // Never harvest hidden text. The trusted full maths wrapper is serialised
    // exclusively from its source, so its hidden MathML annotations are ignored.
    const forbidden = [...content.querySelectorAll('.code-head, [hidden], [aria-hidden="true"]')];
    if (forbidden.some((node) => range.intersectsNode(node)
      && (node.classList.contains("code-head") || !expressions.some((expression) =>
        node.namespaceURI === "http://www.w3.org/1998/Math/MathML"
          && node.closest("annotation, annotation-xml") && expression.firstElementChild.contains(node))))) return emptyPassage();
    if (expressions.length) return serialiseMathPassage(range, expressions, selection);
    // Native Selection preserves rendered block separators that Range omits.
    return { text: selection.toString !== Object.prototype.toString && typeof selection.toString === "function"
      ? selection.toString() : range.toString(), error: "" };
  } catch {
    // Unsupported or stale composed ranges must fail closed, without throwing
    // out of a click handler or copying an unvalidated document selection.
    return emptyPassage();
  }
}

/** Keep the original string contract for callers that do not display guidance. */
export function selectedMessagePassage(content, selection) {
  return selectedMessagePassageResult(content, selection).text;
}

export function attributedMessageQuote(text, role, title) {
  const speaker = role === "user" ? "Your message" : "Assistant response";
  const safeTitle = String(title || "this chat").replace(/[\r\n]/gu, " ");
  return `${speaker} in “${safeTitle}”:\n${String(text).split("\n").map((line) => `> ${line}`).join("\n")}`;
}
