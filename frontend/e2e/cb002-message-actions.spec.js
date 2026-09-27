import { mkdir, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";

const evidence = process.env.CB002_EVIDENCE_DIR || resolve(tmpdir(), "codex-bridge-cb002-browser");
let server;
let origin;
let bundle;

test.beforeAll(async () => {
  await mkdir(evidence, { recursive: true });
  const result = await build({ absWorkingDir: process.cwd(), entryPoints: ["frontend/src/codex-bridge-panel.js"],
    bundle: true, format: "esm", platform: "browser", target: "es2022", loader: { ".css": "text" }, write: false });
  bundle = result.outputFiles[0].text;
  await writeFile(resolve(evidence, "panel-source.js"), bundle);
  server = createServer((request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Content-Type", request.url === "/panel.js" ? "text/javascript" : "text/html");
    response.end(request.url === "/panel.js" ? bundle : '<!doctype html><meta name="viewport" content="width=device-width"><style>body{margin:0}codex-bridge-panel{display:block;height:100vh}</style><script type="module" src="/panel.js"></script>');
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => { if (server) await new Promise((done) => server.close(done)); });

async function setup(page) {
  await page.goto(origin);
  await page.waitForFunction(() => customElements.get("codex-bridge-panel"));
  await page.evaluate(() => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "cb002";
    panel._activeThread = { thread_id: "cb002", title: "Passage chat", status: "idle", mode: "edit", attachments: [] };
    panel._status = { auth: { state: "ok", auth_required: false }, account: { available: true } };
    panel._config = { capabilities: [] };
    window.cb002Calls = [];
    window.cb002ClipboardWrites = [];
    const writeClipboard = panel._writeClipboardText.bind(panel);
    panel._writeClipboardText = async (text) => {
      window.cb002ClipboardWrites.push(text);
      if (window.cb002MathEndpoints) {
        const range = window.getSelection().getComposedRanges({ shadowRoots: [panel.shadowRoot] })[0];
        const original = window.cb002MathEndpoints;
        window.cb002MathAtWrite = { sameStart: range.startContainer === original.start, startOffset: range.startOffset,
          sameEnd: range.endContainer === original.end, endOffset: range.endOffset, selected: window.getSelection().toString() };
      }
      await writeClipboard(text);
    };
    panel._callWS = async (...args) => { window.cb002Calls.push(args); return {}; };
    panel._renderComposerState(panel._activeThread);
    panel._events = [
      { event_type: "message.completed", sequence: 101, payload: { text: "café **👩🏽‍💻** and é\n\n第二段  end" } },
      { event_type: "message.completed", sequence: 102, payload: { text: "Neighbour must stay out" } },
      { event_type: "tool.output", sequence: 103, payload: { text: "Hidden tool context must stay out" } },
    ];
    panel._renderMessages();
    // The focused fixture uses actual panel renderers/actions, without asynchronous
    // HA harness polling replacing injected messages during an interaction.
  });
  return page.locator('codex-bridge-panel .message[data-sequence="101"]');
}

async function selectPassage(page, crossMessage = false) {
  return page.evaluate((cross) => {
    const root = document.querySelector("codex-bridge-panel").shadowRoot;
    const content = root.querySelector('.message[data-sequence="101"] .message-content');
    const range = document.createRange();
    range.selectNodeContents(content);
    if (cross) range.setEnd(root.querySelector('.message[data-sequence="102"] .message-content p').firstChild, 5);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return selection.toString();
  }, crossMessage);
}

for (const touch of [false, true]) {
  test(`exact multi-block Unicode passage survives ${touch ? "touch" : "keyboard"} action activation`, async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: touch, viewport: { width: touch ? 390 : 1280, height: 844 } });
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const page = await context.newPage();
    try {
      const article = await setup(page);
      const selected = await selectPassage(page);
      expect(selected).toBe("café 👩🏽‍💻 and é\n\n第二段 end");
      const copy = article.getByRole("button", { name: "Copy passage", exact: true });
      if (touch) await copy.tap();
      else { await copy.focus(); await copy.press("Enter"); }
      expect(await page.evaluate(() => window.cb002ClipboardWrites)).toEqual([selected]);
      const clipboard = await page.evaluate(() => navigator.clipboard.readText());
      await writeFile(resolve(evidence, touch ? "touch-clipboard.json" : "keyboard-clipboard.json"), JSON.stringify({ selected, clipboard }));
      // Windows stores native clipboard line separators as CRLF. The action's
      // exact input is asserted above; only OS clipboard readback is normalised.
      expect(clipboard.replace(/\r\n/gu, "\n")).toBe(selected);
      const input = page.locator("codex-bridge-panel #prompt-input");
      await input.fill("Draft stays editable");
      await selectPassage(page);
      const quote = article.getByRole("button", { name: "Quote passage", exact: true });
      if (touch) await quote.tap();
      else { await quote.focus(); await quote.press("Space"); }
      await expect(input).toHaveValue(`Draft stays editable\n\nAssistant response in “Passage chat”:\n${selected.split("\n").map((line) => `> ${line}`).join("\n")}`);
      await input.press("End");
      await input.type(" edited");
      expect(await page.evaluate(() => window.cb002Calls)).toEqual([]);
      await page.screenshot({ path: resolve(evidence, touch ? "touch.png" : "keyboard.png") });
    } finally { await context.close(); }
  });
}

