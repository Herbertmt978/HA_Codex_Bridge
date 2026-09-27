import { createReadStream } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { tmpdir } from "node:os";

import AxeBuilder from "@axe-core/playwright";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";
import { installTaskUsageFixture } from "./task-usage-fixture.js";

const repositoryRoot = resolve(process.cwd());
const evidenceDirectory = resolve(process.env.CODEX_BRIDGE_CB060_EVIDENCE_DIR || resolve(tmpdir(), "codex-bridge-task-usage"));
const bundlePath = resolve(evidenceDirectory, "codex-bridge-panel-source.js");
const contentTypes = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8" };
let server;
let origin;

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, { recursive: true });
  await build({
    absWorkingDir: repositoryRoot, entryPoints: ["frontend/src/codex-bridge-panel.js"],
    bundle: true, format: "esm", platform: "browser", target: "es2022", charset: "utf8",
    legalComments: "eof", loader: { ".css": "text" }, outfile: bundlePath,
  });
  server = createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://fixture.invalid").pathname;
    if (pathname === "/custom_components/codex_bridge/frontend/codex-bridge-panel.js") {
      response.writeHead(200, { "Content-Type": contentTypes[".js"], "Cache-Control": "no-store" });
      createReadStream(bundlePath).pipe(response);
      return;
    }
    const relative = pathname === "/" ? "frontend/e2e/panel-harness.html"
      : pathname === "/frontend/src/codex-bridge-pdf-worker.js" ? "custom_components/codex_bridge/frontend/codex-bridge-pdf-worker.js"
        : pathname.slice(1);
    const file = resolve(repositoryRoot, relative);
    if (!file.startsWith(`${repositoryRoot}${sep}`)) { response.writeHead(403).end(); return; }
    try {
      if (!(await stat(file)).isFile()) throw new Error("Not a file");
      response.writeHead(200, { "Content-Type": contentTypes[extname(file)] || "application/octet-stream", "Cache-Control": "no-store" });
      if (pathname === "/frontend/src/pdf-preview.js") {
        response.end((await readFile(file, "utf8")).replace('from "pdfjs-dist/legacy/build/pdf.min.mjs"', 'from "/node_modules/pdfjs-dist/legacy/build/pdf.min.mjs"'));
      } else createReadStream(file).pipe(response);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((ready, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", ready); });
  origin = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => {
  if (server) await new Promise((closed, reject) => { server.close((error) => error ? reject(error) : closed()); });
});

async function preparePanel(page, { theme = "light", owner = "cb060-fixture" } = {}) {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((element) => Boolean(element._config) && !element._isLoading)).toBe(true);
  await panel.evaluate(installTaskUsageFixture, { theme, owner });
  await expect(panel.locator("#thread-title-label")).not.toBeEmpty();
  return panel;
}

for (const width of [1280, 390]) {
  for (const theme of ["light", "dark"]) {
    test(`actual duration/history controls at ${width}px in ${theme}`, async ({ browser }) => {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: theme,
        reducedMotion: "reduce", hasTouch: width === 390, isMobile: width === 390 });
      try {
        const page = await context.newPage();
        const panel = await preparePanel(page, { theme, owner: `cb060-${width}-${theme}` });
        const turnOptions = panel.getByRole("button", { name: /^Turn options/ });
        if (await turnOptions.getAttribute("aria-expanded") !== "true") await turnOptions.click();
        const duration = panel.getByRole("combobox", { name: "Elapsed-time limit", exact: true });
        await expect(duration).toHaveValue("");
        await expect(panel.getByText("Partial results retained after the elapsed-time stop.", { exact: false })).toBeVisible();
        const send = panel.locator("#send-button");
        await panel.locator("#prompt-input").fill("Fixture prompt with no extra limit");
        if (width === 390) await send.tap(); else await send.press("Enter");
        await expect.poll(() => page.evaluate(() => window.__cb060UsageFixture.calls.filter((item) => item.type === "codex_bridge/send_prompt").length)).toBe(1);
        expect(await page.evaluate(() => window.__cb060UsageFixture.calls.find((item) => item.type === "codex_bridge/send_prompt"))).not.toHaveProperty("max_duration_seconds");
        if (await turnOptions.getAttribute("aria-expanded") !== "true") await turnOptions.click();
        await expect(duration).toBeEnabled();
        await duration.focus();
        await duration.press("Home");
        await duration.press("ArrowDown");
        await expect(duration).toHaveValue("60");
        const focus = await duration.evaluate((node) => {
          const style = getComputedStyle(node);
          return { outline: style.outlineStyle, width: Number.parseFloat(style.outlineWidth), transition: Number.parseFloat(style.transitionDuration) };
        });
        expect(focus.outline).not.toBe("none");
        expect(focus.width).toBeGreaterThan(0);
        expect(focus.transition).toBeLessThan(0.001);
        await expect(panel.locator("#elapsed-limit-note")).toBeVisible();
        await page.screenshot({ path: resolve(evidenceDirectory, `selected-limit-${width}-${theme}.png`), animations: "disabled" });
        await duration.press("Tab");
        await panel.locator("#prompt-input").fill("Fixture prompt with a reviewed one-minute limit");
        if (width === 390) await send.tap(); else await send.press("Enter");
        await expect.poll(() => page.evaluate(() => window.__cb060UsageFixture.calls.filter((item) => item.type === "codex_bridge/send_prompt").length)).toBe(2);
        expect(await page.evaluate(() => window.__cb060UsageFixture.calls.filter((item) => item.type === "codex_bridge/send_prompt")[1].max_duration_seconds)).toBe(60);
        if (await turnOptions.getAttribute("aria-expanded") !== "true") await turnOptions.click();
        await expect(duration).toHaveValue("");
        await panel.getByRole("button", { name: "Toggle bottom panel", exact: true }).click();
        const open = panel.getByRole("button", { name: "Usage history", exact: true });
        if (width === 390) await open.tap(); else { await open.focus(); await open.press("Enter"); }
        const history = panel.getByRole("region", { name: "Task usage history", exact: true });
        await expect(history).toBeVisible();
        await expect(history.locator("tbody tr")).toHaveCount(4);
        await expect(history.getByText("Usage not reported", { exact: true })).toBeVisible();
        await expect(history.getByText("0 reported tokens", { exact: true })).toBeVisible();
        await expect(history.getByText("24 reported tokens · partial coverage", { exact: true })).toBeVisible();
        await expect(history.getByText("128 reported tokens", { exact: true })).toBeVisible();
        await expect(history.getByText("Saved work account · account 1", { exact: true })).toHaveCount(2);
        await expect(history.getByText("Personal account · account 2", { exact: true })).toBeVisible();
        await expect(history.getByText('<img src=x onerror="window.cb060Unsafe=true"> · account 3', { exact: true })).toBeVisible();
        await expect(history.getByText("Stop unconfirmed — new work blocked until recovery", { exact: true })).toBeVisible();
        await expect(history.getByText("Token limits are unavailable.", { exact: false })).toBeVisible();
        await expect(history.locator("img")).toHaveCount(0);
        expect(await page.evaluate(() => Boolean(window.cb060Unsafe))).toBe(false);
        await page.screenshot({ path: resolve(evidenceDirectory, `chat-history-${width}-${theme}.png`), animations: "disabled" });

        const scope = history.getByRole("combobox", { name: "History scope", exact: true });
        if (width === 390) await scope.tap(); else await scope.focus();
        await scope.press("ArrowDown");
        await scope.press("Tab");
        await expect(scope).toHaveValue("project");
        await expect(history.locator("tbody tr")).toHaveCount(5);
        const projectRequest = await page.evaluate(() => window.__cb060UsageFixture.calls.filter((item) => item.type === "codex_bridge/usage_history").at(-1));
        expect(projectRequest.project_id).toBe("prj_vba");
        expect(projectRequest).not.toHaveProperty("thread_id");
        const geometry = await panel.evaluate((element) => {
          const section = element.shadowRoot.getElementById("task-usage");
          const results = element.shadowRoot.querySelector(".task-usage-table-scroll");
          const controls = [...section.querySelectorAll("select, .row-actions button")];
          return {
            viewport: window.innerWidth, documentWidth: document.documentElement.scrollWidth,
            sectionWidth: section.getBoundingClientRect().width,
            scrollerWidth: results.clientWidth, scrollWidth: results.scrollWidth,
            horizontalOverflow: getComputedStyle(results).overflowX,
            controls: controls.map((node) => {
              const box = node.getBoundingClientRect();
              return { width: box.width, height: box.height, left: box.left, right: box.right, top: box.top, bottom: box.bottom };
            }),
          };
        });
        expect(geometry.documentWidth).toBeLessThanOrEqual(width + 1);
        expect(geometry.sectionWidth).toBeLessThanOrEqual(width + 1);
        expect(["auto", "scroll"]).toContain(geometry.horizontalOverflow);
        for (const control of geometry.controls) {
          expect(control.right).toBeLessThanOrEqual(width + 1);
          expect(control.height).toBeGreaterThanOrEqual(32);
        }
        for (let index = 1; index < geometry.controls.length; index += 1) {
          const previous = geometry.controls[index - 1];
          const current = geometry.controls[index];
          expect(current.top >= previous.bottom - 1 || current.left >= previous.right - 1).toBe(true);
        }
        if (width === 390) {
          expect(geometry.scrollWidth).toBeGreaterThan(geometry.scrollerWidth);
          const tableScroll = history.getByRole("region", { name: "Reported usage table", exact: true });
          await tableScroll.focus();
          await tableScroll.press("ArrowRight");
          await expect.poll(() => tableScroll.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
          await tableScroll.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
          await history.getByText("Stop unconfirmed — new work blocked until recovery", { exact: true }).scrollIntoViewIfNeeded();
          await page.screenshot({ path: resolve(evidenceDirectory, `history-outcomes-${width}-${theme}.png`), animations: "disabled" });
          await tableScroll.evaluate((node) => { node.scrollLeft = 0; });
          await history.locator("#task-usage-results").evaluate((node) => { node.scrollTop = 0; });
        }
        const axe = await new AxeBuilder({ page }).include({ fromShadowDom: ["codex-bridge-panel", "#task-usage"] })
          .include({ fromShadowDom: ["codex-bridge-panel", "#elapsed-limit-control"] })
          .withTags(["wcag2a", "wcag2aa"]).analyze();
        await writeFile(resolve(evidenceDirectory, `usage-${width}-${theme}.json`), JSON.stringify({ width, theme, touch: width === 390,
          reducedMotion: true, geometry, focus, axeViolations: axe.violations, projectRequest }, null, 2));
        expect(axe.violations).toEqual([]);
        await scope.focus();
        await page.screenshot({ path: resolve(evidenceDirectory, `project-history-${width}-${theme}.png`), animations: "disabled" });
      } finally { await context.close(); }
    });
  }
}

test("actual history error/retry and older-App capability state", async ({ page }) => {
  const panel = await preparePanel(page);
  await page.evaluate(() => { window.__cb060UsageFixture.state.failHistory = true; });
  await panel.getByRole("button", { name: "Toggle bottom panel", exact: true }).click();
  await panel.getByRole("button", { name: "Usage history", exact: true }).click();
  await expect(panel.locator("#task-usage-status")).toContainText("Refresh history to retry");
  await page.evaluate(() => { window.__cb060UsageFixture.state.failHistory = false; });
  await panel.getByRole("button", { name: "Refresh history", exact: true }).click();
  await expect(panel.locator("#task-usage tbody tr")).toHaveCount(4);
  await panel.getByRole("button", { name: "Close history", exact: true }).click();
  await expect(panel.locator("#task-usage")).toBeHidden();
  await panel.evaluate((element) => {
    window.__cb060UsageFixture.state.supported = false;
    element._config = { ...element._config, capabilities: [] };
    element._render(true);
  });
  await expect(panel.locator("#elapsed-limit-control")).toBeHidden();
  await expect(panel.locator("#task-usage-button")).toBeHidden();
});
