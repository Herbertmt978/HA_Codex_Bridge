import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

let origin;
let server;

function createMinimalPdfFixture() {
  const objects = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
    "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
    "4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    "5 0 obj\n<< /Length 48 >>\nstream\nBT /F1 18 Tf 24 100 Td (Local PDF preview) Tj ET\nendstream\nendobj\n",
  ];
  let document = "%PDF-1.4\n";
  const offsets = [0];
  for (const object of objects) {
    offsets.push(Buffer.byteLength(document));
    document += object;
  }
  const startXref = Buffer.byteLength(document);
  document += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  document += offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  document += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${startXref}\n%%EOF\n`;
  return Buffer.from(document, "ascii");
}

test.beforeAll(async () => {
  server = createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://ha.invalid").pathname;
    const relativePath = pathname === "/"
      ? "frontend/e2e/panel-harness.html"
      : pathname === "/frontend/src/codex-bridge-pdf-worker.js"
        ? "custom_components/codex_bridge/frontend/codex-bridge-pdf-worker.js"
        : pathname.slice(1);
    const filePath = resolve(repositoryRoot, relativePath);
    if (!filePath.startsWith(`${resolve(repositoryRoot)}${sep}`)) {
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
  await new Promise((resolveClose, reject) => {
    server.close((error) => (error ? reject(error) : resolveClose()));
  });
});

async function selectHarnessThread(page, threadId = "thr_vba_1") {
  await page.evaluate((selectedThreadId) => document.querySelector("codex-bridge-panel")._selectThread(selectedThreadId), threadId);
  await expect(page.locator("codex-bridge-panel").locator("#thread-title-label")).not.toBeEmpty();
}

async function websocketCalls(page, type) {
  return page.evaluate((commandType) => window.__codexHarness.calls.filter((call) => call.kind === "ws" && call.type === commandType), type);
}

for (const width of [390, 1280]) {
  for (const installed of [false, true]) {
    test(`host access warning is usable at ${width}px with App ${installed ? "ready" : "missing"}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
      const panel = page.locator("codex-bridge-panel");
      await expect(panel.locator("#new-project-button")).toBeVisible();
      await page.evaluate((ready) => {
        const element = document.querySelector("codex-bridge-panel");
        element._stopPolling();
        element._config = { ...element._config, capabilities: ["host_access_v1"] };
        const original = element._callWS.bind(element);
        const status = {
          state: ready ? "ready" : "not_paired", enabled: false,
          warnings: [
            ["Commands and services", "Codex can run commands as root on this Home Assistant OS machine, install or run software, manage containers and services, and restart or stop Home Assistant."],
            ["Files and credentials", "Codex can read, change or delete host files, including Home Assistant configuration, app data, backups and mounted storage. This includes secrets, integration tokens and saved sign-in credentials accessible to root, including Codex sign-in data."],
            ["Internet and local network", "Codex can use this machine's internet and local-network connections. Stored credentials may allow access to other systems, including Proxmox or another VM, with the permissions those credentials grant."],
            ["Data sent outside Home Assistant", "File contents and command output returned to Codex can be sent to the model provider. Network commands can send data to other services."],
            ["Risk to your home", "Incorrect instructions or malicious content encountered during work could delete data, expose credentials or interrupt household automations."],
            ["Stopping and revoking access", "Stop or revoke blocks further Bridge requests and attempts to stop tracked commands. It cannot undo completed changes. Root commands can change these controls or start work that continues afterwards."],
          ].map(([title, description]) => ({ title, description })),
          disclosure: { scope_revision: "a".repeat(64), hostname: "HAOS-DEV", os_version: "18.3" },
        };
        element._callWS = async (method, args) => method === "host_access" ? status : method === "enable_host_access" ? { ...status, enabled: true, grant_id: "b".repeat(32) } : original(method, args);
        element._showThreadForm = true;
        element._renderedThreadFormKey = "";
        element._render();
      }, installed);
      const mode = panel.locator("#thread-mode-select");
      await mode.selectOption("haos-full-access");
      const dialog = panel.getByRole("dialog", { name: "Allow Codex full access to Home Assistant OS?" });
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText("Files and credentials");
      await expect(mode).not.toHaveValue("haos-full-access");
      const bounds = await dialog.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
      const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(accessibility.violations).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath("host-access-warning.png") });
      if (installed) {
        await expect(dialog.getByRole("link")).toHaveCount(0);
        const enable = dialog.getByRole("button", { name: "Enable host access" });
        await expect(enable).toBeDisabled();
        await dialog.getByRole("checkbox").check();
        await enable.click();
        await expect(dialog).toBeHidden();
        await expect(mode).toHaveValue("haos-full-access");
      } else {
        await expect(dialog.getByRole("link", { name: "Host Access installation instructions" })).toBeVisible();
        await expect(dialog.locator("#confirm-host-access")).toHaveCount(0);
        await dialog.getByRole("button", { name: "Cancel" }).click();
        await expect(dialog).toBeHidden();
      }
    });
  }
}

test("settings persist appearance and keep themed menus usable on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => document.querySelector("codex-bridge-panel")._selectDesktopDestination("settings"));
  const panel = page.locator("codex-bridge-panel");
  await panel.getByRole("tab", { name: "Appearance", exact: true }).click();
  const theme = panel.getByRole("combobox", { name: "Theme", exact: true });
  await theme.click();
  const menu = panel.getByRole("listbox", { name: "Theme", exact: true });
  const bounds = await menu.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await theme.press("End"); await theme.press("Enter");
  await expect(panel).toHaveAttribute("data-panel-theme", "dark");
  await expect(theme).toContainText("Dark");
  await expect(theme).toBeFocused();
  await page.evaluate(() => { const element = document.querySelector("codex-bridge-panel"); element.hass = { ...element.hass }; });
  await expect(theme).toBeFocused();
  await panel.screenshot({ path: test.info().outputPath("settings-dark-mobile.png") });
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.reload();
  await expect(panel).toHaveAttribute("data-panel-theme", "dark");
});

