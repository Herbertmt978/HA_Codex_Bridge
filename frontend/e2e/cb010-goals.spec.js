import { createReadStream } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";

import { build } from "esbuild";
import AxeBuilder from "@axe-core/playwright";
import { installGoalFixture } from "./cb010-goals-fixture.js";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const evidenceDirectory = "D:/CodexTemp/bridge-chat-eight-20260927/feature-chats/cb010/browser";
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

async function openGoals(page, width, theme) {
  await page.setViewportSize({ width, height: 1000 });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((p) => Boolean(p._config) && !p._isLoading)).toBe(true);
  await panel.evaluate(installGoalFixture);
  const section = panel.locator("#goal-controls");
  await expect(section.locator('[data-goal-action="create"]')).toHaveCount(1);
  await section.locator(".goal-disclosure > summary").click();
  return { panel, section };
}

async function createGoal(section, objective = "Repair importer <script>window.goalLeak=true</script>") {
  await section.getByLabel("Objective", { exact: true }).fill(objective);
  await section.getByLabel("Completion criteria — one per line", { exact: true }).fill("Existing data retained\nMalformed input rejected");
  await section.getByRole("button", { name: "Save paused goal", exact: true }).click();
  await expect(section.locator(".goal-disclosure > summary")).toContainText("paused");
}

async function assertNoPrompt(page) {
  expect(await page.evaluate(() => window.goalFixture.requests.filter(({ command }) => command === "send_prompt"))).toEqual([]);
}

