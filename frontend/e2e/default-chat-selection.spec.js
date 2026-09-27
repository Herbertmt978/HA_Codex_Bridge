import { createServer } from "node:http";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";

let server;
let origin;

test.beforeAll(async () => {
  // Serve this checkout's source in memory; never touch release assets.
  const bundle = await build({ entryPoints: ["frontend/src/codex-bridge-panel.js"], bundle: true,
    format: "esm", platform: "browser", target: "es2022", loader: { ".css": "text" }, write: false });
  server = createServer((request, response) => {
    if (request.url === "/panel.js") {
      response.writeHead(200, { "Content-Type": "text/javascript" });
      response.end(bundle.outputFiles[0].text);
    } else {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end('<!doctype html><html lang="en"><title>Default chat fixture</title><script type="module" src="/panel.js"></script><body style="margin:0"></body></html>');
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

async function openPanel(page, { inventory = "assist", saved = null, link = false } = {}) {
  await page.goto(`${origin}/${link ? "?thread=assist" : ""}`);
  await page.evaluate(async ({ inventory, saved }) => {
    await customElements.whenDefined("codex-bridge-panel");
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    const assist = { thread_id: "assist", project_id: "direct", title: "Assist chat", schedule_eligible: false, status: "idle" };
    const ordinary = { thread_id: "ordinary", project_id: "direct", title: "Ordinary chat", status: "idle" };
    const threads = inventory === "empty" ? [] : inventory === "mixed" ? [assist, ordinary] : [assist];
    panel._hass = { user: { id: "fixture", is_admin: true }, states: {}, locale: { language: "en" } };
    panel._selectedThreadId = saved;
    panel.fixtureCalls = [];
    panel._callWS = async (action) => {
      panel.fixtureCalls.push(action);
      if (action === "get_config") return { api_version: 1, capabilities: [], connection_type: "supervisor" };
      if (action === "list_projects") return [{ project_id: "direct", kind: "direct", name: "Direct chats" }, { project_id: "project", kind: "project", name: "Project", root_path: "/config/workspaces/project" }];
      if (action === "list_threads") return threads;
      return {};
    };
    panel._startSystemEventSubscription = async () => {};
    panel._refreshSelectedThreadAndStartPolling = async (id) => { panel._activeThread = threads.find((thread) => thread.thread_id === id); };
    await panel._bootstrap();
  }, { inventory, saved });
  return page.locator("codex-bridge-panel");
}

for (const width of [390, 1280]) {
  for (const inventory of ["assist", "empty"]) {
    test(`${inventory} inventory offers a keyboard new-chat action at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const panel = await openPanel(page, { inventory });
      await expect.poll(() => panel.evaluate((element) => element._selectedThreadId)).toBe(null);
      const action = panel.locator('#message-list [data-action="new-direct-chat"]');
      await expect(action).toBeVisible();
      await expect(action).toBeEnabled();
      if (inventory === "assist") {
        await expect(panel.locator('#assistant-section [data-section="assistant"]')).toHaveAttribute("aria-expanded", "false");
        expect(await panel.evaluate((element) => Boolean(element.shadowRoot.getElementById("project-section").compareDocumentPosition(element.shadowRoot.getElementById("assistant-section")) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
      }
      await action.focus();
      await action.press("Enter");
      await expect(panel.locator("#thread-title-input")).toBeVisible();
      expect(await panel.evaluate((element) => element.fixtureCalls.includes("create_thread"))).toBe(false);
    });
  }
}

test("startup replaces saved Assist selection while a deliberate deep link survives refresh", async ({ page }) => {
  let panel = await openPanel(page, { inventory: "mixed", saved: "assist" });
  expect(await panel.evaluate((element) => element._selectedThreadId)).toBe("ordinary");
  panel = await openPanel(page, { inventory: "mixed", link: true });
  await panel.evaluate((element) => element._loadThreads());
  expect(await panel.evaluate((element) => element._selectedThreadId)).toBe("assist");
});