for (const width of [390, 1280]) {
  test(`guided HA-MCP and custom connections remain accessible at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element._stopPolling();
      element._config = { ...element._config, capabilities: [] };
      element._selectDesktopDestination("settings");
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.getByRole("tab", { name: "Access", exact: true }).click();
    await expect(panel.getByText("Full access · Home Assistant OS", { exact: true })).toBeVisible();
    await expect(panel.getByRole("link", { name: "Update instructions and missing-update checks" })).toBeVisible();
    await panel.screenshot({ path: testInfo.outputPath("access-settings.png") });
    await panel.getByRole("tab", { name: "MCP servers", exact: true }).click();
    await panel.getByRole("button", { name: "Add MCP server", exact: true }).click();
    await expect(panel.getByRole("button", { name: /^Home Assistant \(HA-MCP\)/ })).toBeFocused();
    await expect(panel.getByRole("button", { name: /^Other MCP server/ })).toBeVisible();
    await panel.screenshot({ path: testInfo.outputPath("mcp-choices.png") });
    await panel.getByRole("button", { name: /^Home Assistant \(HA-MCP\)/ }).press("Enter");
    await expect(panel.getByLabel("Name", { exact: true })).toHaveValue("home-assistant");
    await expect(panel.getByRole("button", { name: "Add server", exact: true })).toBeDisabled();
    await panel.getByLabel("HA-MCP HTTPS connection URL").fill("https://ha.example.org/api/webhook/test-only");
    await expect(panel.getByLabel("HA-MCP HTTPS connection URL")).toHaveAttribute("type", "password");
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element.hass = { ...element.hass };
    });
    await expect(panel.getByLabel("HA-MCP HTTPS connection URL")).toHaveValue("https://ha.example.org/api/webhook/test-only");
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(accessibility.violations).toEqual([]);
    const formBounds = await panel.locator('[data-desktop-form="mcp"]').boundingBox();
    expect(formBounds.x).toBeGreaterThanOrEqual(0);
    expect(formBounds.x + formBounds.width).toBeLessThanOrEqual(width);
    await panel.screenshot({ path: testInfo.outputPath("ha-mcp-guide.png") });
    await panel.getByRole("button", { name: "Cancel", exact: true }).click();
    await panel.getByRole("button", { name: "Add MCP server", exact: true }).click();
    await panel.getByRole("button", { name: /^Other MCP server/ }).click();
    await expect(panel.getByLabel("Name", { exact: true })).toHaveValue("");
    await panel.getByText("OAuth settings (optional)", { exact: true }).click();
    await expect(panel.getByLabel("OAuth client ID (public)")).toBeVisible();
    await expect(panel.getByLabel("OAuth resource")).toBeVisible();
  });
}

test("scheduled runtime selections and grouped skills remain readable", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await panel.locator('[data-destination="scheduled"]').click();
  await panel.getByRole("button", { name: "New schedule", exact: true }).click();
  const form = panel.locator(".schedule-editor");
  await form.locator("summary").click();
  const model = form.getByRole("combobox", { name: "Model", exact: true });
  await model.click();
  await expect(form.getByRole("option", { name: "GPT-5.6-Sol", exact: true })).toBeVisible();
  await model.press("Escape");
  await expect(form.locator('[name="model"]')).toHaveValue("");
  await form.getByRole("combobox", { name: "Reasoning", exact: true }).click();
  await expect(form.getByRole("listbox", { name: "Reasoning", exact: true })).toBeVisible();
  await form.screenshot({ path: test.info().outputPath("scheduled-model-reasoning.png") });
  await page.evaluate(() => {
    const element = document.querySelector("codex-bridge-panel");
    element._desktopFeatures.skills = { loaded: true, data: { skills: [
      { name: "data-analytics:build-report", scope: "workspace", enabled: true },
      { name: "data-analytics:design-kpis", scope: "workspace", enabled: true },
      { name: "aegis:systematic-debugging", scope: "workspace", enabled: false },
    ] } };
    element._selectDesktopDestination("skills");
  });
  await expect(panel.locator(".skill-group")).toHaveCount(2);
  await expect(panel.getByRole("heading", { name: "Data analytics 2 skills" })).toBeVisible();
  await panel.screenshot({ path: test.info().outputPath("skills-grouped.png") });
});

test("creates and edits a scheduled task using the reference form", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel.hass = { ...panel.hass, config: { time_zone: "Europe/London" } };
  });
  const panel = page.locator("codex-bridge-panel");
  await panel.locator('[data-destination="scheduled"]').click();
  await panel.getByRole("button", { name: "New schedule", exact: true }).click();
  const form = panel.locator(".schedule-editor");
  await expect(form.getByText("Home Assistant", { exact: true })).toBeVisible();
  await expect(form.getByRole("textbox", { name: "Scheduled task title" })).toBeVisible();
  await form.getByRole("textbox", { name: "Scheduled task title" }).fill("Morning summary");
  await form.getByRole("textbox", { name: "Task instructions" }).fill("Summarise overnight events in Home Assistant.");
  await form.getByRole("combobox", { name: "Repeat", exact: true }).click();
  await form.getByRole("option", { name: "Weekdays", exact: true }).click();
  await form.getByLabel("Time", { exact: true }).fill("09:00");
  await expect(form.locator(".schedule-preview")).toHaveText("Every weekday at 09:00 · Europe/London");
  await expect(form.locator('[name="project_id"], [name="thread_id"], [name="revision"], [name="rrule"]')).toHaveCount(0);
  await form.screenshot({ path: test.info().outputPath("scheduled-desktop.png") });
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);

  const repeat = form.getByRole("combobox", { name: "Repeat", exact: true });
  await repeat.focus();
  await repeat.press("Space");
  const stable = await page.evaluate(async () => {
    const panel = document.querySelector("codex-bridge-panel");
    const select = panel.shadowRoot.querySelector('[role="combobox"][aria-label="Repeat"]');
    const observer = new MutationObserver(() => {});
    observer.observe(select.closest("form"), { childList: true, subtree: true });
    for (let index = 0; index < 12; index += 1) {
      panel.hass = { ...panel.hass, states: { ...panel.hass.states } };
      panel._renderDesktopSurface();
      await new Promise(requestAnimationFrame);
    }
    const result = { same: panel.shadowRoot.querySelector('[role="combobox"][aria-label="Repeat"]') === select, focus: panel.shadowRoot.activeElement === select, mutations: observer.takeRecords().length };
    observer.disconnect(); return result;
  });
  await repeat.press("Escape");
  expect(stable).toEqual({ same: true, focus: true, mutations: 0 });
  expect(await websocketCalls(page, "codex_bridge/create_automation")).toHaveLength(0);

  await form.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(panel.getByRole("cell", { name: "Morning summary", exact: true })).toBeVisible();
  const created = (await websocketCalls(page, "codex_bridge/create_automation"))[0].payload;
  expect(created).toMatchObject({ name: "Morning summary", target: { kind: "standalone", project_id: "prj_vba" }, schedule: { kind: "rrule", rule: "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0;BYSECOND=0", timezone: "Europe/London" }, mode: "observe" });
  await panel.getByRole("button", { name: "Update", exact: true }).click();
  await form.getByRole("textbox", { name: "Scheduled task title" }).fill("Renamed summary");
  await form.getByRole("button", { name: "Save changes", exact: true }).click();
  const updated = (await websocketCalls(page, "codex_bridge/update_automation"))[0].payload;
  expect(updated).toMatchObject({ name: "Renamed summary", expected_revision: 1, target: created.target, schedule: created.schedule });
});

test("keeps a schedule draft after a save error and fits a narrow screen", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await panel.locator('[data-destination="scheduled"]').click();
  await panel.getByRole("button", { name: "New schedule", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const form = panel.locator(".schedule-editor");
  await form.getByRole("textbox", { name: "Scheduled task title" }).fill("Check on this chat");
  await form.getByRole("textbox", { name: "Task instructions" }).fill("Check for anything needing attention.");
  await form.getByRole("combobox", { name: "Runs in", exact: true }).click();
  await form.getByRole("option", { name: "Current chat", exact: true }).click();
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    const send = panel.hass.connection.sendMessagePromise;
    panel.hass.connection.sendMessagePromise = (request) => request.type === "codex_bridge/create_automation" ? Promise.reject(new Error("Temporary connection failure")) : send(request);
  });
  await form.getByRole("button", { name: "Create task", exact: true }).click();
  await expect(form.getByRole("alert")).toHaveText("Temporary connection failure");
  await expect(form.getByRole("textbox", { name: "Scheduled task title" })).toHaveValue("Check on this chat");
  await expect(form.getByRole("combobox", { name: "Runs in", exact: true })).toContainText("Current chat");
  await expect(form.locator('[name="target_kind"]')).toHaveValue("continue_thread");
  const overflow = await form.evaluate((node) => node.scrollWidth > node.clientWidth || node.getBoundingClientRect().right > window.innerWidth);
  expect(overflow).toBe(false);
  await form.screenshot({ path: test.info().outputPath("scheduled-mobile.png") });
});

async function seedRunStageActivity(page) {
  await page.evaluate(() => {
    const harness = window.__codexHarness;
    const threadId = "thr_vba_1";
    const runId = "run_stage_tooltip";
    const runningThread = harness.updateThread(threadId, {
      status: "running",
      active_run_id: runId,
    });
    const panel = document.querySelector("codex-bridge-panel");
    panel._activeThread = runningThread;
    panel._threads = panel._threads.map((thread) => (
      thread.thread_id === threadId ? runningThread : thread
    ));
    panel._renderThreadRunState(runningThread);
    panel._renderComposerState(runningThread);
    harness.emitThreadEvent(threadId, "run.started", { run_id: runId });
    harness.emitThreadEvent(threadId, "plan.updated", {
      run_id: runId,
      plan: [
        { step: "Search the web", status: "completed" },
        { step: "Inspect returned pages", status: "inProgress" },
        { step: "Summarize sources", status: "pending" },
      ],
    });
    harness.emitThreadEvent(threadId, "item.started", {
      run_id: runId,
      item_type: "webSearch",
      action_type: "search",
    });
    harness.emitThreadEvent(threadId, "patch.updated", {
      run_id: runId,
      changes: [{ path: "frontend/e2e/panel.spec.js", kind: "update", diff: "+stage tooltip regression\n-old assertion" }],
    });
  });
  await expect(page.locator("codex-bridge-panel").locator("#run-step-chip")).toBeVisible();
}

test("keeps sidebar hover and keyboard focus stable during HA updates", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const plugins = page.locator('codex-bridge-panel [data-destination="plugins"]');
  await plugins.focus();
  await plugins.hover();
  const samples = await page.evaluate(async () => {
    const panel = document.querySelector("codex-bridge-panel");
    const control = panel.shadowRoot.querySelector('[data-destination="plugins"]');
    await Promise.all(control.getAnimations().map((animation) => animation.finished));
    const results = [];
    for (let update = 0; update < 12; update += 1) {
      panel.hass = { ...panel._hass, states: { ...panel._hass.states } };
      await new Promise(requestAnimationFrame);
      results.push({
        connected: control.isConnected,
        hovered: control.matches(":hover"),
        focused: panel.shadowRoot.activeElement === control,
        background: getComputedStyle(control).backgroundColor,
      });
    }
    return results;
  });
  expect(samples.every((sample) => sample.connected && sample.hovered && sample.focused)).toBe(true);
  expect(new Set(samples.map((sample) => sample.background)).size).toBe(1);
});

test("keeps a populated plugin catalogue stable through frequent HA refreshes", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect(panel.locator("#thread-model-select")).toBeVisible();
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._activeDestination = "plugins";
    panel._desktopFeatures.plugins.loaded = true;
    panel._desktopFeatures.plugins.data = {
      plugins: Array.from({ length: 4294 }, (_, index) => ({ id: `plugin-${index}`, name: `Plugin ${index}`, description: "Catalogue description" })),
      marketplaces: [],
    };
    panel._render(true);
  });
  const install = panel.locator('[data-desktop-action="install-plugin"]').first();
  await install.focus();
  await install.hover();
  const result = await page.evaluate(async () => {
    const panel = document.querySelector("codex-bridge-panel");
    const surface = panel.shadowRoot.getElementById("desktop-feature-surface");
    const control = surface.querySelector('[data-desktop-action="install-plugin"]');
    const table = surface.querySelector("table");
    let changes = 0;
    const observer = new MutationObserver((records) => { changes += records.length; });
    observer.observe(surface, { childList: true, subtree: true });
    for (let update = 0; update < 12; update += 1) {
      panel.hass = { ...panel._hass, states: { ...panel._hass.states } };
      await new Promise(requestAnimationFrame);
    }
    observer.disconnect();
    return { changes, sameTable: surface.querySelector("table") === table, focused: panel.shadowRoot.activeElement === control, hovered: control.matches(":hover"), count: surface.querySelectorAll('[data-desktop-action="install-plugin"]').length };
  });
  expect(result).toEqual({ changes: 0, sameTable: true, focused: true, hovered: true, count: 4294 });
  await panel.locator('[data-desktop-action="open-marketplace-form"]').click();
  const ref = panel.locator('[data-desktop-field="ref_name"]');
  await ref.fill("working draft");
  const draftResult = await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    const control = panel.shadowRoot.querySelector('[data-desktop-field="ref_name"]');
    control.setSelectionRange(2, 5);
    for (let update = 0; update < 12; update += 1) {
      panel.hass = { ...panel._hass, states: { ...panel._hass.states } };
      panel._renderDesktopSurface();
    }
    return { sameControl: panel.shadowRoot.querySelector('[data-desktop-field="ref_name"]') === control, focused: panel.shadowRoot.activeElement === control, selection: [control.selectionStart, control.selectionEnd] };
  });
  expect(draftResult).toEqual({ sameControl: true, focused: true, selection: [2, 5] });
  await expect(ref).toHaveValue("working draft");
  await panel.locator('[data-desktop-action="close-form"]').click();
  await panel.locator('[data-desktop-action="open-marketplace-form"]').click();
  await expect(ref).toHaveValue("");
  await panel.getByRole("button", { name: "Chats", exact: true }).click();
  await expect(panel.locator("#prompt-input")).toBeVisible();
});

test("keeps hostile Codex content inert and on the Home Assistant origin", async ({ page }) => {
  const requests = [];
  const pageErrors = [];
  page.on("request", (request) => requests.push(request.url()));
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await expect(panel.locator("#message-list")).toBeVisible();
  await page.evaluate(() => document.querySelector("codex-bridge-panel")._selectThread("thr_vba_1"));
  await expect.poll(() => panel.locator("#message-list").textContent()).toContain("collecting the uploaded file sizes");

  await page.evaluate(() => {
    window.__codexXss = 0;
    const bridgePanel = document.querySelector("codex-bridge-panel");
    const hostile = [
      "</pre><script>window.__codexXss=1</script>",
      '<img src="https://evil.example/collect" onerror="window.__codexXss=2">',
      '<iframe srcdoc="<script>window.__codexXss=3</script>"></iframe>',
      '<svg onload="window.__codexXss=4"></svg>',
      '[click](javascript:window.__codexXss=5)',
    ].join("\n");
    bridgePanel._handleSubscribedEvent(bridgePanel._selectedThreadId, {
      event_id: "evt_hostile",
      thread_id: bridgePanel._selectedThreadId,
      sequence: 999,
      event_type: "message.completed",
      payload: { text: hostile },
      timestamp: new Date().toISOString(),
    });
    bridgePanel._selectedArtifactId = "art_hostile";
    bridgePanel._artifactPreview = {
      artifactId: "art_hostile",
      filename: 'attack.svg" onload="window.__codexXss=6',
      contentType: "image/svg+xml",
      kind: "binary",
    };
    bridgePanel._renderArtifactPreview();
  });

  await expect(panel.locator("#message-list")).toContainText("<script>");
  const unsafeCount = await panel
    .locator("script, iframe, object, embed, [srcdoc], [onerror], [onclick], [onload]")
    .count();
  expect(unsafeCount).toBe(0);
  expect(await page.evaluate(() => window.__codexXss)).toBe(0);
  expect(pageErrors).toEqual([]);
  expect(requests.every((url) => new URL(url).origin === origin)).toBe(true);

  await panel.locator("#file-input").setInputFiles({
    name: "safe-upload.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello from Home Assistant"),
  });
  await expect(panel.locator("#attachment-chip-list")).toContainText("safe-upload.txt");
  expect(pageErrors).toEqual([]);
});

for (const viewport of [
  { name: "desktop", width: 1680, height: 720 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`keeps a long transcript inside its own ${viewport.name} scrollport`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);

  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    const threadId = panel._selectedThreadId;
    panel._events = Array.from({ length: 36 }, (_, index) => ({
      event_id: `evt_scroll_${index}`,
      thread_id: threadId,
      sequence: 10_000 + index,
      event_type: "message.completed",
      payload: {
        text: `Transcript entry ${index + 1}: ${"A long Home Assistant transcript must remain inside the Codex conversation scroller. ".repeat(9)}`,
      },
      timestamp: new Date().toISOString(),
    }));
    panel._forceMessageRebuild = true;
    panel._renderMessages();
  });

  const scrollContract = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const documentScroller = document.scrollingElement;
    const shell = root?.querySelector(".shell");
    const main = root?.querySelector(".main-pane");
    const transcript = root?.getElementById("conversation-scroll");
    const composer = root?.querySelector(".composer-shell");
    const composerTopBefore = composer?.getBoundingClientRect().top || 0;
    if (transcript) transcript.scrollTop = Math.floor(transcript.scrollHeight / 2);
    return {
      documentClientHeight: documentScroller?.clientHeight || 0,
      documentScrollHeight: documentScroller?.scrollHeight || 0,
      shellHeight: shell?.getBoundingClientRect().height || 0,
      mainScrollHeight: main?.scrollHeight || 0,
      mainClientHeight: main?.clientHeight || 0,
      transcriptScrollHeight: transcript?.scrollHeight || 0,
      transcriptClientHeight: transcript?.clientHeight || 0,
      transcriptScrollTop: transcript?.scrollTop || 0,
      composerTopBefore,
      composerTopAfter: composer?.getBoundingClientRect().top || 0,
    };
  });

  expect(scrollContract.documentScrollHeight).toBeLessThanOrEqual(scrollContract.documentClientHeight + 1);
  expect(scrollContract.shellHeight).toBeCloseTo(scrollContract.documentClientHeight, 0);
  expect(scrollContract.mainScrollHeight).toBeLessThanOrEqual(scrollContract.mainClientHeight + 1);
  expect(scrollContract.transcriptScrollHeight).toBeGreaterThan(scrollContract.transcriptClientHeight);
  expect(scrollContract.transcriptScrollTop).toBeGreaterThan(0);
  expect(scrollContract.composerTopAfter).toBeCloseTo(scrollContract.composerTopBefore, 1);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._scrollMessagesToBottom(true);
    panel._hass.connection.sendMessagePromise = async () => {
      throw new Error("Bridge connection lost");
    };
    panel._setError("The connection was interrupted.", { retryable: true });
  });
  const error = page.locator("codex-bridge-panel").locator("#error-strip");
  await expect(error).toBeInViewport({ ratio: 1 });
  await expect(error.getByRole("button", { name: "Retry connection" })).toBeInViewport({ ratio: 1 });
  });
}

test("downloads a cached generated-image preview inside the user activation", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);

  await page.evaluate(() => {
    const generatedImage = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 77]);
    const originalAnchorClick = HTMLAnchorElement.prototype.click;
    window.__codexBridgeCachedDownloadUserActive = null;
    window.__codexBridgeCachedDownloadFetched = false;
    HTMLAnchorElement.prototype.click = function () {
      window.__codexBridgeCachedDownloadUserActive = navigator.userActivation.isActive;
      return originalAnchorClick.call(this);
    };
    const panel = document.querySelector("codex-bridge-panel");
    // The synthetic artifact exists only in this browser fixture. Prevent the
    // harness poller from replacing it with the server's ordinary artifact
    // list while the intentionally delayed authenticated fetch is in flight.
    panel._stopPolling();
    const artifact = {
      artifact_id: "art_generated_cached",
      filename: "generated-cached.png",
      relative_path: "generated-cached.png",
      mime_type: "image/png",
      size_bytes: generatedImage.byteLength,
      source: "generated_image",
    };
    const harnessFetch = window.fetch;
    window.fetch = async (url, init) => {
      const pathname = new URL(String(url), window.location.origin).pathname;
      if (pathname.endsWith("/artifacts/art_generated_cached")) {
        window.__codexBridgeCachedDownloadFetched = true;
      }
      return harnessFetch(url, init);
    };
    panel._artifacts = [...panel._artifacts, artifact];
    panel._artifactPreview = {
      artifactId: artifact.artifact_id,
      filename: artifact.filename,
      kind: "image",
      blob: new Blob([generatedImage], { type: artifact.mime_type }),
    };
    const card = panel._renderGeneratedImageCard({ sequence: 19_999, payload: {} }, artifact);
    panel.shadowRoot.getElementById("message-list").append(card);
  });

  const downloadEvent = page.waitForEvent("download");
  await page.locator("codex-bridge-panel").locator(
    '.generated-image-download[data-artifact-id="art_generated_cached"]'
  ).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("generated-cached.png");
  expect([...await readFile(await download.path())]).toEqual([137, 80, 78, 71, 13, 10, 26, 10, 77]);
  expect(await page.evaluate(() => window.__codexBridgeCachedDownloadUserActive)).toBe(true);
  expect(await page.evaluate(() => window.__codexBridgeCachedDownloadFetched)).toBe(false);
  await expect(page.locator('a[download="generated-cached.png"]')).toBeAttached();
});

test("downloads a generated image through the authenticated browser artifact path", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);

  await page.evaluate(() => {
    const originalAnchorClick = HTMLAnchorElement.prototype.click;
    window.__codexBridgeDownloadAnchorConnected = null;
    HTMLAnchorElement.prototype.click = function () {
      window.__codexBridgeDownloadAnchorConnected = this.isConnected;
      return originalAnchorClick.call(this);
    };
    const panel = document.querySelector("codex-bridge-panel");
    // This delayed synthetic fetch must not race the harness's ordinary
    // artifact refresh, which correctly knows nothing about this fixture row.
    panel._stopPolling();
    const artifact = {
      artifact_id: "art_generated_download",
      filename: "generated-tree.png",
      relative_path: "generated-tree.png",
      mime_type: "image/png",
      size_bytes: 12,
      source: "generated_image",
    };
    const harnessFetch = window.fetch;
    window.fetch = async (url, init) => {
      const pathname = new URL(String(url), window.location.origin).pathname;
      if (pathname.endsWith("/artifacts/art_generated_download")) {
        await new Promise((resolveRequest) => setTimeout(resolveRequest, 75));
        const generatedImage = new Uint8Array(2 * 1024 * 1024);
        generatedImage.set([137, 80, 78, 71, 13, 10, 26, 10]);
        generatedImage[generatedImage.length - 1] = 77;
        return new Response(generatedImage, {
          status: 200,
          headers: {
            "Content-Disposition": 'attachment; filename="generated-tree.png"',
            "Content-Type": "image/png",
          },
        });
      }
      return harnessFetch(url, init);
    };
    panel._artifacts = [...panel._artifacts, artifact];
    const card = panel._renderGeneratedImageCard({ sequence: 20_000, payload: {} }, artifact);
    panel.shadowRoot.getElementById("message-list").append(card);
  });

  const downloadButton = page.locator("codex-bridge-panel").locator(
    '.generated-image-download[data-artifact-id="art_generated_download"]'
  );
  await expect(downloadButton).toHaveAttribute(
    "aria-label",
    "Prepare download generated image generated-tree.png"
  );
  await downloadButton.click();
  await expect(downloadButton).toHaveAttribute("aria-label", "Save generated image generated-tree.png");

  const downloadEvent = page.waitForEvent("download");
  await downloadButton.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("generated-tree.png");
  const downloadedBytes = await readFile(await download.path());
  expect(downloadedBytes).toHaveLength(2 * 1024 * 1024);
  expect([...downloadedBytes.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
  expect(downloadedBytes.at(-1)).toBe(77);
  expect(await page.evaluate(() => window.__codexBridgeDownloadAnchorConnected)).toBe(true);
  await expect(page.locator('a[download="generated-tree.png"]')).toBeAttached();
});

test("shows the prepare and save states on a generic Files-row download", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);

  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._stopPolling();
    const artifact = {
      artifact_id: "art_generic_download",
      filename: "workspace-output.bin",
      relative_path: "workspace-output.bin",
      mime_type: "application/octet-stream",
      size_bytes: 16,
    };
    const harnessFetch = window.fetch;
    window.fetch = async (url, init) => {
      const pathname = new URL(String(url), window.location.origin).pathname;
      if (pathname.endsWith("/artifacts/art_generic_download")) {
        await new Promise((resolveRequest) => setTimeout(resolveRequest, 75));
        return new Response(new Uint8Array([1, 2, 3, 4]), {
          status: 200,
          headers: {
            "Content-Disposition": 'attachment; filename="workspace-output.bin"',
            "Content-Type": "application/octet-stream",
          },
        });
      }
      return harnessFetch(url, init);
    };
    panel._artifacts = [...panel._artifacts, artifact];
    panel._sideTab = "files";
    panel._renderSideTabs();
    panel._renderArtifacts();
  });

  const panel = page.locator("codex-bridge-panel");
  const downloadButton = panel.locator(
    '.download-button[data-artifact-id="art_generic_download"]'
  );
  await expect(downloadButton).toHaveText("Prepare download");

  await downloadButton.click();
  await expect(downloadButton).toBeEnabled();
  await expect(downloadButton).toHaveText("Save file");
});

test("renders a local PDF on canvas without embeds or off-origin requests", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const requests = [];
  const workerResponses = [];
  const pageErrors = [];
  page.on("request", (request) => requests.push(request.url()));
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === "/frontend/src/codex-bridge-pdf-worker.js") {
      workerResponses.push(response.status());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await expect(page.locator("codex-bridge-panel").locator("#message-list")).toBeVisible();
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const fixture = createMinimalPdfFixture().toString("base64");
  await page.evaluate(async (encodedFixture) => {
    const bytes = Uint8Array.from(atob(encodedFixture), (character) => character.charCodeAt(0));
    const artifact = {
      artifact_id: "art_local_pdf",
      filename: "local-preview.pdf",
      mime_type: "application/pdf",
      relative_path: "local-preview.pdf",
      size_bytes: bytes.byteLength,
    };
    const harnessFetch = window.fetch;
    window.fetch = async (url, init) => {
      const pathname = new URL(String(url), window.location.origin).pathname;
      if (pathname.endsWith("/artifacts/art_local_pdf")) {
        return new Response(bytes, {
          status: 200,
          headers: {
            "Content-Length": String(bytes.byteLength),
            "Content-Type": "application/pdf",
          },
        });
      }
      return harnessFetch(url, init);
    };
    const panel = document.querySelector("codex-bridge-panel");
    window.__codexHarness.addArtifact(panel._selectedThreadId, artifact);
    panel._artifacts = [...panel._artifacts, artifact];
    await panel._selectArtifact(artifact.artifact_id);
  }, fixture);

  await panel.locator("#side-tab-files").click();
  const preview = panel.locator(".pdf-preview-shell");
  const canvas = preview.locator("canvas.pdf-preview-canvas");
  await expect(canvas).toBeVisible();
  expect(await canvas.evaluate((element) => element.width > 0 && element.height > 0)).toBe(true);
  await expect(preview.getByRole("toolbar", { name: "PDF preview controls" })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Previous page" })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Next page" })).toBeVisible();
  await expect(preview.locator(".pdf-preview-page-status").first()).toHaveText("1 / 1");
  await expect(preview.locator(".pdf-preview-zoom-status")).toHaveText("100%");
  await expect(preview.getByRole("button", { name: "Zoom out" })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Zoom in" })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Open PDF in a new tab" })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Download local-preview.pdf" })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await panel.locator("#mobile-context-toggle").click();
  await expect(panel.locator("#context-drawer")).toHaveAttribute("aria-hidden", "false");
  const mobileToolbar = await preview.getByRole("toolbar", { name: "PDF preview controls" }).evaluate((toolbar) => ({
    fits: toolbar.scrollWidth <= toolbar.clientWidth + 1,
    minimumTarget: Math.min(
      ...Array.from(toolbar.querySelectorAll("button"))
        .filter((button) => !button.hidden && button.getClientRects().length)
        .map((button) => button.getBoundingClientRect().height)
    ),
  }));
  expect(mobileToolbar.fits).toBe(true);
  expect(mobileToolbar.minimumTarget).toBeGreaterThanOrEqual(44);

  expect(workerResponses).toContain(200);
  expect(pageErrors).toEqual([]);
  const networkRequests = requests.filter((url) => /^https?:/u.test(url));
  expect(networkRequests).not.toHaveLength(0);
  expect(networkRequests.every((url) => new URL(url).origin === origin)).toBe(true);
  expect(await page.locator("iframe, object, embed").count()).toBe(0);
  expect(await panel.locator("iframe, object, embed").count()).toBe(0);
});

test("runs the Home Assistant first-run and ChatGPT device sign-in flow without exposing runtime secrets", async ({ page, context }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await panel.locator("#side-tab-system").click();
  const onboarding = panel.locator("#onboarding");
  const auth = panel.locator("#auth-panel");

  await expect(onboarding).toContainText("App connected");
  await expect(onboarding).toContainText("Integration confirmed");
  await expect(onboarding).toContainText("Bridge ready");
  await expect(onboarding).toContainText("Codex ready");
  await expect(panel.locator("#runtime-strip")).toBeHidden();
  await expect
    .poll(() =>
      page.evaluate(() => window.__codexHarness.subscriptions.map((subscription) => subscription.scopes || [])),
    )
    .toContainEqual(expect.arrayContaining(["auth", "runtime"]));
  const calls = await page.evaluate(() => window.__codexHarness.calls);
  const globalSubscription = calls.findIndex(
    (call) => call.kind === "subscribe" && call.payload.scopes?.includes("auth") && call.payload.scopes?.includes("runtime"),
  );
  const firstChatData = calls.findIndex((call) => call.kind === "ws" && ["codex_bridge/list_projects", "codex_bridge/list_threads"].includes(call.type));
  expect(globalSubscription).toBeGreaterThanOrEqual(0);
  expect(firstChatData).toBeGreaterThan(globalSubscription);

  await auth.locator('button[data-action="confirm-sign-out"]').click();
  await auth.locator('button[data-action="sign-out"]').click();
  await expect(auth.locator('button[data-action="start-auth-login"]')).toBeVisible();
  await auth.locator('button[data-action="start-auth-login"]').click();
  await expect(auth).toContainText("HOME-ASSISTANT");
  await expect(auth).toContainText("phone or another signed-in device");
  await auth.locator('button[data-action="copy-auth-code"]').click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("HOME-ASSISTANT");

  await page.evaluate(() => {
    window.__openedChatGpt = null;
    window.open = (url) => {
      window.__openedChatGpt = String(url);
      return null;
    };
  });
  await auth.locator('button[data-action="open-chatgpt"]').click();
  await expect.poll(() => page.evaluate(() => window.__openedChatGpt)).toBe("https://auth.openai.com/codex/device");

  await auth.locator('button[data-action="cancel-sign-in"]').click();
  await expect(auth).not.toContainText("HOME-ASSISTANT");
  await expect(auth.locator('button[data-action="start-auth-login"]')).toBeVisible();

  await auth.locator('button[data-action="start-auth-login"]').click();
  await page.evaluate(() => window.__codexHarness.completeLogin());
  await expect(auth).toContainText("ChatGPT connected");
  await auth.locator('button[data-action="confirm-sign-out"]').click();
  await auth.locator('button[data-action="sign-out"]').click();
  await expect(auth).toContainText("ChatGPT sign-in");

  const renderedText = await panel.locator(".shell").textContent();
  for (const privateFragment of ["C:\\", "Windows", "VM", "API key", "PAT", "access_token", "auth.openai.com/codex/device"]) {
    expect(renderedText).not.toContain(privateFragment);
  }
});

for (const viewport of [{ width: 1755, height: 850 }, { width: 1024, height: 600 }, { width: 390, height: 844 }]) {
  test(`keeps expired sign-in status and recovery controls visible at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    await selectHarnessThread(page);
    await page.evaluate(() => {
      window.__codexHarness.updateThread("thr_vba_1", {
        status: "error",
        last_error: "Codex sign-in expired. Start a new sign-in from Home Assistant.",
      });
      window.__codexHarness.emitThreadEvent("thr_vba_1", "run.failed", {
        run_id: "expired-run", failure_type: "auth.expired", auth_required: true,
        error: "Codex sign-in expired. Start a new sign-in from Home Assistant.",
      });
      window.__codexHarness.expireLogin();
    });
    const banner = panel.locator("#status-banner");
    await expect(panel.locator("#account-pill")).toHaveText("ChatGPT not connected");
    await expect(banner).toContainText("Your ChatGPT sign-in expired");
    const signIn = banner.getByRole("button", { name: "Sign in with ChatGPT", exact: true });
    await expect(signIn).toBeInViewport({ ratio: 1 });
    if (viewport.width < 600) {
      expect((await signIn.boundingBox()).height).toBeGreaterThanOrEqual(44);
    }
    const geometry = await banner.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const scroll = element.parentElement.querySelector("#conversation-scroll").getBoundingClientRect();
      return { height: element.clientHeight, contentHeight: element.scrollHeight, bottom: rect.bottom, scrollTop: scroll.top };
    });
    expect(geometry.contentHeight).toBeLessThanOrEqual(geometry.height + 1);
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.scrollTop + 1);
    await page.screenshot({ path: testInfo.outputPath("expired-sign-in.png"), fullPage: true });
    await signIn.click();
    await expect(banner.getByRole("button", { name: "Open ChatGPT", exact: true })).toBeInViewport({ ratio: 1 });
    await page.evaluate(() => window.__codexHarness.completeLogin());
    await expect(panel.locator("#account-pill")).toHaveText("ChatGPT Pro");
    await expect(banner).toBeHidden();
  });
}

