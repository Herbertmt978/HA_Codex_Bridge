import { createReadStream } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const evidenceDirectory = resolve(process.env.CODEX_BRIDGE_CB064_EVIDENCE_DIR || resolve(tmpdir(), "codex-bridge-cb064-browser"));
const bundlePath = resolve(evidenceDirectory, "codex-bridge-panel-source.js");
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

let origin;
let server;

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, { recursive: true });
  const result = await build({
    absWorkingDir: repositoryRoot,
    entryPoints: ["frontend/src/codex-bridge-panel.js"],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2022",
    charset: "utf8",
    legalComments: "eof",
    loader: { ".css": "text" },
    outfile: bundlePath,
  });
  if (result.errors.length) throw new Error(result.errors.map((item) => item.text).join("\n"));

  server = createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://fixture.invalid").pathname;
    if (pathname === "/custom_components/codex_bridge/frontend/codex-bridge-panel.js") {
      response.writeHead(200, { "Cache-Control": "no-store", "Content-Type": contentTypes[".js"] });
      createReadStream(bundlePath).pipe(response);
      return;
    }
    const relativePath = pathname === "/" ? "frontend/e2e/panel-harness.html"
      : pathname === "/frontend/src/codex-bridge-pdf-worker.js"
        ? "custom_components/codex_bridge/frontend/codex-bridge-pdf-worker.js"
        : pathname.slice(1);
    const filePath = resolve(repositoryRoot, relativePath);
    if (!filePath.startsWith(`${repositoryRoot}${sep}`)) {
      response.writeHead(403).end();
      return;
    }
    try {
      const metadata = await stat(filePath);
      if (!metadata.isFile()) throw new Error("not a file");
      response.writeHead(200, {
        "Cache-Control": "no-store",
        "Content-Type": contentTypes[extname(filePath)] || "application/octet-stream",
      });
      if (pathname === "/frontend/src/pdf-preview.js") {
        response.end((await readFile(filePath, "utf8")).replace(
          'from "pdfjs-dist/legacy/build/pdf.min.mjs"',
          'from "/node_modules/pdfjs-dist/legacy/build/pdf.min.mjs"',
        ));
        return;
      }
      createReadStream(filePath).pipe(response);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolveListening, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListening);
  });
  const address = server.address();
  origin = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  if (server) await new Promise((resolveClose, reject) => {
    server.close((error) => (error ? reject(error) : resolveClose()));
  });
});

async function openCodePanel(page, width, theme) {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((element) => Boolean(element._config) && !element._isLoading)).toBe(true);
  await panel.evaluate(async (element) => {
    await element._selectThread("thr_direct");
    element._stopPolling();
    element._stopEventSubscription();
    element._stopSystemEventSubscription();
    element._events = [];
    window.__cb064Writes = [];
    const writeText = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = async (text) => {
      window.__cb064Writes.push(text);
      return writeText(text);
    };
  });
  return panel;
}

for (const width of [390, 1280]) {
  for (const theme of ["light", "dark"]) {
    test(`long code stays readable with wrapping and numbering at ${width} in ${theme}`, async ({ page }) => {
      const panel = await openCodePanel(page, width, theme);
      const source = `const payload = "${"long_value_".repeat(100)}";\n\n\t// Keep tabs and 🌍\n`;
      await panel.evaluate((element, code) => {
        element.shadowRoot.getElementById("message-list").replaceChildren(element._renderMessage("assistant", `\`\`\`js\n${code}\`\`\``, "code-check"));
      }, source);
      const block = panel.locator('.message[data-sequence="code-check"] .code-block');
      const pre = block.locator(".code-text");
      await expect(pre).toHaveText(source, { useInnerText: false });
      expect(await pre.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await pre.focus();
      await pre.press("ArrowRight");
      await expect.poll(() => pre.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
      await block.getByRole("button", { name: "Line numbers", exact: true }).focus();
      await page.keyboard.press("Enter");
      await block.getByRole("button", { name: "Wrap lines", exact: true }).click();
      await expect(pre).toHaveClass(/has-line-numbers/);
      await expect(pre).toHaveClass(/is-wrapped/);
      expect(await pre.textContent()).toBe(source);
      expect(await pre.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      const bounds = await pre.evaluate((node) => {
        const preRect = node.getBoundingClientRect();
        return [...node.querySelectorAll(".code-line")].map((line) => {
          const rect = line.getBoundingClientRect();
          return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, numberBottom: rect.top + Number.parseFloat(getComputedStyle(line, "::before").lineHeight), preLeft: preRect.left, preRight: preRect.right, preBottom: preRect.bottom };
        });
      });
      expect(bounds.every((line) => line.left >= line.preLeft && line.right <= line.preRight + 1)).toBe(true);
      expect(bounds[2].top).toBeGreaterThan(bounds[1].top);
      await writeFile(resolve(evidenceDirectory, `geometry-${width}-${theme}.json`), JSON.stringify(bounds, null, 2));
      expect(bounds.at(-1).bottom).toBeLessThanOrEqual(bounds.at(-1).preBottom);
      expect(bounds.at(-1).numberBottom).toBeLessThanOrEqual(bounds.at(-1).preBottom);
      const copy = block.getByRole("button", { name: "Copy original code" });
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Shift+Tab");
      await expect(copy).toBeFocused();
      expect(await copy.evaluate((node) => getComputedStyle(node).outlineStyle)).not.toBe("none");
      await copy.press("Enter");
      await expect.poll(() => page.evaluate(() => window.__cb064Writes.at(-1))).toBe(source);
      // Native Windows clipboard readback converts LF to CRLF.
      await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/gu, "\n"))).toBe(source);
      expect(await copy.evaluate((node) => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(width < 600 ? 44 : 32);
      await expect.poll(async () => (await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
      await page.screenshot({ path: resolve(evidenceDirectory, `code-${width}-${theme}.png`), animations: "disabled" });
    });
  }
}

test("streamed and retained code use equivalent safe rendering and captured copy", async ({ page }) => {
  const panel = await openCodePanel(page, 390, "light");
  const source = 'const payload = "<img src=x onerror=alert(1)>";\n';
  const message = `\`\`\`js\n${source}\`\`\``;
  const result = await panel.evaluate((element, text) => {
    const list = element.shadowRoot.getElementById("message-list");
    list.replaceChildren();
    element._events = [{ event_type: "message.delta", payload: { text, run_id: "code-run" } }];
    element._syncStreamingMessage(list, { busy: true, runId: "code-run", assistantState: "streaming" });
    const streamed = list.querySelector(".code-block");
    const history = element._renderEvent({ event_type: "message.completed", sequence: 987, payload: { text } });
    return { streamed: streamed.outerHTML, history: history.querySelector(".code-block").outerHTML, code: streamed.querySelector(".code-text").textContent, inert: !streamed.querySelector("img, script") };
  }, message);
  expect(result.streamed).toBe(result.history);
  expect(result.code).toBe(source);
  expect(result.inert).toBe(true);
  await panel.getByRole("button", { name: "Copy original code" }).click();
  await expect.poll(() => page.evaluate(() => window.__cb064Writes.at(-1))).toBe(source);
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/gu, "\n"))).toBe(source);
});
