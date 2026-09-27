import { createReadStream } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const evidenceDirectory = process.env.CODEX_BRIDGE_DESIGN_EVIDENCE_DIR
  ? resolve(process.env.CODEX_BRIDGE_DESIGN_EVIDENCE_DIR)
  : resolve(tmpdir(), "codex-bridge-design-language", String(process.pid));
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

async function openDesignPanel(page, width, theme) {
  await page.setViewportSize({ width, height: 1100 });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((element) => Boolean(element._config) && !element._isLoading)).toBe(true);
  await panel.evaluate(async (element) => {
    element._stopSystemEventSubscription();
    await element._selectThread("thr_direct");
    element._stopPolling();
    element._stopEventSubscription();
    // Install fixture capability responses at the transport owner, so refreshes
    // cannot replace the advertised controls with the harness's older config.
    const call = element._callWS.bind(element);
    element._callWS = async (command, parameters) => {
      if (command === "get_config") return { ...element._config, capabilities: ["plan_mode_v1", "git_review_v1"] };
      if (command === "git_review") {
        if (element._designGitState === "error") throw new Error("Fixture unavailable");
        if (element._designGitState === "loading") return new Promise((resolveResult) => { element._designResolveGit = resolveResult; });
        if (element._designGitState === "files") return { files: [{ path: "src/example.js", status: "modified", patch: "+const value = 1;" }] };
        return { files: [] };
      }
      return call(command, parameters);
    };
    element._config = { ...element._config, capabilities: ["plan_mode_v1", "git_review_v1"] };
    element._render(true);
  });
  if (width === 390) await panel.locator(".composer-diagnostics > summary").click();
  return panel;
}

for (const width of [390, 1280]) {
  for (const theme of ["light", "dark"]) {
    test(`design language: Collaboration and Git controls at ${width}px in ${theme}`, async ({ page }, testInfo) => {
      const panel = await openDesignPanel(page, width, theme);
      const collaboration = panel.getByRole("combobox", { name: "Collaboration", exact: true });
      await expect(collaboration).toBeVisible();
      await collaboration.focus();
      await collaboration.press("ArrowDown");
      await expect(collaboration).toHaveValue("plan");
      const focus = await collaboration.evaluate((node) => {
        const css = getComputedStyle(node);
        return { outline: css.outlineStyle, width: parseFloat(css.outlineWidth), motion: parseFloat(css.transitionDuration) || 0 };
      });
      expect(focus.outline).not.toBe("none");
      expect(focus.width).toBeGreaterThan(0);
      expect(focus.motion).toBeLessThan(0.001);
      await collaboration.selectOption("default");
      await panel.getByRole("button", { name: "Review changes", exact: true }).click();
      const results = panel.locator("#git-review-results");
      await expect(results).toHaveText("No changes in this scope.");
      const scope = panel.getByRole("combobox", { name: "Scope", exact: true });
      const reference = panel.getByRole("textbox", { name: "Reference", exact: true });
      const refresh = panel.getByRole("button", { name: "Refresh diff", exact: true });
      await page.screenshot({ path: testInfo.outputPath(`git-${width}-${theme}-before-check.png`), animations: "disabled" });
      for (const control of [collaboration, scope, reference, refresh]) {
        await expect(control).toBeVisible();
        const box = await control.boundingBox();
        expect(box.height, await control.getAttribute("id") || "Refresh diff").toBeGreaterThanOrEqual(width === 390 ? 44 : 32);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      const styles = await panel.evaluate((element) => {
        const css = (id) => { const s = getComputedStyle(element.shadowRoot.getElementById(id)); return { font: s.fontFamily, size: s.fontSize, color: s.color, appearance: s.appearance }; };
        return { collaboration: css("collaboration-mode"), scope: css("git-review-scope"), reference: css("git-review-ref"), model: (() => { const s = getComputedStyle(element.shadowRoot.querySelector("#compact-toolbar select")); return { font: s.fontFamily, size: s.fontSize }; })() };
      });
      expect(styles.collaboration.font).toBe(styles.model.font);
      expect(styles.scope.font).toBe(styles.model.font);
      expect(styles.scope.size).toBe(styles.collaboration.size);
      expect(styles.reference.size).toBe(styles.scope.size);
      expect(styles.scope.appearance).toBe("none");
      await scope.focus();
      await scope.press("ArrowDown");
      await expect(scope).toHaveValue("staged");
      await reference.fill("HEAD");
      await panel.evaluate((element) => { element._designGitState = "loading"; });
      await refresh.click();
      await expect(results).toHaveText("Loading Git diff…");
      await panel.evaluate((element) => { element._designResolveGit({ files: [] }); element._designGitState = "error"; });
      await expect(results).toHaveText("No changes in this scope.");
      await refresh.click();
      await expect(results).toContainText("Git review unavailable.");
      await page.screenshot({ path: testInfo.outputPath(`git-${width}-${theme}-unavailable.png`), animations: "disabled" });
      await panel.evaluate((element) => { element._designGitState = "empty"; });
      await refresh.focus();
      await refresh.press("Enter");
      await expect(results).toHaveText("No changes in this scope.");
      await panel.evaluate((element) => { element._designGitState = "files"; });
      await refresh.click();
      await panel.locator("#git-review summary").click();
      await expect(panel.locator("#git-review .git-diff")).toHaveText("+const value = 1;");
      const load = panel.getByRole("button", { name: "Load file diff", exact: true });
      expect((await load.boundingBox()).height).toBeGreaterThanOrEqual(width === 390 ? 44 : 32);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expect.poll(async () => (await new AxeBuilder({ page }).include(["codex-bridge-panel", "#collaboration-controls"]).include(["codex-bridge-panel", "#git-review"]).withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`git-${width}-${theme}.png`), animations: "disabled" });
    });
  }
}