test("creates a workspace project and first chat at compact widths in both colour schemes", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");

  await panel.locator("#new-project-button").click();
  await panel.locator("#project-name-input").fill("Home lab notes");
  await panel.locator('button[data-action="save-project"]').click();
  await expect(panel.locator("#project-section")).toContainText("Home lab notes");

  const createdProject = panel.locator(".project-shell").filter({ hasText: "Home lab notes" });
  await page.setViewportSize({ width: 1120, height: 1000 });
  await createdProject.locator('button[data-action="toggle-project-actions"]').click();
  await createdProject.locator('button[data-action="new-chat"]').click();
  await panel.locator("#thread-title-input").fill("First Home Assistant chat");
  const formLayout = await panel.locator("#thread-form-panel").evaluate((form) => {
    const actions = form.querySelector(".form-actions");
    const create = form.querySelector('[data-action="save-thread"]');
    const close = form.querySelector('[data-action="cancel-thread-form"]');
    const formRect = form.getBoundingClientRect();
    const createRect = create?.getBoundingClientRect();
    const closeRect = close?.getBoundingClientRect();
    return {
      actionsFit: Boolean(actions && actions.scrollWidth <= actions.clientWidth),
      createFits: Boolean(createRect && createRect.right <= formRect.right),
      closeFits: Boolean(closeRect && closeRect.right <= formRect.right),
      createWhiteSpace: create ? getComputedStyle(create).whiteSpace : "",
      createWidth: createRect?.width || 0,
      createHeight: createRect?.height || 0,
    };
  });
  expect(formLayout).toMatchObject({
    actionsFit: true,
    createFits: true,
    closeFits: true,
    createWhiteSpace: "nowrap",
  });
  expect(formLayout.createWidth).toBeGreaterThan(100);
  expect(formLayout.createHeight).toBeLessThanOrEqual(44);
  await panel.locator('button[data-action="save-thread"]').click();
  await expect(panel.locator("#thread-title-label")).toContainText("First Home Assistant chat");
  const refresh = panel.locator("#refresh-thread-button");
  await expect(refresh).toHaveAttribute("aria-label", "Refresh");
  await expect(refresh.locator("svg")).toBeVisible();
  await expect(refresh).toHaveCSS("width", "32px");
  await expect(refresh).toHaveCSS("height", "32px");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.locator("#runtime-strip")).toBeHidden();

  await page.emulateMedia({ colorScheme: "light" });
  await expect(panel.locator("#runtime-strip")).toBeHidden();
});