test("original public source copy excludes adjacent turns; invalid cross-message passage leaves clipboard and draft unchanged", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const article = await setup(page);
  await article.getByRole("button", { name: "Copy message", exact: true }).click();
  const original = "café **👩🏽‍💻** and é\n\n第二段  end";
  expect(await page.evaluate(() => window.cb002ClipboardWrites)).toEqual([original]);
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/gu, "\n"))).toBe(original);
  await selectPassage(page, true);
  await article.getByRole("button", { name: "Copy passage", exact: true }).click();
  expect(await page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/gu, "\n"))).toBe(original);
  expect(await page.evaluate(() => window.cb002ClipboardWrites)).toEqual([original]);
  await selectPassage(page, true);
  await article.getByRole("button", { name: "Quote passage", exact: true }).click();
  await expect(page.locator("codex-bridge-panel #prompt-input")).toHaveValue("");
  expect(await page.evaluate(() => window.cb002Calls)).toEqual([]);
});

test("maths passages use captured source, preserve native block separators and restore composed ranges", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const article = await setup(page);
  const before = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel").shadowRoot;
    const content = root.querySelector('.message[data-sequence="101"] .message-content');
    content.innerHTML = '<p>x before</p><div class="assistant-math-expression" data-math-source="$$x$$"><math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><semantics><mi>x</mi><annotation hidden encoding="application/x-tex">annotation never copied</annotation></semantics></math></div><p>after x café é</p>';
    const range = document.createRange(); range.selectNodeContents(content);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    const composed = selection.getComposedRanges({ shadowRoots: [root] })[0];
    window.cb002MathEndpoints = { start: composed.startContainer, startOffset: composed.startOffset, end: composed.endContainer, endOffset: composed.endOffset };
    const read = (part) => { selection.removeAllRanges(); selection.addRange(part); return selection.toString(); };
    const prefix = range.cloneRange(); prefix.setEndBefore(content.children[1]);
    const suffix = range.cloneRange(); suffix.setStartAfter(content.children[1]);
    const nativePrefix = read(prefix); const nativeSuffix = read(suffix);
    const through = range.cloneRange(); through.setEndAfter(content.children[1]);
    const nativeThrough = read(through);
    const inner = range.cloneRange(); inner.setEnd(content.children[1], content.children[1].childNodes.length);
    const nativeInner = read(inner);
    const math = range.cloneRange(); math.selectNodeContents(content.children[1]);
    const nativeMath = read(math);
    selection.removeAllRanges(); selection.addRange(range);
    return { nativePrefix, nativeSuffix, nativeThrough, nativeInner, nativeMath, full: selection.toString() };
  });
  await article.getByRole("button", { name: "Copy passage", exact: true }).click();
  // Native selection has a newline after the display expression that a suffix
  // range beginning after that node omits. Keep it from the full selected text.
  const expected = "x before\n\n$$x$$\nafter x café é";
  const trace = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel").shadowRoot;
    const restored = window.getSelection().getComposedRanges({ shadowRoots: [root] })[0];
    const original = window.cb002MathEndpoints;
    return { atWrite: window.cb002MathAtWrite, original: { startOffset: original.startOffset, endOffset: original.endOffset },
      afterClick: { sameStart: restored.startContainer === original.start, startOffset: restored.startOffset,
        sameEnd: restored.endContainer === original.end, endOffset: restored.endOffset, selected: window.getSelection().toString() } };
  });
  await writeFile(resolve(evidence, "math-selection-trace.json"), JSON.stringify({ before, expected, trace }, null, 2));
  expect(await page.evaluate(() => window.cb002ClipboardWrites)).toEqual([expected]);
  expect(await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel").shadowRoot;
    const restored = window.getSelection().getComposedRanges({ shadowRoots: [root] })[0];
    const original = window.cb002MathEndpoints;
    return restored.startContainer === original.start && restored.startOffset === original.startOffset
      && restored.endContainer === original.end && restored.endOffset === original.endOffset;
  })).toBe(true);
  expect(expected).toContain("x before");
  expect(expected).toContain("after x café é");
  expect(expected).not.toContain("annotation");
  await article.getByRole("button", { name: "Quote passage", exact: true }).click();
  await expect(page.locator("codex-bridge-panel #prompt-input")).toHaveValue(`Assistant response in “Passage chat”:\n${expected.split("\n").map((line) => `> ${line}`).join("\n")}`);
  await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel").shadowRoot;
    const glyph = root.querySelector('.message[data-sequence="101"] mi');
    const range = document.createRange(); range.selectNodeContents(glyph);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  await article.getByRole("button", { name: "Copy passage", exact: true }).click();
  expect(await page.evaluate(() => window.cb002ClipboardWrites)).toEqual([expected]);
  await expect(page.getByText("Select the entire mathematical expression, or select a passage outside it.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.cb002Calls)).toEqual([]);
});
