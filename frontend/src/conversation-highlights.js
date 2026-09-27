import { casefoldCharacter } from "./unicode-casefold.js";

/** Literal Unicode case-fold matching with original UTF-16 DOM offsets. */
export function conversationMatchRanges(source, query, maximum = 400) {
  let folded = "";
  const starts = [];
  const ends = [];
  let offset = 0;
  for (const character of source) {
    const value = casefoldCharacter(character);
    folded += value;
    for (let index = 0; index < value.length; index += 1) {
      starts.push(offset); ends.push(offset + character.length);
    }
    offset += character.length;
  }
  const needle = Array.from(query, casefoldCharacter).join("");
  if (!needle) return [];
  const ranges = [];
  let cursor = 0;
  while (ranges.length < maximum) {
    const match = folded.indexOf(needle, cursor);
    if (match < 0) break;
    const range = { start: starts[match], end: ends[match + needle.length - 1] };
    if (!ranges.length || range.start >= ranges.at(-1).end) ranges.push(range);
    cursor = match + needle.length;
  }
  return ranges;
}

export function highlightedConversationText(documentRef, text, query) {
  const fragment = documentRef.createDocumentFragment();
  let cursor = 0;
  for (const match of conversationMatchRanges(text, query)) {
    fragment.append(documentRef.createTextNode(text.slice(cursor, match.start)));
    const mark = documentRef.createElement("mark");
    mark.textContent = text.slice(match.start, match.end);
    fragment.append(mark);
    cursor = match.end;
  }
  fragment.append(documentRef.createTextNode(text.slice(cursor)));
  return fragment;
}

/** Remove only search wrappers; preserve renderer elements and their listeners. */
export function clearConversationHighlights(root) {
  for (const mark of root.querySelectorAll("mark.conversation-text-match")) {
    const parent = mark.parentNode;
    mark.replaceWith(root.ownerDocument.createTextNode(mark.textContent));
    parent.normalize();
  }
}

/** Highlight rendered public text across inline markup, with bounded DOM work. */
export function highlightConversationMessage(root, query) {
  clearConversationHighlights(root);
  const documentRef = root.ownerDocument;
  const walker = documentRef.createTreeWalker(root, 4);
  const nodes = [];
  let text = "";
  let node;
  while ((node = walker.nextNode()) && nodes.length < 8000 && text.length < 262144) {
    if (node.parentElement.closest("button, .code-head, .message-state, .assistant-math-expression, math, [hidden], [aria-hidden='true']")) continue;
    const value = node.data.slice(0, 262144 - text.length);
    nodes.push({ node, start: text.length, end: text.length + value.length });
    text += value;
  }
  const ranges = conversationMatchRanges(text, query);
  let rangeIndex = 0;
  let marks = 0;
  for (const entry of nodes) {
    while (rangeIndex < ranges.length && ranges[rangeIndex].end <= entry.start) rangeIndex += 1;
    if (rangeIndex >= ranges.length) break;
    const fragment = documentRef.createDocumentFragment();
    let cursor = 0;
    let index = rangeIndex;
    while (index < ranges.length && ranges[index].start < entry.end && marks < 1000) {
      const start = Math.max(0, ranges[index].start - entry.start);
      const end = Math.min(entry.end, ranges[index].end) - entry.start;
      fragment.append(documentRef.createTextNode(entry.node.data.slice(cursor, start)));
      const mark = documentRef.createElement("mark");
      mark.className = "conversation-text-match";
      mark.textContent = entry.node.data.slice(start, end);
      fragment.append(mark);
      cursor = end; index += 1; marks += 1;
    }
    if (cursor) {
      fragment.append(documentRef.createTextNode(entry.node.data.slice(cursor)));
      entry.node.replaceWith(fragment);
    }
  }
  return marks;
}