test("exposes run stages through one accessible tooltip in both colour schemes", async ({ page }) => {
  const palettes = [];
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "no-preference" });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await seedRunStageActivity(page);

    const panel = page.locator("codex-bridge-panel");
    const chip = panel.locator("#run-step-chip");
    const tooltip = panel.locator("#run-step-tooltip");
    const genericTooltip = panel.locator("#tooltip-layer");
    await expect(chip).toHaveText(/Step 2 \/ 3/);
    await expect(chip).toHaveAttribute("aria-haspopup", "dialog");
    await expect(chip).toHaveAttribute("aria-expanded", "false");
    await expect(chip).toHaveAttribute("aria-controls", "run-step-tooltip");
    await expect(chip).not.toHaveAttribute("aria-describedby", /(?:^|\s)run-step-tooltip(?:\s|$)/);

    await chip.hover();
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText("Inspect returned pages");
    await expect(tooltip).toContainText("Searching the web");
    await expect(genericTooltip).toBeHidden();

    await page.mouse.move(1, 1);
    await chip.focus();
    await expect(chip).toBeFocused();
    await expect(tooltip).toBeVisible();
    await expect(genericTooltip).toBeHidden();
    await expect(chip).not.toHaveAttribute("aria-describedby", /(?:^|\s)tooltip-layer(?:\s|$)/);

    await chip.click();
    await expect(chip).toHaveAttribute("aria-expanded", "true");
    await expect(chip).toHaveAttribute("aria-describedby", /(?:^|\s)run-step-tooltip(?:\s|$)/);
    await expect(chip.locator(".." )).toHaveClass(/open/);
    await expect(tooltip).toBeVisible();
    await expect(genericTooltip).toBeHidden();

    const palette = await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      const root = panel?.shadowRoot;
      const chip = root?.querySelector("#run-step-chip");
      const tooltip = root?.querySelector("#run-step-tooltip");
      const chipStyle = chip ? getComputedStyle(chip) : null;
      const tooltipStyle = tooltip ? getComputedStyle(tooltip) : null;
      const hostStyle = panel ? getComputedStyle(panel) : null;
      return {
        chipColor: chipStyle?.color || "",
        chipBackground: chipStyle?.backgroundColor || "",
        tooltipColor: tooltipStyle?.color || "",
        tooltipBackground: tooltipStyle?.backgroundColor || "",
        tooltipBorder: tooltipStyle?.borderTopColor || "",
        textToken: hostStyle?.getPropertyValue("--text-color").trim() || "",
        surfaceToken: hostStyle?.getPropertyValue("--surface-bg").trim() || "",
      };
    });
    for (const value of Object.values(palette)) {
      expect(value).not.toBe("");
      expect(value).not.toBe("rgba(0, 0, 0, 0)");
    }
    expect(palette.tooltipColor).not.toBe(palette.tooltipBackground);
    expect(palette.chipColor).not.toBe(palette.chipBackground);
    palettes.push(palette);
  }

  expect(palettes[0].textToken).not.toBe(palettes[1].textToken);
  expect(palettes[0].surfaceToken).not.toBe(palettes[1].surfaceToken);
});

