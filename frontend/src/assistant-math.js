import katex from "katex";

export const assistantMathLimits = Object.freeze({
  expressionChars: 2048,
  messageChars: 8192,
  expressions: 64,
  depth: 32,
  expansions: 200,
  sizeEm: 10,
  outputNodes: 4096,
});

// No author-defined macros, HTML, URLs, resources, or author-selected colours.
// Reject the commands before KaTeX so denied content has a uniform text fallback.
const UNSAFE_COMMAND = /\\(?:def|gdef|edef|xdef|let|futurelet|global|newcommand|renewcommand|providecommand|csname|catcode|href|url|includegraphics|html[A-Za-z]*|input|include|require|write|openout|read|color|textcolor|colorbox|fcolorbox|class|style)\b/u;

function escapedAt(source, index) {
  let start = index;
  while (start > 0 && source[start - 1] === "\\") start -= 1;
  return (index - start) % 2 === 1;
}

/** Constant-time closing lookup prevents repeated scans of unmatched dollars. */
export function inlineMathIndex(source) {
  const escaped = new Uint8Array(source.length);
  let slashes = 0;
  for (let index = 0; index < source.length; index += 1) {
    escaped[index] = slashes % 2;
    slashes = source[index] === "\\" ? slashes + 1 : 0;
  }
  const dollars = new Int32Array(source.length + 1).fill(-1);
  const parentheses = new Int32Array(source.length + 1).fill(-1);
  let dollar = -1;
  let parenthesis = -1;
  for (let index = source.length - 1; index >= 0; index -= 1) {
    if (!escaped[index]) {
      if (source[index] === "$" && source[index - 1] !== "$" && source[index + 1] !== "$"
        && !/\s/u.test(source[index - 1] || " ") && !/\d/u.test(source[index + 1] || "")) dollar = index;
      if (source.startsWith("\\)", index)) parenthesis = index;
    }
    dollars[index] = dollar;
    parentheses[index] = parenthesis;
  }
  return { dollars, parentheses };
}

/** Called only at the current Markdown cursor, never inside an emitted code/link. */
export function inlineMathAt(source, index, closers) {
  let open;
  let close;
  if (source.startsWith("\\(", index) && !escapedAt(source, index)) {
    open = "\\("; close = "\\)";
  } else if (source[index] === "$" && source[index + 1] !== "$"
    && source[index - 1] !== "$" && !escapedAt(source, index)
    && source[index + 1] && !/[\s\d]/u.test(source[index + 1])) {
    open = "$"; close = "$";
  } else return null;

  const end = (open === "$" ? closers.dollars : closers.parentheses)[index + open.length];
  if (end >= 0 && end - index - open.length <= assistantMathLimits.expressionChars) {
    const next = end + close.length;
    return { source: source.slice(index, next), expression: source.slice(index + open.length, end), end: next, display: false, complete: true };
  }
  // Explicit incomplete/oversized TeX stays inert rather than becoming emphasis
  // or losing backslashes. A dollar opener can instead be ordinary prose.
  if (open === "$") return null;
  return { source: source.slice(index), expression: "", end: source.length, display: false, complete: false };
}

