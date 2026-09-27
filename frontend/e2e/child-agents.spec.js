/** Run only in the coordinator's serial browser lane, with --workers=1. */
import { createServer } from "node:http";
import { resolve } from "node:path";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

let server;
let origin;
test.use({ hasTouch: true });
test.beforeAll(async () => {
  const result = await build({
    entryPoints: [resolve("frontend/src/codex-bridge-panel.js")], bundle: true,
    write: false, format: "esm", platform: "browser", target: "es2022",
    loader: { ".css": "text" }, logLevel: "silent",
  });
  const javascript = result.outputFiles[0].contents;
  server = createServer((request, response) => {
    if (request.url === "/panel.js") {
      response.writeHead(200, { "Content-Type": "text/javascript", "Cache-Control": "no-store" });
      response.end(javascript);
    } else {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Child acceptance fixture</title><style>html,body{margin:0;height:100%;}codex-bridge-panel{display:block;height:100vh;}</style><script type="module" src="/panel.js"></script><body></body></html>');
    }
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => {
  if (server) await new Promise((done) => server.close(done));
});

for (const width of [390, 1280]) for (const theme of ["light", "dark"]) {
  test(`real child panel verifies one target at ${width}px in ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto(origin);
    await page.waitForFunction(() => customElements.get("codex-bridge-panel"));
    await page.evaluate((colour) => {
      const panel = document.createElement("codex-bridge-panel");
      document.body.append(panel);
      panel.setAttribute("data-panel-theme", colour);
      panel.setAttribute("data-motion", "reduced");
      panel._config = { capabilities: ["subagents_v1"] };
      panel._hass = { user: { id: "fixture-owner" } };
      panel._selectedThreadId = "parent";
      panel._activeThread = { thread_id: "parent", status: "running", attachments: [] };
      window.childCalls = [];
      const rows = [
        { child_id: "a".repeat(32), revision: 2, label: "Reviewer", status: "running", stale: true,
          task: "Review the tests", can_stop: false, can_follow_up: false },
        { child_id: "b".repeat(32), revision: 2, label: "Researcher", status: "completed", stale: true,
          task: "Check the documentation", result: "Documentation checked", can_stop: false, can_follow_up: false },
      ];
      panel._callWS = async (action, payload) => {
        window.childCalls.push({ action, payload });
        if (action === "child_agents") return { children: rows };
        if (payload.child_id !== rows[0].child_id) throw new Error("wrong child");
        if (payload.action === "refresh") return { ...rows[0], revision: 3, stale: false, can_stop: true };
        if (payload.action === "stop") return { outcome: "accepted", child: { ...rows[0], revision: 5, stop_pending: true } };
        throw new Error("unsupported control");
      };
      panel._activityView = true;
      panel._renderActivityCenter();
      panel._mobileDrawer = "context";
      panel._syncMobileDrawer();
    }, theme);
    const area = page.locator('codex-bridge-panel [data-section="individual-subagents"]');
    const refresh = area.getByRole("button", { name: "Refresh list", exact: true });
    await refresh.focus();
    await refresh.press("Enter");
    await expect(refresh).toBeFocused();
    await expect(area.locator("details")).toHaveCount(2);
    await area.locator("summary").first().tap();
    await expect(area.getByRole("button", { name: "Stop this child" }).first()).toBeDisabled();
    await area.getByRole("button", { name: "Verify status" }).first().tap();
    await expect(area.getByRole("button", { name: "Stop this child" }).first()).toBeEnabled();
    await area.getByRole("button", { name: "Stop this child" }).first().tap();
    await expect(area).toContainText("Stop requested for this child");
    await expect(area.getByRole("button", { name: "Verify status" }).first()).toBeFocused();
    expect(await page.evaluate(() => window.childCalls.filter((c) => c.payload.action === "stop").map((c) => c.payload.child_id))).toEqual(["a".repeat(32)]);
    await expect(area.getByRole("button", { name: "Follow-up unavailable" }).first()).toBeDisabled();
    const bounds = await area.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    const accessibility = await new AxeBuilder({ page })
      .include(["codex-bridge-panel", '[data-section="individual-subagents"]'])
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(accessibility.violations).toEqual([]);
    await area.screenshot({ path: info.outputPath(`children-${theme}-${width}.png`) });
  });
}