test("design language: narrow touch Git actions", async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true });
  try {
    const page = await context.newPage();
    const panel = await openDesignPanel(page, 390, "dark");
    await panel.getByRole("button", { name: "Review changes", exact: true }).tap();
    await expect(panel.locator("#git-review-results")).toHaveText("No changes in this scope.");
    await panel.getByRole("button", { name: "Refresh diff", exact: true }).tap();
    await expect(panel.locator("#git-review-results")).toHaveText("No changes in this scope.");
  } finally { await context.close(); }
});

for (const theme of ["light", "dark"]) {
  test(`design language: empty narrow conversation scroll is keyboard accessible in ${theme}`, async ({ page }, testInfo) => {
    const panel = await openDesignPanel(page, 390, theme);
    await panel.getByRole("button", { name: "Review changes", exact: true }).click();
    await expect(panel.locator("#git-review-results")).toHaveText("No changes in this scope.");
    const scroll = panel.getByRole("region", { name: "Conversation", exact: true });
    await expect(scroll).toHaveAttribute("tabindex", "0");
    // The real empty layout overflows when expanded settings and Git review
    // share the narrow viewport; do not inject content or force its dimensions.
    const geometry = await scroll.evaluate((node) => ({ height: node.clientHeight, content: node.scrollHeight }));
    expect(geometry.height).toBeGreaterThan(0);
    expect(geometry.content).toBeGreaterThan(geometry.height);
    await panel.getByRole("button", { name: "Toggle bottom panel", exact: true }).focus();
    await page.keyboard.press("Tab");
    const goalSummary = panel.locator("#goal-controls summary");
    await expect(goalSummary).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(scroll).toBeFocused();
    await expect.poll(() => scroll.evaluate((node) => parseFloat(getComputedStyle(node).outlineOffset))).toBe(-2);
    const focus = await scroll.evaluate((node) => {
      const css = getComputedStyle(node);
      return { outline: css.outlineStyle, width: parseFloat(css.outlineWidth), offset: parseFloat(css.outlineOffset), shadow: css.boxShadow };
    });
    expect(focus.outline).not.toBe("none");
    expect(focus.width).toBeGreaterThan(0);
    expect(focus.offset).toBeLessThan(0);
    expect(focus.shadow).toContain("inset");
    await scroll.evaluate((node) => { node.scrollTop = 0; });
    await page.keyboard.press("PageDown");
    await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
    await page.keyboard.press("Tab");
    await expect(panel.getByRole("button", { name: "Add to chat", exact: true })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(scroll).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(goalSummary).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(panel.getByRole("button", { name: "Toggle bottom panel", exact: true })).toBeFocused();
    await expect.poll(async () => (await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await scroll.focus();
    await page.screenshot({ path: testInfo.outputPath(`conversation-focus-${theme}.png`), animations: "disabled" });
  });
}

test("design language: non-empty conversation scroll keeps native keys and tab order", async ({ page }) => {
  const panel = await openDesignPanel(page, 390, "light");
  await panel.getByRole("button", { name: "Review changes", exact: true }).click();
  await expect(panel.locator("#git-review-results")).toHaveText("No changes in this scope.");
  await panel.evaluate((element) => {
    const list = element.shadowRoot.getElementById("message-list");
    list.replaceChildren(element._renderMessage("user", Array.from({ length: 100 }, (_, index) => `Transcript line ${index + 1}`).join("\n"), 99010));
  });
  const scroll = panel.getByRole("region", { name: "Conversation", exact: true });
  await panel.getByRole("button", { name: "Toggle bottom panel", exact: true }).focus();
  await page.keyboard.press("Tab");
  const goalSummary = panel.locator("#goal-controls summary");
  await expect(goalSummary).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(scroll).toBeFocused();
  await scroll.evaluate((node) => { node.scrollTop = 0; });
  await page.keyboard.press("PageDown");
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await page.keyboard.press("Home");
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBe(0);
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await page.keyboard.press("Tab");
  const copy = panel.locator('.message[data-sequence="99010"]').getByRole("button", { name: "Copy message", exact: true });
  await expect(copy).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(scroll).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(goalSummary).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(panel.getByRole("button", { name: "Toggle bottom panel", exact: true })).toBeFocused();
});
