import { sanitizeUrl } from "./safe-dom.js";
import { createAssistantMath, inlineMathAt, inlineMathIndex, displayMathAt, isDisplayMathStart, assistantMathStyles } from "./assistant-math.js";

const MAX_MARKDOWN_LENGTH = 200_000;

function element(document, name, text) {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeLink(document, destination, label) {
  const base = document.defaultView?.location?.origin;
  const target = destination.startsWith("<") && destination.endsWith(">")
    ? destination.slice(1, -1)
    : destination;
  if (/^(?:file|sandbox):/iu.test(target)
    || /^(?:[a-z]:[\\/]|\\\\|\/\/)/iu.test(target)
    || /^\/(?:config\/workspaces|workspace)(?:\/|$)/iu.test(target)) return null;
  let input;
  try { input = new URL(target, base || "http://ha.invalid"); } catch { return null; }
  if (input.username || input.password) return null;
  const href = sanitizeUrl(target, { base, allowRemote: true });
  if (!href) return null;
  const parsed = new URL(href);
  const currentOrigin = base ? new URL(base).origin : null;
  if (parsed.username || parsed.password || (parsed.protocol === "http:" && parsed.origin !== currentOrigin)) return null;

  const link = element(document, "a", label);
  link.href = href;
  if (parsed.origin !== currentOrigin) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  return link;
}

function appendText(document, parent, value) {
  if (value) parent.append(document.createTextNode(value));
}

function renderInline(document, parent, source, math) {
  const length = source.length;
  const mathClosers = inlineMathIndex(source);
  const escaped = new Uint8Array(length);
  let slashCount = 0;
  for (let index = 0; index < length; index += 1) {
    escaped[index] = slashCount % 2;
    slashCount = source[index] === "\\" ? slashCount + 1 : 0;
  }

  const tracked = ["[", "]", "(", ")", "`", "*", "_", "~", "\n"];
  const nextAt = Object.create(null);
  for (const character of tracked) {
    const next = new Int32Array(length + 1);
    next.fill(-1);
    let nearest = -1;
    for (let index = length - 1; index >= 0; index -= 1) {
      if (source[index] === character && !escaped[index]) nearest = index;
      next[index] = nearest;
    }
    nextAt[character] = next;
  }

  const tickRuns = [];
  for (let index = 0; index < length;) {
    if (source[index] !== "`" || escaped[index]) { index += 1; continue; }
    let end = index + 1;
    while (end < length && source[end] === "`") end += 1;
    tickRuns.push({ start: index, width: end - index });
    index = end;
  }
  const nextTickRun = new Map();
  const closestTickByWidth = new Map();
  for (let index = tickRuns.length - 1; index >= 0; index -= 1) {
    const run = tickRuns[index];
    nextTickRun.set(run.start, closestTickByWidth.get(run.width) ?? -1);
    closestTickByWidth.set(run.width, run.start);
  }

  const plain = [];
  let plainStart = 0;
  let index = 0;
  const emitToken = (end, node, text) => {
    const before = source.slice(plainStart, index);
    appendText(document, parent, before);
    if (before) plain.push(before);
    parent.append(node);
    plain.push(text);
    index = end;
    plainStart = end;
  };

  while (index < length) {
    const equation = inlineMathAt(source, index, mathClosers);
    if (equation) {
      emitToken(equation.end, math(equation), equation.source);
      continue;
    }
    if (source[index] === "\\" && index + 1 < length && "\\`*_{}[]()#+-.!>~|".includes(source[index + 1])) {
      const escapedText = source[index + 1];
      emitToken(index + 2, document.createTextNode(escapedText), escapedText);
      continue;
    }

    const image = source[index] === "!" && source[index + 1] === "[" && !escaped[index];
    const openBracket = image ? index + 1 : index;
    if ((source[index] === "[" || image) && !escaped[openBracket]) {
      const closeBracket = nextAt["]"][openBracket + 1];
      const newline = nextAt["\n"][openBracket + 1];
      if (closeBracket > openBracket && (newline < 0 || newline > closeBracket) && source[closeBracket + 1] === "(") {
        const closeParen = nextAt[")"][closeBracket + 2];
        if (closeParen > closeBracket + 2) {
          const label = source.slice(openBracket + 1, closeBracket);
          const destinationText = source.slice(closeBracket + 2, closeParen).trim();
          const destinationMatch = destinationText.match(/^([^\s]+)(?:\s+"([^"\n]*)")?$/u);
          if (destinationMatch) {
            const destination = destinationMatch[1];
            const title = destinationMatch[2];
            if (image) {
              // Images in assistant text must not bypass the authenticated, bounded HA image flow.
              const alt = label || "Image";
              emitToken(closeParen + 1, document.createTextNode(alt), alt);
              continue;
            }
            const link = safeLink(document, destination, label);
            if (link) {
              if (title) link.title = title;
              emitToken(closeParen + 1, link, `${label} (${destination})`);
            } else {
              const text = `${label} (${destination})`;
              emitToken(closeParen + 1, document.createTextNode(text), text);
            }
            continue;
          }
          const text = `${label} (${destinationText})`;
          emitToken(closeParen + 1, document.createTextNode(text), text);
          continue;
        }
      }
    }

    if (source[index] === "`" && !escaped[index]) {
      let runEnd = index + 1;
      while (runEnd < length && source[runEnd] === "`") runEnd += 1;
      const closeRun = nextTickRun.get(index) ?? -1;
      if (closeRun > index) {
        const closeEnd = closeRun + (runEnd - index);
        const code = source.slice(runEnd, closeRun);
        emitToken(closeEnd, element(document, "code", code), code);
        continue;
      }
    }

    const marker = source[index];
    if ((marker === "*" || marker === "_" || marker === "~") && !escaped[index]) {
      const width = marker === "~" ? 2 : source[index + 1] === marker ? 2 : 1;
      if (width === 2 && source[index + 1] !== marker) { index += 1; continue; }
      const close = nextAt[marker][index + width];
      if (close > index + width && (width === 1 || source[close + 1] === marker)) {
        const end = close + width;
        const content = source.slice(index + width, close);
        const tag = marker === "~" ? "del" : width === 2 ? "strong" : "em";
        emitToken(end, element(document, tag, content), content);
        continue;
      }
    }
    index += 1;
  }

  appendText(document, parent, source.slice(plainStart));
  plain.push(source.slice(plainStart));
  return plain.join("");
}

function isTableDelimiter(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/u.test(line);
}

function tableCells(line) {
  const value = line.trim().replace(/^\|/u, "").replace(/\|$/u, "");
  // Inline parsing owns escaped pipes, allowing maths source to retain \|.
  return value.split(/(?<!\\)\|/u).map((cell) => cell.trim());
}

function appendTable(document, parent, lines, math) {
  const rows = lines.filter(Boolean).map(tableCells);
  const plainRows = [];
  const table = element(document, "table");
  table.className = "assistant-markdown-table";
  const head = element(document, "thead");
  const headingRow = element(document, "tr");
  plainRows.push([]);
  for (const value of rows[0] || []) {
    const cell = element(document, "th");
    cell.scope = "col";
    plainRows[0].push(renderInline(document, cell, value, math));
    headingRow.append(cell);
  }
  head.append(headingRow);
  table.append(head);
  const body = element(document, "tbody");
  for (const row of rows.slice(1)) {
    const tr = element(document, "tr");
    const plainRow = [];
    for (let index = 0; index < (rows[0]?.length || row.length); index += 1) {
      const cell = element(document, "td");
      plainRow.push(renderInline(document, cell, row[index] || "", math));
      tr.append(cell);
    }
    plainRows.push(plainRow);
    body.append(tr);
  }
  table.append(body);
  parent.append(table);
  return plainRows.map((row) => row.join("\t")).join("\n");
}

function fenceInfo(value) {
  const match = value.trim().match(/^([\w.+-]*)/u);
  return match?.[1] || "";
}

function sourceLines(source) {
  const lines = [];
  const pattern = /([^\r\n]*)(\r\n|\r|\n|$)/gu;
  let match;
  while ((match = pattern.exec(source)) && match[0]) {
    lines.push({ text: match[1], start: match.index, end: pattern.lastIndex });
  }
  return lines;
}

function fencedBody(source, lines, index) {
  const fence = lines[index].text.match(/^\s*(`{3,}|~{3,})(.*)$/u);
  if (!fence) return null;
  const start = lines[index].end;
  const close = new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`, "u");
  let next = index + 1;
  while (next < lines.length && !close.test(lines[next].text)) next += 1;
  const bodyEnd = next < lines.length ? lines[next].start : source.length;
  return {
    start: lines[index].start,
    end: next < lines.length ? lines[next].end : source.length,
    code: source.slice(start, bodyEnd),
    language: fenceInfo(fence[2]),
    next: next < lines.length ? next + 1 : next,
  };
}

/** Exact source ranges for fenced code; prose remains the caller's responsibility. */
export function fencedCodeParts(source) {
  const original = String(source ?? "");
  const lines = sourceLines(original);
  const parts = [];
  for (let index = 0; index < lines.length;) {
    const fence = fencedBody(original, lines, index);
    if (fence) { parts.push(fence); index = fence.next; }
    else index += 1;
  }
  return parts;
}

/**
 * Render a bounded subset of assistant Markdown using DOM nodes only.
 * `createCodeBlock` may return the panel's existing fenced-code node, including
 * its copy action. It receives (document, exactSourceCode, language), preserving
 * original line endings without adding a newline to an unfinished fence.
 */
export function renderAssistantMarkdown(document, source, { createCodeBlock, onMathSource } = {}) {
  if (!document?.createDocumentFragment) throw new TypeError("A document is required");
  const original = String(source ?? "");
  let formattedLength = Math.min(original.length, MAX_MARKDOWN_LENGTH);
  if (formattedLength < original.length
    && original.charCodeAt(formattedLength - 1) >= 0xd800 && original.charCodeAt(formattedLength - 1) <= 0xdbff
    && original.charCodeAt(formattedLength) >= 0xdc00 && original.charCodeAt(formattedLength) <= 0xdfff) {
    formattedLength += 1;
  }
  const markdown = original.slice(0, formattedLength);
  const overflow = original.slice(formattedLength);
  const rawLines = sourceLines(markdown);
  const lines = rawLines.map((line) => line.text);
  const math = createAssistantMath(document, { onSource: onMathSource });
  const mathSourceLines = [...markdown.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/gu)].filter((match) => match[0]);
  const fragment = document.createDocumentFragment();
  const plainParts = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    const fence = fencedBody(markdown, rawLines, index);
    if (fence) {
      index = fence.next;
      const { code, language } = fence;
      const node = createCodeBlock?.(document, code, language) || (() => {
        const pre = element(document, "pre");
        pre.className = "assistant-markdown-code";
        const codeNode = element(document, "code", code);
        if (language) codeNode.dataset.language = language;
        pre.append(codeNode);
        return pre;
      })();
      fragment.append(node);
      plainParts.push(code);
      continue;
    }

    const displayEquation = displayMathAt(lines, index);
    if (displayEquation) {
      const first = mathSourceLines[index];
      const last = mathSourceLines[displayEquation.next - 1];
      if (first && last) {
        displayEquation.source = markdown.slice(first.index, last.index + last[0].replace(/(?:\r\n|\r|\n)$/u, "").length).trim();
      }
      fragment.append(math(displayEquation));
      plainParts.push(displayEquation.source);
      index = displayEquation.next;
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") && isTableDelimiter(lines[index + 1])) {
      const tableLines = [line];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) tableLines.push(lines[index++]);
      plainParts.push(appendTable(document, fragment, tableLines, math));
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/u);
    if (heading) {
      const node = element(document, `h${heading[1].length}`);
      node.className = "assistant-markdown-heading";
      const headingText = renderInline(document, node, heading[2], math);
      fragment.append(node);
      plainParts.push(headingText);
      index += 1;
      continue;
    }

    const listMatch = line.match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/u);
    if (listMatch) {
      const ordered = /^\d/u.test(listMatch[1]);
      const list = element(document, ordered ? "ol" : "ul");
      list.className = "assistant-markdown-list";
      const listPlain = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/u);
        if (!itemMatch || /^\d/u.test(itemMatch[1]) !== ordered) break;
        const item = element(document, "li");
        const itemText = renderInline(document, item, itemMatch[2], math);
        list.append(item);
        listPlain.push(`${ordered ? `${list.children.length}.` : "•"} ${itemText}`);
        index += 1;
      }
      fragment.append(list);
      plainParts.push(listPlain.join("\n"));
      continue;
    }

    if (/^\s*>/u.test(line)) {
      const quote = element(document, "blockquote");
      quote.className = "assistant-markdown-quote";
      const quotePlain = [];
      while (index < lines.length && /^\s*>/u.test(lines[index])) {
        const text = lines[index++].replace(/^\s*>\s?/u, "");
        const paragraph = element(document, "p");
        const paragraphText = renderInline(document, paragraph, text, math);
        quote.append(paragraph);
        quotePlain.push(paragraphText);
      }
      fragment.append(quote);
      plainParts.push(quotePlain.join("\n"));
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^\s{0,3}(?:#{1,6}\s|```|~~~|>|[-+*]\s|\d+[.)]\s)/u.test(lines[index])) {
      if (isDisplayMathStart(lines[index])) break;
      if (index + 1 < lines.length && lines[index].includes("|") && isTableDelimiter(lines[index + 1])) break;
      paragraphLines.push(lines[index++]);
    }
    const paragraph = element(document, "p");
    paragraph.className = "assistant-markdown-paragraph";
    const paragraphText = paragraphLines.map((part, lineIndex) => {
      if (lineIndex) paragraph.append(element(document, "br"));
      return renderInline(document, paragraph, part, math);
    });
    fragment.append(paragraph);
    plainParts.push(paragraphText.join("\n"));
  }

  if (overflow) {
    const notice = element(document, "p", "The rest of this response is shown as plain text because it is too long to format safely.");
    notice.className = "assistant-markdown-overflow-notice";
    const remainder = element(document, "pre", overflow);
    remainder.className = "assistant-markdown-overflow";
    fragment.append(notice, remainder);
    plainParts.push(overflow);
  }

  // DocumentFragment remains directly appendable while exposing a text-only
  // representation to callers that provide whole-message copy controls.
  fragment.plainText = plainParts.filter(Boolean).join("\n\n");
  return fragment;
}