test("keeps run-stage details within the mobile viewport and disables motion when requested", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);

  const panel = page.locator("codex-bridge-panel");
  const chip = panel.locator("#run-step-chip");
  const tooltip = panel.locator("#run-step-tooltip");
  await chip.click();
  await expect(chip).toHaveAttribute("aria-expanded", "true");
  await expect(tooltip).toBeVisible();

  const mobileLayout = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const tooltip = root?.querySelector("#run-step-tooltip");
    const shell = root?.querySelector(".shell");
    const spinner = root?.querySelector(".step-spinner");
    const tooltipStyle = tooltip ? getComputedStyle(tooltip) : null;
    const chipStyle = root?.querySelector("#run-step-chip") ? getComputedStyle(root.querySelector("#run-step-chip")) : null;
    const tooltipBox = tooltip?.getBoundingClientRect();
    return {
      tooltipRight: tooltipBox?.right || 0,
      tooltipWidth: tooltipBox?.width || 0,
      viewportWidth: window.innerWidth,
      shellFits: Boolean(shell && shell.scrollWidth <= shell.clientWidth + 1),
      tooltipTransition: tooltipStyle?.transitionDuration || "",
      tooltipAnimation: tooltipStyle?.animationName || "",
      spinnerAnimation: spinner ? getComputedStyle(spinner).animationName : "",
      chipTransition: chipStyle?.transitionDuration || "",
      chipHeight: root?.querySelector("#run-step-chip")?.getBoundingClientRect().height || 0,
    };
  });
  expect(mobileLayout.tooltipRight).toBeLessThanOrEqual(mobileLayout.viewportWidth + 1);
  expect(mobileLayout.tooltipWidth).toBeLessThanOrEqual(mobileLayout.viewportWidth - 24);
  expect(mobileLayout.shellFits).toBe(true);
  expect(mobileLayout.tooltipTransition).toMatch(/0\.01ms|1e-05s|0s/);
  expect(mobileLayout.chipTransition).toMatch(/0\.01ms|1e-05s|0s/);
  expect(mobileLayout.spinnerAnimation).toBe("none");
  expect(mobileLayout.chipHeight).toBeGreaterThanOrEqual(44);
});