export function isDisplayMathStart(line) {
  return /^\s{0,3}(?:\$\$|\\\[)/u.test(line);
}

/** Standalone display delimiters only; stop before a blank line or code fence. */
export function displayMathAt(lines, index) {
  const opener = lines[index].match(/^\s{0,3}(\$\$|\\\[)(.*)$/u);
  if (!opener) return null;
  const close = opener[1] === "$$" ? "$$" : "\\]";
  const parts = [];
  let size = 0;
  let consumed = index;
  for (let next = index; next < lines.length; next += 1) {
    const line = lines[next];
    if (next > index && (!line.trim() || /^\s*(`{3,}|~{3,})/u.test(line))) break;
    const value = next === index ? opener[2] : line;
    const trimmed = value.trimEnd();
    const end = trimmed.length - close.length;
    if (end >= 0 && trimmed.endsWith(close) && !escapedAt(trimmed, end)) {
      parts.push(trimmed.slice(0, end));
      return { source: lines.slice(index, next + 1).join("\n"), expression: parts.join("\n"), next: next + 1, display: true, complete: true };
    }
    parts.push(value);
    consumed = next + 1;
    size += line.length + 1;
    if (size > assistantMathLimits.expressionChars) break;
  }
  // Preserve the bounded incomplete block as source. Blank lines and code
  // fences still terminate it, keeping following paragraphs/code independent.
  return { source: lines.slice(index, consumed).join("\n"), expression: "", next: consumed, display: true, complete: false };
}

function boundedExpression(expression) {
  if (!expression.trim() || expression.length > assistantMathLimits.expressionChars
    || UNSAFE_COMMAND.test(expression) || /<\/?[A-Za-z][^>]*>/u.test(expression)) return false;
  let depth = 0;
  let nesting = 0;
  const commands = expression.match(/\\[A-Za-z]+/gu) || [];
  if (commands.length > 128 || commands.filter((command) => command === "\\sqrt" || command === "\\frac" || command === "\\left").length > assistantMathLimits.depth
    || (expression.match(/&|\\\\/gu) || []).length > 128
    || (expression.match(/\\begin/gu) || []).length > 8
    || /\\begin\s*\{array\}\s*\{[^}]{33}/u.test(expression)) return false;
  for (let index = 0; index < expression.length; index += 1) {
    if (expression[index] === "\\") { index += 1; continue; }
    if (expression[index] === "{") depth += 1;
    if (expression[index] === "}") depth -= 1;
    // Also cap nested sqrt/frac/left/array inputs lacking brace nesting.
    if (expression[index] === "[") nesting += 1;
    if (expression[index] === "]") nesting -= 1;
    if (depth < 0 || depth > assistantMathLimits.depth || nesting > assistantMathLimits.depth) return false;
  }
  return depth === 0;
}

/** One budget per response, shared by every inline/table/list/display hook. */
export function createAssistantMath(documentRef, { onSource } = {}) {
  let expressions = 0;
  let characters = 0;
  return (token) => {
    const wrapper = documentRef.createElement(token.display ? "div" : "span");
    wrapper.className = `assistant-math-expression${token.display ? " assistant-math-display" : ""}`;
    const fallback = () => {
      delete wrapper.dataset.mathSource;
      wrapper.classList.add("assistant-math-fallback");
      wrapper.textContent = token.source;
      return wrapper;
    };
    expressions += 1;
    characters += token.expression.length;
    if (!token.complete || expressions > assistantMathLimits.expressions
      || characters > assistantMathLimits.messageChars || !boundedExpression(token.expression)) return fallback();
    const output = documentRef.createElement("span");
    try {
      katex.render(token.expression, output, {
        displayMode: token.display,
        output: "mathml",
        trust: false,
        strict: "error",
        throwOnError: true,
        maxExpand: assistantMathLimits.expansions,
        maxSize: assistantMathLimits.sizeEm,
        macros: Object.create(null),
        globalGroup: false,
      });
      if (output.querySelectorAll("*").length > assistantMathLimits.outputNodes
        || output.querySelector("a, img, script, iframe, object, embed, [href], [src], [style*='url(']")) return fallback();
      // Annotation is metadata; expression source has its own explicit owner.
      output.querySelectorAll("annotation").forEach((node) => node.remove());
      const nativeMath = output.querySelector("math");
      if (!nativeMath || output.querySelectorAll("math").length !== 1) return fallback();
      wrapper.dataset.mathSource = token.source;
      // The passage owner validates one sole native MathML child. KaTeX's
      // implementation wrapper and annotations are deliberately not retained.
      wrapper.append(nativeMath);
      if (token.display) {
        wrapper.tabIndex = 0;
        wrapper.setAttribute("role", "group");
        wrapper.setAttribute("aria-label", "Mathematical expression");
      }
      onSource?.(token.source);
      return wrapper;
    } catch {
      return fallback();
    }
  };
}

/** Source actions belong outside message content, never in selectable prose. */
export function mathSourceActions(documentRef, sources, onCopy) {
  const actions = documentRef.createElement("div");
  actions.className = "message-actions assistant-math-actions";
  actions.setAttribute("aria-label", "Maths source actions");
  sources.forEach((source, index) => {
    const button = documentRef.createElement("button");
    button.type = "button";
    button.className = "composer-limits-button";
    button.textContent = sources.length === 1 ? "Copy maths source" : `Copy maths source ${index + 1}`;
    button.addEventListener("click", () => { void onCopy(source); });
    actions.append(button);
  });
  return actions;
}

export const assistantMathStyles = `
  .assistant-math-expression { display: inline-block; box-sizing: border-box; color: inherit; max-width: 100%; overflow-x: auto; vertical-align: middle; }
  .assistant-math-display { display: block; margin: 0.75em 0; padding: 0.2em; }
  .assistant-math-display:focus-visible { outline: 2px solid var(--focus-ring-contrast); outline-offset: 1px; box-shadow: 0 0 0 4px var(--focus-ring-color); }
  .assistant-math-fallback { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--code-font-family, monospace); }
`;
