import { createReadStream } from "node:fs";
import { mkdir, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";
import { build } from "esbuild";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const root = resolve(process.cwd());
const evidence = process.env.CODEX_BRIDGE_MATH_EVIDENCE_DIR || resolve(tmpdir(), "codex-bridge-maths", String(process.pid));
const bundle = resolve(evidence, "panel-source.js");
let server;
let origin;

test.beforeAll(async () => {
  await mkdir(evidence, { recursive: true });
  await build({ absWorkingDir: root, entryPoints: ["frontend/src/codex-bridge-panel.js"], bundle: true,
    format: "esm", platform: "browser", target: "es2022", charset: "utf8", legalComments: "eof",
    loader: { ".css": "text" }, outfile: bundle });
  server = createServer(async (request, response) => {
    const pathname = new URL(request.url, "http://fixture.invalid").pathname;
    const isBundle = pathname === "/custom_components/codex_bridge/frontend/codex-bridge-panel.js";
    const path = isBundle ? bundle : resolve(root, pathname.slice(1));
    if (!isBundle && !path.startsWith(`${root}${sep}`)) { response.writeHead(403).end(); return; }
    try {
      if (!(await stat(path)).isFile()) throw new Error("Not a fixture file");
      response.writeHead(200, { "Cache-Control": "no-store", "Content-Type": extname(path) === ".html" ? "text/html" : "text/javascript" });
      createReadStream(path).pipe(response);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((ready, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", ready); });
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => {
  if (server) await new Promise((done, reject) => server.close((error) => error ? reject(error) : done()));
});

async function openResponse(page, source, theme = "light") {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((element) => Boolean(element._config) && !element._isLoading)).toBe(true);
  await panel.evaluate(async (element, values) => {
    element._stopSystemEventSubscription();
    element._preferences = { ...element._preferences, theme: values.theme };
    element._applyPreferences();
    await element._selectThread("thr_direct");
    element._stopPolling(); element._stopEventSubscription();
    element.shadowRoot.getElementById("message-list").replaceChildren(element._renderMessage("assistant", values.source, 99063));
  }, { source, theme });
  return panel.locator('.message[data-sequence="99063"]');
}

for (const width of [390, 1280]) {
  for (const theme of ["light", "dark"]) {
    test(`native maths is readable and source-copy works at ${width}px in ${theme}`, async ({ page, context }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
      await page.addInitScript(() => {
        const originalWrite = navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText = (text) => {
          window.__mathClipboardPayload = text;
          return originalWrite(text);
        };
      });
      const remoteRequests = [];
      page.on("request", (request) => { if (!request.url().startsWith(origin)) remoteRequests.push(request.url()); });
      const equation = "\\[\r\n\\frac{a}{b}+\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}\r\n\\]";
      const source = `Inline \\(E=mc^2\\) and prose.\n\n${equation}\n\nCurrency $5 and $10.99.`;
      const article = await openResponse(page, source, theme);
      await expect(article.locator("math")).toHaveCount(2);
      await expect(article.locator("mfrac")).toBeVisible();
      await expect(article.locator("mtable")).toBeVisible();
      const dimensions = await article.evaluate((node) => {
        const content = node.querySelector(".message-content").getBoundingClientRect();
        const fraction = node.querySelector("mfrac").getBoundingClientRect();
        const display = node.querySelector(".assistant-math-display").getBoundingClientRect();
        return { fractionHeight: fraction.height, contentWidth: content.width, displayWidth: display.width,
          contentOverflow: node.querySelector(".message-content").scrollWidth - content.width };
      });
      expect(dimensions.fractionHeight).toBeGreaterThan(20);
      expect(dimensions.displayWidth).toBeLessThanOrEqual(dimensions.contentWidth + 1);
      expect(dimensions.contentOverflow).toBeLessThan(2);
      expect(await article.locator("annotation, [aria-hidden], img, a[href], script").count()).toBe(0);
      await article.getByRole("button", { name: "Message actions", exact: true }).click();
      const sourceCopy = article.getByRole("menuitem", { name: "Copy maths source 2", exact: true });
      await sourceCopy.focus(); await sourceCopy.press("Enter");
      expect(await page.evaluate(() => window.__mathClipboardPayload)).toBe(equation);
      await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(equation);
      await article.getByRole("button", { name: "Message actions", exact: true }).click();
      await article.getByRole("menuitem", { name: "Copy message", exact: true }).click();
      expect(await page.evaluate(() => window.__mathClipboardPayload)).toBe(source);
      // Windows' native clipboard normalises mixed line endings. Assert exact
      // API input above, then verify the native roundtrip with that conversion.
      await expect.poll(async () => (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n?/gu, "\n"))
        .toBe(source.replace(/\r\n?/gu, "\n"));
      expect(remoteRequests).toEqual([]);
      const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(accessibility.violations).toEqual([]);
      await article.screenshot({ path: resolve(evidence, `maths-${width}-${theme}.png`) });
      const longSource = `$$${Array.from({ length: 100 }, () => "x_1").join("+")}$$`;
      const panel = page.locator("codex-bridge-panel");
      await panel.evaluate((element, text) => {
        element.shadowRoot.getElementById("message-list").replaceChildren(element._renderMessage("assistant", text, 99064));
      }, longSource);
      const longExpression = panel.locator('.message[data-sequence="99064"] .assistant-math-display');
      await expect(longExpression.locator("math")).toHaveCount(1);
      expect(await longExpression.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
      await longExpression.focus();
      await longExpression.press("ArrowRight");
      await expect.poll(() => longExpression.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
      expect(await longExpression.evaluate((node) => getComputedStyle(node).outlineStyle)).toBe("solid");
      expect(await panel.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      await panel.locator('.message[data-sequence="99064"]').screenshot({ path: resolve(evidence, `maths-overflow-${width}-${theme}.png`) });
    });
  }
}

test("adversarial and incomplete maths stay inert without fetching or losing neighbouring prose/code", async ({ page }) => {
  const requests = [];
  page.on("request", (request) => { if (!request.url().startsWith(origin)) requests.push(request.url()); });
  const source = String.raw`\(\includegraphics{https://tracker.example/pixel}\)
\(\htmlStyle{display:none}{hidden}\)
\(\href{javascript:alert(1)}{bad}\)
$$ unfinished

Following **prose**.

~~~tex
\(not maths\)
~~~`;
  const article = await openResponse(page, source);
  await expect(article.locator("math, img, a[href], script")).toHaveCount(0);
  await expect(article.locator("strong")).toHaveText("prose");
  await expect(article.locator(".code-text")).toContainText(String.raw`\(not maths\)`);
  await expect(article.locator(".assistant-math-fallback")).toHaveCount(4);
  expect(requests).toEqual([]);
});