for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test(`manual goal lifecycle keyboard/accessibility ${width} ${theme}`, async ({ page }, testInfo) => {
    const { panel, section } = await openGoals(page, width, theme);
    await createGoal(section, `Repair importer ${"unbroken".repeat(35)} <script>window.goalLeak=true</script>`);
    expect(await page.evaluate(() => window.goalLeak)).toBeUndefined();
    await expect(section.getByRole("button", { name: "Mark goal complete", exact: true })).toBeDisabled();
    const resume = section.getByRole("button", { name: "Resume for manual turns", exact: true });
    await resume.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    expect(await resume.evaluate((el) => el.matches(":focus-visible"))).toBe(true);
    const focus = await resume.evaluate((el) => ({ style: getComputedStyle(el).outlineStyle, width: parseFloat(getComputedStyle(el).outlineWidth) }));
    expect(focus.style).not.toBe("none");
    expect(focus.width).toBeGreaterThan(0);
    await page.screenshot({ path: `${evidenceDirectory}/goals-focus-${width}-${theme}.png` });
    await page.keyboard.press("Enter");
    await expect(section.locator(".goal-disclosure > summary")).toContainText("applies to manual turns");
    await assertNoPrompt(page);
    await expect(section.getByLabel("Objective", { exact: true })).toHaveAttribute("readonly", "");
    await section.getByLabel("Progress notes", { exact: true }).fill("Local reproduction recorded");
    await section.getByRole("button", { name: "Save progress", exact: true }).click();
    await expect(section.getByRole("button", { name: "Save progress", exact: true })).toBeEnabled();
    await expect(section.getByLabel("Progress notes", { exact: true })).toHaveValue("Local reproduction recorded");
    expect(await section.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    const activeAxe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    await testInfo.attach("axe-active", { body: JSON.stringify(activeAxe, null, 2), contentType: "application/json" });
    expect(activeAxe.violations).toEqual([]);
    await page.screenshot({ path: `${evidenceDirectory}/goals-active-${width}-${theme}.png` });
    await section.getByRole("button", { name: "Pause goal", exact: true }).click();
    await expect(section.locator(".goal-disclosure > summary")).toContainText("paused");
    await section.getByRole("button", { name: "Cancel goal", exact: true }).click();
    await expect(section.locator(".goal-disclosure > summary")).toContainText("cancelled");
    await section.getByRole("button", { name: "New goal", exact: true }).click();
    await createGoal(section, "Verify replacement goal");
    const complete = section.getByRole("button", { name: "Mark goal complete", exact: true });
    await expect(complete).toBeDisabled();
    await section.getByRole("checkbox", { name: "I confirm the completion criteria are met", exact: true }).check();
    await complete.focus();
    await page.keyboard.press("Enter");
    await expect(section.locator(".goal-disclosure > summary")).toContainText("completed");
    await expect(section.getByRole("button", { name: "Resume for manual turns", exact: true })).toHaveCount(0);
    await assertNoPrompt(page);
    const actions = await page.evaluate(() => window.goalFixture.requests.filter(({ command }) => command === "goal_action").map(({ payload }) => payload));
    expect(actions.map(({ action }) => action)).toEqual(["create", "resume", "progress", "pause", "cancel", "create", "complete"]);
    expect(actions.at(-1).completion_confirmed).toBe(true);
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await section.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" });
    expect(results.violations).toEqual([]);
    await section.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${evidenceDirectory}/goals-${width}-${theme}.png` });
    await expect(panel).toBeVisible();
  });
}

test("touch resume starts no work and confirmation is required", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 1000 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    const { section } = await openGoals(page, 390, "dark");
    await createGoal(section);
    const resume = section.getByRole("button", { name: "Resume for manual turns", exact: true });
    expect(await resume.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    await resume.tap();
    await expect(section.locator(".goal-disclosure > summary")).toContainText("applies to manual turns");
    await assertNoPrompt(page);
    await expect(section.getByRole("button", { name: "Mark goal complete", exact: true })).toBeDisabled();
    await section.getByRole("checkbox", { name: "I confirm the completion criteria are met", exact: true }).tap();
    await section.getByRole("button", { name: "Mark goal complete", exact: true }).tap();
    await expect(section.locator(".goal-disclosure > summary")).toContainText("completed");
    await assertNoPrompt(page);
    await page.screenshot({ path: `${evidenceDirectory}/goals-touch-dark.png` });
  } finally { await context.close(); }
});

test("owner away-and-back and disconnect reject late read and action replies", async ({ page }) => {
  const { panel, section } = await openGoals(page, 1280, "light");
  await createGoal(section, "Owner one's goal");
  await page.evaluate(() => { window.goalFixture.holdNext = "get_goal"; });
  await section.getByRole("button", { name: "Refresh goal", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.goalFixture.delayed.length)).toBe(1);
  await panel.evaluate((p) => p._callWS("goal_action", { thread_id: "goal-chat", action: "progress",
    expected_revision: 1, client_request_id: "external-progress", progress: "Newer server progress" }));
  await page.evaluate(() => window.goalFixture.changeOwner("goal-owner-two"));
  await expect(section.locator('[data-goal-action="create"]')).toHaveCount(1);
  await page.evaluate(() => window.goalFixture.changeOwner("goal-owner-one"));
  await expect(section.locator('[name="goal-objective"]')).toHaveValue("Owner one's goal");
  await expect(section.locator('[name="goal-progress"]')).toHaveValue("Newer server progress");
  await page.evaluate(() => window.goalFixture.release());
  await expect(section.locator('[name="goal-objective"]')).toHaveValue("Owner one's goal");
  await expect(section.locator('[name="goal-progress"]')).toHaveValue("Newer server progress");
  // A pending mutation has applied on the server; its late readback must not
  // populate the other account or resurrect retry state after disconnection.
  await section.locator(".goal-disclosure > summary").click();
  await page.evaluate(() => { window.goalFixture.holdNext = "goal_action"; });
  await section.getByRole("button", { name: "Resume for manual turns", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.goalFixture.delayed.length)).toBe(1);
  await page.evaluate(() => window.goalFixture.changeOwner("goal-owner-two"));
  await expect(section.locator('[data-goal-action="create"]')).toHaveCount(1);
  await page.evaluate(() => window.goalFixture.release());
  expect(await panel.evaluate((p) => ({ goal: p._goalControls.view.goal, pending: p._goalControls.pending }))).toEqual({ goal: null, pending: null });
  await panel.evaluate((p) => { window.goalFixture.holdNext = "get_goal"; void p._goalControls.refresh(); });
  await expect.poll(() => page.evaluate(() => window.goalFixture.delayed.length)).toBe(1);
  await panel.evaluate((p) => { window.detachedGoalPanel = p; p.remove(); });
  await page.evaluate(() => window.goalFixture.release());
  await expect.poll(() => page.evaluate(() => ({ view: window.detachedGoalPanel._goalControls.view,
    pending: window.detachedGoalPanel._goalControls.pending,
    children: window.detachedGoalPanel.shadowRoot.getElementById("goal-controls").childElementCount })))
    .toEqual({ view: null, pending: null, children: 0 });
  expect(await page.evaluate(() => document.querySelector("codex-bridge-panel"))).toBeNull();
  await assertNoPrompt(page);
});