test("shows inline command approvals and user questions through the HA websocket boundary", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await selectHarnessThread(page);

  const interactions = panel.locator("#interaction-region");
  const command = interactions.locator('[data-interaction-id="int_command_harness"]');
  const question = interactions.locator('[data-interaction-id="int_question_harness"]');
  await expect(command).toContainText("Run the focused checks");
  await expect(command).toContainText("python -m pytest bridge_service/tests -q");
  await expect(command).toContainText("custom_components/codex_bridge");
  await expect(question).toContainText("Which files should Codex update?");

  await command.locator('[data-action="accept-interaction"]').click();
  await expect(command).toHaveCount(0);
  const decisions = await websocketCalls(page, "codex_bridge/decide_interaction");
  expect(decisions).toHaveLength(1);
  expect(decisions[0].payload).toMatchObject({
    interaction_id: "int_command_harness",
    thread_id: "thr_vba_1",
    decision: "accept",
  });
  expect(decisions[0].payload).not.toHaveProperty("run_id");
  expect(decisions[0].payload).not.toHaveProperty("turn_id");
  expect(decisions[0].payload).not.toHaveProperty("item_id");
  expect(decisions[0].payload.client_request_id).toMatch(/^[A-Za-z0-9_.:-]{1,256}$/);

  await question.getByLabel("Source and tests").check();
  await question.locator('[data-action="answer-interaction"]').click();
  await expect(question).toHaveCount(0);
  const answers = await websocketCalls(page, "codex_bridge/answer_interaction");
  expect(answers).toHaveLength(1);
  expect(answers[0].payload).toMatchObject({
    interaction_id: "int_question_harness",
    thread_id: "thr_vba_1",
    answers: [{ question_id: "scope", values: ["Source and tests"] }],
  });
  expect(answers[0].payload).not.toHaveProperty("run_id");
  expect(answers[0].payload).not.toHaveProperty("turn_id");
  expect(answers[0].payload).not.toHaveProperty("item_id");
  expect(answers[0].payload.client_request_id).toMatch(/^[A-Za-z0-9_.:-]{1,256}$/);
});

test("keeps the active approval actions visible at the 1280px desktop layout", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const approvalAction = panel.locator('[data-interaction-id="int_command_harness"] [data-action="accept-interaction"]');
  await expect.poll(async () => approvalAction.evaluate((action) => {
    const region = action.closest("#interaction-region");
    const actionBox = action.getBoundingClientRect();
    const regionBox = region?.getBoundingClientRect();
    return Boolean(
      regionBox &&
      action.isConnected &&
      actionBox.top >= regionBox.top &&
      actionBox.bottom <= Math.min(regionBox.bottom, window.innerHeight)
    );
  }), {
    message: "active approval action should settle inside its visible decision region",
  }).toBe(true);
});

test("keeps the conversation rail stable and opens Activity as a compact-width drawer", async ({ page }) => {
  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    const panel = page.locator("codex-bridge-panel");
    await expect(panel.locator("#archived-chat-list")).toBeHidden();
    await expect(panel.locator("#onboarding-shell")).toBeHidden();

    const navigationToggle = panel.locator("#mobile-nav-toggle");
    const contextToggle = panel.locator("#mobile-context-toggle");
    const context = panel.locator("#context-drawer");
    const scrim = panel.locator("#mobile-drawer-scrim");
    await expect(navigationToggle).toBeHidden();
    await expect(contextToggle).toBeVisible();
    await expect(contextToggle).toHaveAccessibleName("Context");
    await expect(contextToggle).toHaveAttribute("aria-expanded", "false");
    await expect(context).toHaveAttribute("aria-hidden", "true");
    await expect(panel.locator("#workspace-drawer")).not.toHaveAttribute("aria-hidden");
    await expect(panel.locator("#workspace-drawer")).toHaveJSProperty("inert", false);
    await expect(scrim).toBeHidden();

    const compactLayout = await page.evaluate(() => {
      const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
      const rect = (selector) => root?.querySelector(selector)?.getBoundingClientRect();
      const rail = rect(".rail-pane");
      const main = rect(".main-pane");
      const side = rect(".side-pane");
      const messages = rect("#message-list");
      const composer = rect(".composer-shell");
      const toolbar = root?.querySelector("#compact-toolbar");
      const bubble = root?.querySelector(".bubble-text");
      const railElement = root?.querySelector(".rail-pane");
      const mainElement = root?.querySelector(".main-pane");
      const sideElement = root?.querySelector(".side-pane");
      return {
        railWidth: rail?.width || 0,
        mainWidth: main?.width || 0,
        sideOffCanvas: Boolean(side && side.left >= window.innerWidth - 1),
        readingMeasure: messages?.width || 0,
        composerMeasure: composer?.width || 0,
        toolbarInComposer: Boolean(toolbar && root?.querySelector(".composer-shell")?.contains(toolbar)),
        proseFont: bubble ? getComputedStyle(bubble).fontFamily : "",
        railBackground: railElement ? getComputedStyle(railElement).backgroundColor : "",
        mainBackground: mainElement ? getComputedStyle(mainElement).backgroundColor : "",
        sideBackground: sideElement ? getComputedStyle(sideElement).backgroundColor : "",
      };
    });
    expect(compactLayout.railWidth).toBeGreaterThanOrEqual(300);
    expect(compactLayout.railWidth).toBeLessThanOrEqual(330);
    expect(compactLayout.mainWidth).toBeCloseTo(width - compactLayout.railWidth, 0);
    expect(compactLayout.sideOffCanvas).toBe(true);
    expect(compactLayout.readingMeasure).toBeCloseTo(840, 0);
    expect(compactLayout.composerMeasure).toBeCloseTo(840, 0);
    expect(compactLayout.toolbarInComposer).toBe(true);
    expect(compactLayout.proseFont).not.toMatch(/monospace|consolas|courier/i);
    expect(compactLayout.railBackground).not.toBe(compactLayout.mainBackground);
    expect(compactLayout.sideBackground).not.toBe(compactLayout.mainBackground);

    await contextToggle.click();
    await expect(contextToggle).toHaveAttribute("aria-expanded", "true");
    await expect(context).toHaveAttribute("aria-hidden", "false");
    await expect(panel.locator("#workspace-drawer")).toHaveAttribute("aria-hidden", "true");
    await expect(panel.locator("#workspace-drawer")).toHaveJSProperty("inert", true);
    await expect(scrim).toBeVisible();
    await expect.poll(() => context.evaluate((drawer) => drawer.getBoundingClientRect().right))
      .toBeLessThanOrEqual(width + 1);
    const openDrawer = await context.evaluate((drawer) => {
      const box = drawer.getBoundingClientRect();
      return { left: box.left, right: box.right, width: box.width, viewport: window.innerWidth };
    });
    expect(openDrawer.width).toBeGreaterThan(0);
    expect(openDrawer.right).toBeLessThanOrEqual(openDrawer.viewport + 1);
    expect(openDrawer.left).toBeLessThan(openDrawer.viewport);

    await scrim.click({ position: { x: 10, y: 420 } });
    await expect(contextToggle).toHaveAttribute("aria-expanded", "false");
    await expect(context).toHaveAttribute("aria-hidden", "true");
    await expect(panel.locator("#workspace-drawer")).not.toHaveAttribute("aria-hidden");
    await expect(panel.locator("#workspace-drawer")).toHaveJSProperty("inert", false);
    await expect(scrim).toBeHidden();
    await expect(contextToggle).toBeFocused();
  }

  await page.setViewportSize({ width: 1280, height: 1000 });
  const panel = page.locator("codex-bridge-panel");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(220);
  const navigationToggle = panel.locator("#mobile-nav-toggle");
  const contextToggle = panel.locator("#mobile-context-toggle");
  const navigation = panel.locator("#workspace-drawer");
  const context = panel.locator("#context-drawer");
  const scrim = panel.locator("#mobile-drawer-scrim");
  await expect(navigationToggle).toBeVisible();
  await expect(contextToggle).toBeVisible();
  await expect(navigationToggle).toHaveAccessibleName("Chats");
  await expect(contextToggle).toHaveAccessibleName("Context");
  await expect(navigation).toHaveAttribute("aria-hidden", "true");
  await expect(context).toHaveAttribute("aria-hidden", "true");
  await expect(scrim).toBeHidden();

  const closedMobile = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const shell = root?.querySelector(".shell");
    const main = root?.querySelector(".main-pane")?.getBoundingClientRect();
    const rail = root?.querySelector(".rail-pane")?.getBoundingClientRect();
    const side = root?.querySelector(".side-pane")?.getBoundingClientRect();
    return {
      chatFillsViewport: Boolean(main && main.height >= window.innerHeight - 2),
      noStackedPanels: Boolean(shell && shell.scrollHeight <= shell.clientHeight + 1 && rail && side && rail.right <= 0 && side.left >= window.innerWidth),
    };
  });
  expect(closedMobile).toEqual({ chatFillsViewport: true, noStackedPanels: true });

  await navigationToggle.click();
  await expect(navigationToggle).toHaveAttribute("aria-expanded", "true");
  await expect(navigation).toHaveAttribute("aria-hidden", "false");
  await expect(scrim).toBeVisible();
  await expect(navigation.locator("#new-direct-chat-button")).toBeFocused();
  const reachableNavigationActions = await navigation.locator("button[data-action]").evaluateAll((buttons) => buttons
    .filter((button) => !button.hidden && button.getClientRects().length)
    .map((button) => {
      const rect = button.getBoundingClientRect();
      return {
        action: button.dataset.action,
        width: rect.width,
        height: rect.height,
      };
    }));
  expect(reachableNavigationActions.length).toBeGreaterThan(0);
  expect(reachableNavigationActions.every(({ width, height }) => width >= 44 && height >= 44)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(navigationToggle).toHaveAttribute("aria-expanded", "false");
  await expect(scrim).toBeHidden();
  await expect(navigationToggle).toBeFocused();

  await navigationToggle.click();
  await navigation.locator('[data-action="select-thread"]').first().click();
  await expect(navigationToggle).toHaveAttribute("aria-expanded", "false");

  await contextToggle.click();
  await expect(contextToggle).toHaveAttribute("aria-expanded", "true");
  await expect(context).toHaveAttribute("aria-hidden", "false");
  await expect(scrim).toBeVisible();
  await scrim.click({ position: { x: 10, y: 420 } });
  await expect(contextToggle).toHaveAttribute("aria-expanded", "false");
  await expect(contextToggle).toBeFocused();
});

