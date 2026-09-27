const MAX_HIGHLIGHT_CHARS = 64 * 1024;
const MAX_HIGHLIGHT_LINES = 1_600;
const MAX_HIGHLIGHT_TOKENS = 4_000;

const LANGUAGE_ALIASES = new Map([
  ["js", "javascript"], ["jsx", "javascript"], ["mjs", "javascript"],
  ["ts", "typescript"], ["tsx", "typescript"],
  ["py", "python"], ["shell", "bash"], ["sh", "bash"], ["zsh", "bash"],
  ["yml", "yaml"], ["html", "html"], ["xml", "html"],
]);

const KEYWORDS = {
  javascript: new Set("async await break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield".split(" ")),
  typescript: new Set("abstract any as async await boolean break case catch class const constructor continue declare default delete do else enum export extends finally for from function get if implements import in infer instanceof interface is keyof let namespace never new number object of private protected public readonly require return set static string super switch this throw type typeof undefined unique unknown var void while with yield".split(" ")),
  python: new Set("and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split(" ")),
  bash: new Set("case coproc do done elif else esac eval exec fi for function if in local printf read return select set shift source then time trap until wait while".split(" ")),
  json: new Set(["true", "false", "null"]),
  css: new Set(["important", "inherit", "initial", "unset"]),
  yaml: new Set(["true", "false", "null", "yes", "no"]),
  sql: new Set("add all alter and as asc begin between by case cast check column constraint create cross current_date current_time database default delete desc distinct drop else end exists false fetch for from full grant group having in index inner insert into is join key left like limit not null offset on or order outer primary references right rollback schema select set table then true truncate union unique update values view when where with".split(" ")),
};

const SUPPORTED_LANGUAGES = new Set(Object.keys(KEYWORDS));
const IDENTIFIER_START = /[A-Za-z_$]/u;
const IDENTIFIER_PART = /[A-Za-z0-9_$]/u;
const NUMBER = /^(?:0[xob][\da-f]+|(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?n?)/iu;

function normaliseLanguage(language) {
  const value = String(language || "").trim().toLowerCase();
  return LANGUAGE_ALIASES.get(value) || value;
}

function tokenise(code, language) {
  const keywords = KEYWORDS[language];
  const segments = [];
  let highlighted = 0;
  let plainStart = 0;
  let index = 0;
  let blockComment = false;

  const emit = (end, kind = "") => {
    if (end <= index) return;
    if (plainStart < index) segments.push({ text: code.slice(plainStart, index), kind: "" });
    segments.push({ text: code.slice(index, end), kind });
    if (kind) highlighted += 1;
    index = end;
    plainStart = end;
  };

  while (index < code.length) {
    if (highlighted > MAX_HIGHLIGHT_TOKENS) return { segments: [], highlighted };
    const character = code[index];
    if (character === "\n" || character === "\r") {
      if (plainStart < index) segments.push({ text: code.slice(plainStart, index), kind: "" });
      const end = index + (character === "\r" && code[index + 1] === "\n" ? 2 : 1);
      segments.push({ text: code.slice(index, end), kind: "" });
      index = end;
      plainStart = index;
      continue;
    }

    if (blockComment) {
      const close = code.indexOf("*/", index);
      const end = close < 0 ? code.length : close + 2;
      emit(end, "comment");
      blockComment = close < 0;
      continue;
    }

    const pair = code.slice(index, index + 2);
    const lineComment = pair === "//" && ["javascript", "typescript", "css"].includes(language)
      || pair === "--" && language === "sql"
      || character === "#" && ["python", "bash", "yaml"].includes(language);
    if (lineComment) {
      const newline = code.slice(index).search(/[\r\n]/u);
      emit(newline < 0 ? code.length : index + newline, "comment");
      continue;
    }
    if (pair === "/*" && ["javascript", "typescript", "css", "sql"].includes(language)) {
      blockComment = true;
      continue;
    }

    if (character === "'" || character === '"' || (character === "`" && ["javascript", "typescript"].includes(language))) {
      const quote = character;
      let end = index + 1;
      while (end < code.length) {
        if (code[end] === "\\") { end = Math.min(code.length, end + 2); continue; }
        if (code[end] === quote) { end += 1; break; }
        if ((code[end] === "\n" || code[end] === "\r") && quote !== "`") break;
        end += 1;
      }
      emit(end, "string");
      continue;
    }

    if (/[0-9]/u.test(character) || character === "." && /[0-9]/u.test(code[index + 1] || "")) {
      const match = code.slice(index).match(NUMBER)?.[0];
      if (match) { emit(index + match.length, "number"); continue; }
    }

    if (IDENTIFIER_START.test(character)) {
      let end = index + 1;
      while (end < code.length && IDENTIFIER_PART.test(code[end])) end += 1;
      const word = code.slice(index, end);
      if (keywords.has(word)) {
        emit(end, "keyword");
      } else if (["javascript", "typescript", "python", "json", "yaml"].includes(language)
        && ["true", "false", "null", "undefined", "None", "True", "False"].includes(word)) {
        emit(end, "literal");
      } else {
        index = end;
      }
      continue;
    }
    index += 1;
  }

  if (plainStart < code.length) segments.push({ text: code.slice(plainStart), kind: "" });
  return { segments, highlighted };
}

function appendSegments(documentRef, parent, segments) {
  for (const segment of segments) {
    if (!segment.text) continue;
    if (!segment.kind) {
      parent.append(documentRef.createTextNode(segment.text));
      continue;
    }
    const token = documentRef.createElement("span");
    token.className = `code-token-${segment.kind}`;
    token.textContent = segment.text;
    parent.append(token);
  }
}

function renderLines(documentRef, codeText, segments, lineNumbers) {
  const byLine = [];
  let current = [];
  for (const segment of segments) {
    const pieces = segment.text.split(/(\r\n|\r|\n)/u);
    pieces.forEach((piece, index) => {
      if (index % 2) { byLine.push({ segments: current, ending: piece }); current = []; }
      else if (piece) current.push({ text: piece, kind: segment.kind });
    });
  }
  byLine.push({ segments: current, ending: "" });

  byLine.forEach((line, index) => {
    const lineNode = documentRef.createElement("span");
    lineNode.className = "code-line";
    if (lineNumbers) lineNode.dataset.lineNumber = String(index + 1);
    appendSegments(documentRef, lineNode, line.segments);
    codeText.append(lineNode);
    if (line.ending) codeText.append(documentRef.createTextNode(line.ending));
  });
}

/**
 * Build a bounded, inert code block for the panel's Markdown code callback.
 * The caller owns clipboard access and receives the exact source in onCopy.
 */
export function renderCodeBlock(documentRef, source, language = "", {
  onCopy,
  lineNumbers = false,
  wrap = false,
} = {}) {
  if (!documentRef?.createElement) throw new TypeError("A document is required");
  const code = String(source ?? "");
  const displayLanguage = String(language || "code").slice(0, 32);
  const normalizedLanguage = normaliseLanguage(displayLanguage);
  let lineCount = 1;
  for (let index = 0; index < code.length && lineCount <= MAX_HIGHLIGHT_LINES; index += 1) {
    if (code[index] === "\r") {
      lineCount += 1;
      if (code[index + 1] === "\n") index += 1;
    } else if (code[index] === "\n") lineCount += 1;
  }
  const canNumberLines = lineCount <= MAX_HIGHLIGHT_LINES;
  const mayHighlight = SUPPORTED_LANGUAGES.has(normalizedLanguage)
    && code.length <= MAX_HIGHLIGHT_CHARS
    && canNumberLines;
  let segments = [{ text: code, kind: "" }];
  let isHighlighted = false;
  if (mayHighlight) {
    const result = tokenise(code, normalizedLanguage);
    if (result.highlighted <= MAX_HIGHLIGHT_TOKENS) {
      segments = result.segments;
      isHighlighted = result.highlighted > 0;
    }
  }

  const block = documentRef.createElement("div");
  block.className = "code-block";
  block.dataset.language = normalizedLanguage || "text";
  block.dataset.highlighted = String(isHighlighted);
  const head = documentRef.createElement("div");
  head.className = "code-head";
  const label = documentRef.createElement("span");
  label.className = "code-language";
  label.textContent = displayLanguage;
  head.append(label);

  const controls = documentRef.createElement("div");
  controls.className = "code-controls";
  const copy = documentRef.createElement("button");
  copy.type = "button";
  copy.className = "copy-button";
  copy.textContent = "Copy code";
  copy.setAttribute("aria-label", "Copy original code");
  copy.disabled = typeof onCopy !== "function";
  if (typeof onCopy === "function") {
    copy.addEventListener("click", () => { void onCopy(code); });
  }
  controls.append(copy);

  const numberToggle = documentRef.createElement("button");
  numberToggle.type = "button";
  numberToggle.className = "code-toggle";
  numberToggle.textContent = "Line numbers";
  numberToggle.setAttribute("aria-pressed", String(Boolean(lineNumbers && canNumberLines)));
  numberToggle.disabled = !canNumberLines;
  const wrapToggle = documentRef.createElement("button");
  wrapToggle.type = "button";
  wrapToggle.className = "code-toggle";
  wrapToggle.textContent = "Wrap lines";
  wrapToggle.setAttribute("aria-pressed", String(Boolean(wrap)));
  controls.append(numberToggle, wrapToggle);
  head.append(controls);

  const pre = documentRef.createElement("pre");
  pre.className = "code-text";
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", `${displayLanguage} code block`);
  if (wrap) pre.classList.add("is-wrapped");
  if (lineNumbers && canNumberLines) pre.classList.add("has-line-numbers");
  if (isHighlighted) pre.dataset.highlighted = "true";
  if (canNumberLines) {
    renderLines(documentRef, pre, segments, Boolean(lineNumbers));
  } else {
    // A single text node keeps extreme line counts cheap while preserving copy.
    pre.textContent = code;
  }
  numberToggle.addEventListener("click", () => {
    const enabled = numberToggle.getAttribute("aria-pressed") !== "true";
    numberToggle.setAttribute("aria-pressed", String(enabled));
    pre.classList.toggle("has-line-numbers", enabled);
    pre.querySelectorAll(".code-line").forEach((line, index) => {
      if (enabled) line.dataset.lineNumber = String(index + 1);
      else delete line.dataset.lineNumber;
    });
  });
  wrapToggle.addEventListener("click", () => {
    const enabled = wrapToggle.getAttribute("aria-pressed") !== "true";
    wrapToggle.setAttribute("aria-pressed", String(enabled));
    pre.classList.toggle("is-wrapped", enabled);
  });
  block.append(head, pre);
  return block;
}

export const codeBlockLimits = Object.freeze({
  maxHighlightChars: MAX_HIGHLIGHT_CHARS,
  maxHighlightLines: MAX_HIGHLIGHT_LINES,
  maxHighlightTokens: MAX_HIGHLIGHT_TOKENS,
});