export const assistantMarkdownMaxLength = MAX_MARKDOWN_LENGTH;

export const assistantMarkdownStyles = `
  ${assistantMathStyles}
  .assistant-markdown-paragraph { margin: 0 0 0.8em; overflow-wrap: anywhere; }
  .assistant-markdown-paragraph:last-child { margin-bottom: 0; }
  .assistant-markdown-heading { margin: 1.1em 0 0.45em; line-height: 1.25; overflow-wrap: anywhere; }
  .assistant-markdown-heading:first-child { margin-top: 0; }
  .assistant-markdown-list { margin: 0.5em 0 0.85em; padding-inline-start: 1.5em; }
  .assistant-markdown-list li + li { margin-top: 0.25em; }
  .assistant-markdown-quote { margin: 0.65em 0; padding-inline-start: 0.9em; border-inline-start: 3px solid var(--border-color); color: var(--muted-color); }
  .assistant-markdown-quote p { margin: 0.35em 0; }
  .assistant-markdown-table { display: block; width: max-content; max-width: 100%; margin: 0.75em 0; border-collapse: collapse; overflow-x: auto; }
  .assistant-markdown-table th, .assistant-markdown-table td { padding: 0.45em 0.65em; border: 1px solid var(--border-color); text-align: start; vertical-align: top; overflow-wrap: anywhere; }
  .assistant-markdown-table th { background: var(--surface-muted); }
  .assistant-markdown-code { max-width: 100%; margin: 0.75em 0; padding: 0.8em; overflow: auto; border: 1px solid var(--border-color); border-radius: 8px; background: var(--surface-muted); white-space: pre; }
  .assistant-markdown-code code, .assistant-markdown-paragraph code { font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace); }
  .assistant-markdown-paragraph code { padding: 0.08em 0.3em; border-radius: 4px; background: var(--surface-muted); overflow-wrap: anywhere; }
  .assistant-markdown-paragraph a { overflow-wrap: anywhere; }
  .assistant-markdown-overflow-notice { margin: 0.75em 0 0.35em; color: var(--muted-color); font-size: var(--font-caption-size); }
  .assistant-markdown-overflow { max-width: 100%; max-height: 32rem; margin: 0; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
  @media (max-width: 600px) { .assistant-markdown-table { max-width: 100%; } .assistant-markdown-table th, .assistant-markdown-table td { padding: 0.35em 0.45em; } }
`;