test("keeps drawer state and accessibility exact at the responsive boundaries", async ({ page }) => {
  const snapshot = async (width) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    return page.evaluate(() => {
      const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
      const navigation = root?.querySelector("#workspace-drawer");
      const context = root?.querySelector("#context-drawer");
      const navToggle = root?.querySelector("#mobile-nav-toggle");
      const contextToggle = root?.querySelector("#mobile-context-toggle");
      const contextRect = context?.getBoundingClientRect();
      return {
        navigationToggleVisible: Boolean(navToggle?.getClientRects().length),
        contextToggleVisible: Boolean(contextToggle?.getClientRects().length),
        navigationHidden: navigation?.getAttribute("aria-hidden"),
        contextHidden: context?.getAttribute("aria-hidden"),
        navigationInert: Boolean(navigation?.inert),
        contextInert: Boolean(context?.inert),
        contextLeft: contextRect?.left || 0,
        contextRight: contextRect?.right || 0,
        viewport: window.innerWidth,
      };
    });
  };

  const mobile = await snapshot(880);
  expect(mobile.navigationToggleVisible).toBe(true);
  expect(mobile.contextToggleVisible).toBe(true);
  expect(mobile.navigationHidden).toBe("true");
  expect(mobile.contextHidden).toBe("true");
  expect(mobile.navigationInert).toBe(true);
  expect(mobile.contextInert).toBe(true);

  for (const width of [881, 1120]) {
    const staticContext = await snapshot(width);
    expect(staticContext.navigationToggleVisible).toBe(false);
    expect(staticContext.contextToggleVisible).toBe(false);
    expect(staticContext.navigationHidden).toBeNull();
    expect(staticContext.contextHidden).toBeNull();
    expect(staticContext.navigationInert).toBe(false);
    expect(staticContext.contextInert).toBe(false);
    expect(staticContext.contextLeft).toBeGreaterThanOrEqual(0);
    expect(staticContext.contextRight).toBeLessThanOrEqual(staticContext.viewport + 1);
  }

  const compactDesktop = await snapshot(1121);
  expect(compactDesktop.navigationToggleVisible).toBe(false);
  expect(compactDesktop.contextToggleVisible).toBe(true);
  expect(compactDesktop.navigationHidden).toBeNull();
  expect(compactDesktop.contextHidden).toBe("true");
  expect(compactDesktop.navigationInert).toBe(false);
  expect(compactDesktop.contextInert).toBe(true);
  expect(compactDesktop.contextLeft).toBeGreaterThanOrEqual(compactDesktop.viewport - 1);
});

test("aligns the desktop workspace rails and reading edges at wide widths", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);

  const layout = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const rect = (selector) => root?.querySelector(selector)?.getBoundingClientRect();
    const rail = rect(".rail-pane");
    const side = rect(".side-pane");
    const title = rect("#thread-title-label");
    const actions = rect(".main-header .row-actions");
    const messages = rect("#message-list");
    const fontSize = (selector) => {
      const element = root?.querySelector(selector);
      return element ? getComputedStyle(element).fontSize : "";
    };
    return {
      railWidth: rail?.width || 0,
      sideWidth: side?.width || 0,
      headerTitleAligned: Boolean(title && messages && Math.abs(title.left - messages.left) <= 1),
      headerActionsAligned: Boolean(actions && messages && Math.abs(actions.right - messages.right) <= 1),
      projectFontSize: fontSize(".project-name"),
      threadFontSize: fontSize(".thread-name"),
      mainTitleFontSize: fontSize(".main-header .title"),
      sideTop: side?.top || 0,
      mainTop: rect(".main-pane")?.top || 0,
    };
  });

  expect(layout.railWidth).toBeGreaterThanOrEqual(300);
  expect(layout.railWidth).toBeLessThanOrEqual(330);
  expect(layout.sideWidth).toBeGreaterThanOrEqual(330);
  expect(layout.sideWidth).toBeLessThanOrEqual(360);
  expect(layout.headerTitleAligned).toBe(true);
  expect(layout.headerActionsAligned).toBe(true);
  expect(layout.projectFontSize).toBe("14px");
  expect(layout.threadFontSize).toBe("14px");
  expect(layout.mainTitleFontSize).toBe("16px");
  expect(layout.sideTop).toBeGreaterThanOrEqual(layout.mainTop + 60);
  expect(layout.sideTop).toBeLessThanOrEqual(layout.mainTop + 68);

  const floating = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const side = root?.querySelector(".side-pane");
    const scroll = root?.querySelector(".side-scroll");
    const style = side ? getComputedStyle(side) : null;
    return {
      radius: style?.borderTopLeftRadius || "",
      scrolling: scroll ? getComputedStyle(scroll).overflowY : "",
    };
  });
  expect(floating.radius).toBe("18px");
  expect(floating.scrolling).toMatch(/auto|scroll/);
});

test("renders an intentional empty workspace with a working new-chat action", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await page.evaluate(() => {
    const bridgePanel = document.querySelector("codex-bridge-panel");
    bridgePanel._stopPolling();
    bridgePanel._stopEventSubscription();
    bridgePanel._setSelectedThreadId(null);
    bridgePanel._activeThread = null;
    bridgePanel._threads = [];
    bridgePanel._render(true);
  });

  const emptyState = panel.locator(".empty-state-main");
  await expect(emptyState).toContainText("Start a new chat");
  await expect(emptyState.locator(".empty-state-mark svg")).toBeVisible();
  const newChat = emptyState.getByRole("button", { name: "Create a new direct chat" });
  await expect(newChat).toBeVisible();
  await expect(panel.locator("#direct-section")).toContainText("No direct chats yet.");
  await expect(panel.locator("#direct-section .section-count")).toHaveText(/0/);
  await page.screenshot({ path: testInfo.outputPath("empty-workspace.png"), fullPage: true });

  await newChat.click();
  await expect(panel.locator("#thread-form-panel")).toHaveClass(/visible/);
  await expect(panel.locator("#thread-title-input")).toBeFocused();
  const railScroll = await panel.locator(".section-scroll").evaluate((scrollport) => ({
    overflowX: getComputedStyle(scrollport).overflowX,
    overflowY: getComputedStyle(scrollport).overflowY,
    scrollbarButtonDisplay: getComputedStyle(scrollport, "::-webkit-scrollbar-button").display,
  }));
  expect(railScroll).toEqual({
    overflowX: "hidden",
    overflowY: "auto",
    scrollbarButtonDisplay: "none",
  });
});

test("retries a dropped prompt response with one stable client request id", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await selectHarnessThread(page);
  await page.evaluate(() => window.__codexHarness.dropNextPromptResponse());

  const prompt = panel.locator("#prompt-input");
  const send = panel.locator("#send-button");
  await prompt.fill("Run only the focused Home Assistant checks");
  await send.click();
  await expect.poll(() => websocketCalls(page, "codex_bridge/send_prompt")).toHaveLength(1);
  await expect(send).toBeEnabled();

  await send.click();
  await expect.poll(() => websocketCalls(page, "codex_bridge/send_prompt")).toHaveLength(2);
  const prompts = await websocketCalls(page, "codex_bridge/send_prompt");
  expect(prompts[0].payload).toMatchObject({
    thread_id: "thr_vba_1",
    prompt: "Run only the focused Home Assistant checks",
  });
  expect(prompts[1].payload.client_request_id).toBe(prompts[0].payload.client_request_id);
  expect(prompts[0].payload.client_request_id).toMatch(/^[A-Za-z0-9_.:-]{1,256}$/);
  await expect(panel.locator("#message-list")).toContainText("Run only the focused Home Assistant checks");
});

test("reconnects an interrupted interaction stream and keeps approvals keyboard-accessible at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await selectHarnessThread(page);
  const command = panel.locator('[data-interaction-id="int_command_harness"]');
  await expect(command).toBeVisible();

  const subscriptionsBefore = await page.evaluate(() => window.__codexHarness.calls.filter(
    (call) => call.kind === "subscribe" && call.payload.thread_id === "thr_vba_1",
  ).length);
  await page.evaluate(() => window.__codexHarness.stopThreadStream());
  await expect.poll(async () => page.evaluate(() => window.__codexHarness.calls.filter(
    (call) => call.kind === "subscribe" && call.payload.thread_id === "thr_vba_1",
  ).length), { timeout: 5_000 }).toBeGreaterThan(subscriptionsBefore);

  await page.evaluate(() => {
    const card = document.querySelector("codex-bridge-panel").shadowRoot
      .querySelector('[data-interaction-id="int_command_harness"] [role="alertdialog"]');
    card?.focus();
  });
  await page.keyboard.press("Escape");
  await expect.poll(() => websocketCalls(page, "codex_bridge/decide_interaction")).toHaveLength(1);
  const decision = (await websocketCalls(page, "codex_bridge/decide_interaction"))[0];
  expect(decision.payload.decision).toBe("cancel");
  await expect(command).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.querySelector("codex-bridge-panel").shadowRoot.activeElement?.id)).toBe("prompt-input");

  const shellFits = await page.evaluate(() => {
    const shell = document.querySelector("codex-bridge-panel").shadowRoot.querySelector(".shell");
    return shell.scrollWidth <= shell.clientWidth + 1;
  });
  expect(shellFits).toBe(true);
  await expect(panel.locator('[data-interaction-id="int_question_harness"]')).toBeVisible();
});

test("keeps every pending decision action reachable at desktop and mobile widths", async ({ page }) => {
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    const panel = page.locator("codex-bridge-panel");

    for (const viewport of [
      { width: 1280, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      const commandActions = panel.locator('[data-interaction-id="int_command_harness"] .decision-actions button');
      for (let index = 0; index < await commandActions.count(); index += 1) {
        const action = commandActions.nth(index);
        await action.focus();
        await action.evaluate((node) => node.scrollIntoView({ block: "center" }));
        await expect(action).toBeFocused();
        await expect.poll(() => action.evaluate((node) => {
          const regionBox = node.closest("#interaction-region")?.getBoundingClientRect();
          const main = node.closest(".main-pane");
          const mainBox = main?.getBoundingClientRect();
          const composerBox = main?.querySelector(".composer-shell")?.getBoundingClientRect();
          const actionBox = node.getBoundingClientRect();
          if (window.innerWidth > 880) {
            return Boolean(regionBox && actionBox.top >= regionBox.top && actionBox.bottom <= regionBox.bottom);
          }
          return Boolean(
            mainBox &&
            composerBox &&
            getComputedStyle(node.closest("#interaction-region")).overflowY === "visible" &&
            actionBox.top >= mainBox.top &&
            actionBox.bottom <= Math.min(mainBox.bottom, composerBox.top - 4)
          );
        })).toBe(true);
      }

      const question = panel.locator('[data-interaction-id="int_question_harness"]');
      await question.getByLabel("Source and tests").check();
      const answer = question.locator('[data-action="answer-interaction"]');
      await answer.focus();
      await answer.evaluate((node) => node.scrollIntoView({ block: "center" }));
      await expect(answer).toBeFocused();
      await expect.poll(() => answer.evaluate((node) => {
        const regionBox = node.closest("#interaction-region")?.getBoundingClientRect();
        const main = node.closest(".main-pane");
        const mainBox = main?.getBoundingClientRect();
        const composerBox = main?.querySelector(".composer-shell")?.getBoundingClientRect();
        const actionBox = node.getBoundingClientRect();
        if (window.innerWidth > 880) {
          return Boolean(regionBox && actionBox.top >= regionBox.top && actionBox.bottom <= regionBox.bottom);
        }
        return Boolean(
          mainBox &&
          composerBox &&
          getComputedStyle(node.closest("#interaction-region")).overflowY === "visible" &&
          actionBox.top >= mainBox.top &&
          actionBox.bottom <= Math.min(mainBox.bottom, composerBox.top - 4)
        );
      })).toBe(true);
    }
  }
});

test("keeps the mobile composer focused and folds diagnostics behind an accessible disclosure", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const settings = panel.locator("#composer-diagnostics");
  const summary = settings.locator("summary");
  const toolbar = panel.locator("#compact-toolbar");
  const composer = panel.locator(".composer-shell");

  await expect(settings).toHaveAttribute("open", "");
  await expect(toolbar).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(summary).toHaveText("Chat settings and limits");
  await expect(settings).not.toHaveAttribute("open", "");
  await expect(toolbar).toBeHidden();
  const collapsedHeight = await composer.evaluate((node) => node.getBoundingClientRect().height);

  await summary.click();
  await expect(settings).toHaveAttribute("open", "");
  await expect(toolbar).toBeVisible();
  const expandedHeight = await composer.evaluate((node) => node.getBoundingClientRect().height);
  expect(collapsedHeight).toBeLessThan(844 * 0.34);
  expect(expandedHeight - collapsedHeight).toBeGreaterThan(100);
});

test("passes axe checks with live decisions at desktop and mobile widths", async ({ page }, testInfo) => {
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    const panel = page.locator("codex-bridge-panel");
    await expect(panel.locator(".interaction-summary")).toContainText("Codex needs your input");
    await expect(panel.locator(".interaction-summary-count")).toContainText("2 pending decisions");
    await expect(panel.locator('[data-interaction-id="int_command_harness"]')).toBeVisible();
    await expect(panel.locator('[data-interaction-id="int_question_harness"]')).toBeVisible();

    for (const viewport of [
      { name: "desktop", width: 1280, height: 1000 },
      { name: "mobile", width: 390, height: 844 },
    ]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.waitForTimeout(220);
      await expect(panel.locator('[data-interaction-id="int_command_harness"]')).toBeVisible();
      await expect(panel.locator('[data-interaction-id="int_question_harness"]')).toBeVisible();
      const results = await new AxeBuilder({ page })
        .include("codex-bridge-panel")
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        results.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          targets: violation.nodes.flatMap((node) => node.target),
        }))
      ).toEqual([]);
      const theme = await page.evaluate(() => {
        const panel = document.querySelector("codex-bridge-panel");
        const root = panel?.shadowRoot;
        const shell = root?.querySelector(".shell");
        const composer = root?.querySelector(".composer-shell");
        return {
          colorScheme: getComputedStyle(document.documentElement).colorScheme,
          panelBackground: root ? getComputedStyle(panel).getPropertyValue("--panel-bg").trim() : "",
          shellBackground: shell ? getComputedStyle(shell).backgroundColor : "",
          composerBottomPadding: composer ? getComputedStyle(composer).paddingBottom : "",
        };
      });
      expect(theme.colorScheme).toBe(scheme);
      expect(theme.panelBackground).not.toBe("");
      expect(theme.composerBottomPadding).not.toBe("0px");
      await page.screenshot({
        path: testInfo.outputPath(`task17-${scheme}-${viewport.name}.png`),
        fullPage: true,
      });
    }
  }
});
