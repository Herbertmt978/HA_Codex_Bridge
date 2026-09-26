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
      await page.emulateMedia({ colorScheme: installed ? "dark" : "light" });
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
      for (const action of await dialog.getByRole("button").all()) {
        const size = await action.boundingBox();
        expect(size.height).toBeGreaterThanOrEqual(40);
        expect(size.width).toBeGreaterThanOrEqual(40);
      }
      await expect(mode).not.toHaveValue("haos-full-access");
      const bounds = await dialog.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
      const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(accessibility.violations).toEqual([]);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).scrollIntoViewIfNeeded();
      await dialog.getByRole("button", { name: "Cancel", exact: true }).focus();
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
  test(`saved account menu stays private and usable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    if (width === 390) await panel.locator("#mobile-nav-toggle").click();
    await expect(panel.locator("#app-menu-toggle")).toBeVisible();
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element._stopPolling();
      element._config = { ...element._config, capabilities: ["account_profiles_v1"] };
      element._status = {
        ...element._status,
        auth: { state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" },
        account: { available: true, auth_mode: "chatgpt", plan_type: "pro" },
      };
      const original = element._callWS.bind(element);
      let detached = false;
      element._callWS = async (method, args) => {
        if (method === "prepare_new_account_login") {
          detached = true;
          return { state: "logged_out", revision: 2 };
        }
        if (method === "list_account_profiles") return [
          { id: "a".repeat(32), label: "Personal", plan: "pro", active: !detached },
          { id: "b".repeat(32), label: "<Private workspace>", plan: "team", active: false },
        ];
        return original(method, args);
      };
    });
    await panel.locator("#app-menu-toggle").click();
    const menu = panel.locator("#app-menu");
    await expect(menu).toBeVisible();
    await expect(menu.locator("#account-menu-list")).toContainText("<Private workspace>");
    await expect(menu.locator("#account-menu-list img")).toHaveCount(0);
    const bounds = await menu.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await expect(menu.getByRole("button", { name: "Add another account" })).toBeEnabled();
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(accessibility.violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`account-menu-${width}.png`), animations: "disabled" });
    await menu.getByRole("button", { name: "Add another account" }).click();
    await expect(menu).toBeHidden();
    await expect(panel.locator("#side-panel-system")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`add-account-${width}.png`), animations: "disabled" });
  });
}

for (const viewport of [{ width: 390, height: 600 }, { width: 1280, height: 844 }]) {
  test(`five saved accounts fit the account menu at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    if (viewport.width === 390) await panel.locator("#mobile-nav-toggle").click();
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element._stopPolling();
      element._config = { ...element._config, capabilities: ["account_profiles_v1"] };
      element._status = {
        ...element._status,
        auth: { state: "ok", auth_required: false, auth_mode: "chatgpt", plan_type: "pro" },
        account: { available: true, auth_mode: "chatgpt", plan_type: "pro" },
      };
      element._accountProfileDetails = new Map(Array.from({ length: 5 }, (_, index) => [
        String(index + 1).repeat(32), {
          status: index === 4 ? "stale" : "available", plan: "pro",
          windows: [{ name: "5 hours", remaining_percent: 62 }, { name: "Weekly", remaining_percent: 18 }],
          available_resets: 1, next_reset_expiry: 1_800_000_000, expiry_complete: true,
          updated_at: "2026-09-24T18:00:00Z",
        },
      ]));
      element._callWS = async (method) => method === "list_account_profiles"
        ? Array.from({ length: 5 }, (_, index) => ({
          id: String(index + 1).repeat(32), label: `Account ${index + 1}`, plan: "pro", active: index === 0,
        }))
        : {};
    });
    await panel.locator("#app-menu-toggle").click();
    const menu = panel.locator("#app-menu");
    const list = menu.locator("#account-menu-list");
    const rows = list.locator(".account-menu-row");
    await expect(rows).toHaveCount(5);
    await expect(rows.last()).toContainText("figures may have changed");
    const menuBounds = await menu.boundingBox();
    expect(menuBounds.x).toBeGreaterThanOrEqual(0);
    expect(menuBounds.x + menuBounds.width).toBeLessThanOrEqual(viewport.width);
    expect(menuBounds.y + menuBounds.height).toBeLessThanOrEqual(viewport.height);
    if (viewport.width === 1280) {
      expect(menuBounds.width).toBeGreaterThanOrEqual(700);
      expect((await rows.first().boundingBox()).height).toBeLessThan(130);
    }
    await expect(menu.getByRole("button", { name: "Add another account" })).toBeInViewport();
    await rows.last().scrollIntoViewIfNeeded();
    await expect(rows.last()).toBeInViewport();
    await expect(menu.getByRole("button", { name: "Save current account" })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath(`five-account-menu-${viewport.width}.png`), animations: "disabled" });
  });
}

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
    const defaults = panel.getByRole("button", { name: "New chat defaults", exact: true });
    expect((await defaults.boundingBox()).height).toBeGreaterThanOrEqual(40);
    await defaults.focus();
    await expect(defaults).toBeFocused();
    await panel.screenshot({ path: testInfo.outputPath("access-settings.png") });
    await panel.getByRole("tab", { name: "MCP servers", exact: true }).click();
    await panel.getByRole("button", { name: "Add MCP server", exact: true }).click();
    await expect(panel.getByRole("button", { name: /^Home Assistant \(HA-MCP\)/ })).toBeFocused();
    await expect(panel.getByRole("button", { name: /^Other MCP server/ })).toBeVisible();
    await panel.screenshot({ path: testInfo.outputPath("mcp-choices.png") });
    await panel.getByRole("button", { name: /^Home Assistant \(HA-MCP\)/ }).press("Enter");
    await expect(panel.getByLabel("Name", { exact: true })).toHaveValue("home-assistant");
    await expect(panel.getByRole("button", { name: "Add server", exact: true })).toBeDisabled();
    await panel.getByLabel("HA-MCP connection URL").fill("https://ha.example.org/api/webhook/test-only");
    await expect(panel.getByLabel("HA-MCP connection URL")).toHaveAttribute("type", "password");
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element.hass = { ...element.hass };
    });
    await expect(panel.getByLabel("HA-MCP connection URL")).toHaveValue("https://ha.example.org/api/webhook/test-only");
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(accessibility.violations).toEqual([]);
    const formBounds = await panel.locator('[data-desktop-form="mcp"]').boundingBox();
    expect(formBounds.x).toBeGreaterThanOrEqual(0);
    expect(formBounds.x + formBounds.width).toBeLessThanOrEqual(width);
    await panel.screenshot({ path: testInfo.outputPath("ha-mcp-guide.png") });
    await page.evaluate(() => {
      const element = document.querySelector("codex-bridge-panel");
      element._config.capabilities = ["mcp_admin_v1", "mcp_local_v1"];
      element._renderDesktopSurface();
    });
    await panel.getByLabel("Connect to a local network or Home Assistant App server").check();
    await panel.getByLabel("HA-MCP connection URL").fill("http://ha.local:8123/private-test");
    const consent = panel.getByLabel("I trust this server and understand the access and connection risks");
    await expect(consent).not.toBeChecked();
    await expect(panel.getByText("OAuth settings (optional)", { exact: true })).toHaveCount(0);
    await consent.check();
    await panel.getByLabel("HA-MCP connection URL").fill("http://ha.local:8123/changed-test");
    await expect(consent).not.toBeChecked();
    expect((await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await panel.screenshot({ path: testInfo.outputPath("local-mcp-warning.png") });
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

test.describe("schedule run outcomes", () => {
  test.use({ timezoneId: "America/Los_Angeles" });
  for (const width of [390, 900, 1200, 1440]) {
    test(`keeps a completed status word intact at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
      await selectHarnessThread(page);
      await page.evaluate(async () => {
        const panel = document.querySelector("codex-bridge-panel");
        panel.hass = { ...panel.hass, config: { time_zone: "Europe/London" } };
        await panel._selectDesktopDestination("scheduled");
        panel._desktopFeatures.scheduled.data.runs = [{
          status: "completed",
          due_at: "2026-09-26T05:31:00Z",
          started_at: "2026-09-26T05:31:01Z",
          completed_at: "2026-09-26T05:31:30Z",
        }];
        panel._render(true);
      });
      const panel = page.locator("codex-bridge-panel");
      const table = panel.locator(".schedule-run-history");
      const status = table.locator('td[data-label="Status"]');
      await expect(status).toHaveText("Completed");
      const lines = await status.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        return range.getClientRects().length;
      });
      expect(lines).toBe(1);
      const bounds = await table.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return { right: box.right, width: element.scrollWidth, client: element.clientWidth };
      });
      expect(bounds.right).toBeLessThanOrEqual(width);
      expect(bounds.width).toBeLessThanOrEqual(bounds.client + 1);
      await panel.locator("#desktop-feature-surface").screenshot({ path: test.info().outputPath(`completed-status-${width}.png`) });
    });
  }
  for (const [name, timezone] of [["missing", undefined], ["empty", ""], ["configured UTC", "UTC"]]) {
    test(`the actual panel distinguishes ${name} HA time zone from fallback`, async ({ page }) => {
      await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
      await selectHarnessThread(page);
      await page.evaluate(async ({ timezone }) => {
        const panel = document.querySelector("codex-bridge-panel");
        panel.hass = { ...panel.hass, config: timezone === undefined ? {} : { time_zone: timezone } };
        await panel._selectDesktopDestination("scheduled");
        panel._desktopFeatures.scheduled.data.runs = [{ status: "completed", due_at: "2026-09-25T08:00:00Z" }];
        panel._render(true);
      }, { timezone });
      const panel = page.locator("codex-bridge-panel");
      const surface = panel.locator("#desktop-feature-surface");
      await expect(surface).toContainText("25 Sept 2026, 08:00 UTC");
      await expect(surface).toContainText(timezone ? "Times shown in Home Assistant's UTC time zone." : "Times shown in UTC because the Home Assistant time zone is unavailable.");
      await panel.getByRole("button", { name: "New schedule", exact: true }).click();
      expect(await page.evaluate(() => document.querySelector("codex-bridge-panel")._scheduleContext().timezone)).toBe("UTC");
      await expect(panel.locator(".schedule-editor")).toBeVisible();
    });
  }
  for (const width of [390, 1440]) {
    test(`explains scheduler outcomes in HA time without overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1100 });
      await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
      await selectHarnessThread(page);
      await page.evaluate(async () => {
        const panel = document.querySelector("codex-bridge-panel");
        panel.hass = { ...panel.hass, config: { time_zone: "Europe/London" } };
        await panel._selectDesktopDestination("scheduled");
        panel._desktopFeatures.scheduled.data.runs = [
          { status: "skipped_overlap", due_at: "2026-09-25T08:00:00Z", error: "private-error", automation_run_id: "private-id" },
          { status: "skipped_misfire", due_at: "2026-09-25T09:00:00Z" },
          { status: "queued", due_at: "2026-09-25T10:00:00Z" },
          { status: "failed", due_at: "2026-09-25T11:00:00Z", completed_at: "2026-09-25T11:00:30Z", error: "private-error" },
          { status: "completed", due_at: "2026-09-25T12:00:00Z", started_at: "2026-09-25T12:00:01Z", completed_at: "2026-09-25T12:00:30Z" },
          { status: "private-unknown-status", due_at: "private-invalid-date" },
        ];
        panel._render(true);
      });
      const panel = page.locator("codex-bridge-panel");
      const table = panel.locator(".schedule-run-history");
      await expect(table).toContainText("Skipped · already running");
      await expect(table).toContainText("No second run started");
      await expect(table).toContainText("Skipped · missed window");
      await expect(table).toContainText("25 Sept 2026, 09:00 BST");
      await expect(table).toContainText("Status unavailable");
      await expect(table).toContainText("Completed");
      await expect(table).not.toContainText("private-");
      await expect(panel.locator("#desktop-feature-surface")).toContainText("Home Assistant's Europe/London time zone");
      const bounds = await table.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return { right: box.right, left: box.left, width: element.scrollWidth, client: element.clientWidth };
      });
      expect(bounds.left).toBeGreaterThanOrEqual(0);
      expect(bounds.right).toBeLessThanOrEqual(width);
      expect(bounds.width).toBeLessThanOrEqual(bounds.client + 1);
      await panel.locator("#desktop-feature-surface").screenshot({ path: test.info().outputPath(`schedule-run-history-${width}.png`) });
      const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      expect(accessibility.violations).toEqual([]);
    });
  }
});

for (const width of [390, 1440]) {
  test(`reviews a described change to only the selected task at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(async () => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "automation_proposals_v1"] };
      const definition = {
        prompt: "Original instructions", target: { kind: "standalone", project_id: "prj_vba" },
        schedule: { kind: "interval", seconds: 300, anchor_at: "2026-01-01T00:00:37Z" },
        mode: "observe", model: "saved-model", thinking: "high",
        notifications: { policy: "off", mobile_targets: [] },
      };
      await panel._callWS("create_automation", { ...definition, name: "Selected task" });
      await panel._callWS("create_automation", { ...definition, name: "Another task" });
      panel._selectDesktopDestination("scheduled");
    });
    const panel = page.locator("codex-bridge-panel");
    const selected = panel.getByRole("row").filter({ hasText: "Selected task" });
    await selected.getByRole("button", { name: "Describe change", exact: true }).click();
    const form = panel.locator('[data-desktop-form="automation-edit-description"]');
    const request = form.getByRole("textbox", { name: "Change request", exact: true });
    await expect(request).toBeFocused();
    await request.fill("Rename to Morning heating check");
    const stable = await page.evaluate(async () => {
      const panel = document.querySelector("codex-bridge-panel");
      const field = panel.shadowRoot.querySelector('[name="edit_description"]');
      panel.hass = { ...panel.hass, states: { ...panel.hass.states } };
      panel._renderDesktopSurface();
      await new Promise(requestAnimationFrame);
      return panel.shadowRoot.querySelector('[name="edit_description"]') === field;
    });
    expect(stable).toBe(true);
    await form.getByRole("button", { name: "Review changes", exact: true }).click();
    await expect(form.getByRole("heading", { name: "Describe a change", exact: true })).toBeFocused();
    await expect(form.getByText("Current", { exact: true })).toBeVisible();
    await expect(form.getByText("Proposed", { exact: true })).toBeVisible();
    await expect(form.getByText("Morning heating check", { exact: true })).toBeVisible();
    expect(await websocketCalls(page, "codex_bridge/update_automation")).toHaveLength(0);
    const bounds = await form.evaluate(element => ({ width: element.scrollWidth, client: element.clientWidth, right: element.getBoundingClientRect().right }));
    expect(bounds.width).toBeLessThanOrEqual(bounds.client + 1);
    expect(bounds.right).toBeLessThanOrEqual(width);
    await form.screenshot({ path: test.info().outputPath(`described-schedule-edit-${width}.png`) });
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(accessibility.violations).toEqual([]);
    await form.getByRole("button", { name: "Save changes", exact: true }).press("Enter");
    await expect(form).toHaveCount(0);
    await expect(panel.getByRole("cell", { name: /Morning heating check/u })).toBeVisible();
    await expect(panel.getByRole("cell", { name: /Another task/u })).toBeVisible();
    const calls = await websocketCalls(page, "codex_bridge/update_automation");
    expect(calls).toHaveLength(1);
    expect(calls[0].payload).toEqual({ type: "codex_bridge/update_automation", automation_id: "automation_1", expected_revision: 1, name: "Morning heating check" });
  });
}

test("creates and edits a scheduled task using the reference form", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined, configurable: true });
  });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel.hass = { ...panel.hass, config: { time_zone: "Europe/London" } };
    panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "automation_proposals_v1"] };
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
  expect(created.client_request_id).toMatch(/^[a-f0-9]{32}$/);
  await panel.getByRole("button", { name: "Update", exact: true }).click();
  await form.getByRole("textbox", { name: "Scheduled task title" }).fill("Renamed summary");
  await form.getByRole("button", { name: "Save changes", exact: true }).click();
  const updated = (await websocketCalls(page, "codex_bridge/update_automation"))[0].payload;
  expect(updated).toMatchObject({ name: "Renamed summary", expected_revision: 1, target: created.target, schedule: created.schedule });
});

for (const width of [390, 1280]) {
  test(`scheduled notifications select only the chosen phone at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 844 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "automation_notifications_v1"] };
      panel.hass = { ...panel.hass, services: { ...panel.hass?.services, notify: { mobile_app_test_phone: {}, mobile_app_second_phone: {} } } };
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.locator('[data-destination="scheduled"]').click();
    await panel.getByRole("button", { name: "New schedule", exact: true }).click();
    await page.setViewportSize({ width, height: 844 });
    const form = panel.locator(".schedule-editor");
    await form.getByRole("textbox", { name: "Scheduled task title" }).fill("Morning report");
    await form.getByRole("textbox", { name: "Task instructions" }).fill("Report the result.");
    await form.getByRole("combobox", { name: "When to notify" }).click();
    await form.getByRole("option", { name: "All outcomes" }).click();
    await form.getByRole("checkbox", { name: "Home Assistant notification (visible to all HA users)" }).check();
    await form.getByRole("checkbox", { name: "Phone · test phone" }).check();
    await expect(form.getByRole("checkbox", { name: "Phone · second phone" })).not.toBeChecked();
    await expect(form.getByRole("checkbox", { name: "Include a brief answer preview on selected phones" })).not.toBeChecked();
    const bounds = await form.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await form.screenshot({ path: test.info().outputPath(`scheduled-notifications-${width}.png`) });
    await form.getByRole("button", { name: "Create task" }).click();
    const created = (await websocketCalls(page, "codex_bridge/create_automation")).at(-1).payload;
    expect(created.notifications).toEqual({ policy: "all", persistent: true, mobile_targets: ["mobile_app_test_phone"], preview: false });
  });
}

test("reviews a chat message as a schedule before any task is created", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-23T08:00:00Z"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    bridge._config = { ...bridge._config, capabilities: [...(bridge._config?.capabilities || []), "automation_proposals_v1"] };
    bridge.hass = { ...bridge.hass, config: { time_zone: "Europe/London" } };
    const send = bridge.hass.connection.sendMessagePromise;
    bridge.hass.connection.sendMessagePromise = (request) => request.type === "codex_bridge/preview_automation_schedule"
      ? Promise.resolve({ next_runs: ["2026-09-24T08:00:00Z"] }) : send(request);
    bridge._render(true);
  });
  await panel.locator("#prompt-input").fill("On 24 September 2026 at 09:00, prepare a report");
  await panel.getByRole("button", { name: "Add to chat" }).click();
  await panel.getByRole("button", { name: "Schedule this message" }).click();
  await expect(panel.locator('[data-desktop-field="description"]')).toHaveValue("On 24 September 2026 at 09:00, prepare a report");
  await panel.getByRole("button", { name: "Review timing" }).click();
  const form = panel.locator(".schedule-editor");
  await expect(form.getByRole("textbox", { name: "Scheduled task title" })).toHaveValue("prepare a report");
  await expect(form.locator(".schedule-next-runs")).toContainText("24 Sept 2026");
  expect(await websocketCalls(page, "codex_bridge/create_automation")).toHaveLength(0);
  const overflow = await form.evaluate((node) => node.scrollWidth > node.clientWidth || node.getBoundingClientRect().right > window.innerWidth);
  expect(overflow).toBe(false);
  await form.screenshot({ path: test.info().outputPath("schedule-description-review.png") });
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("does not offer an Assist conversation as a scheduled target", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-23T08:00:00Z"));
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    window.__codexHarness.updateThread(panel._activeThread.thread_id, { schedule_eligible: false });
    panel._activeThread = { ...panel._activeThread, schedule_eligible: false };
    panel._threads = panel._threads.map((thread) => thread.thread_id === panel._activeThread.thread_id ? panel._activeThread : thread);
    panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "automation_proposals_v1"] };
    panel.hass = { ...panel.hass, config: { time_zone: "Europe/London" } };
    panel._render(true);
  });
  const panel = page.locator("codex-bridge-panel");
  await panel.getByRole("button", { name: "Scheduled" }).click();
  await panel.getByRole("button", { name: "Describe a task" }).click();
  await panel.getByRole("textbox", { name: "Task and timing" }).fill("On 24 September 2026 at 09:00 in this chat, say hello");
  await panel.getByRole("button", { name: "Review timing" }).click();
  await expect(panel.locator(".schedule-description")).toContainText("Assist conversations cannot run scheduled tasks");
  expect(await websocketCalls(page, "codex_bridge/create_automation")).toHaveLength(0);
  await panel.getByRole("button", { name: "Cancel" }).click();
  await panel.getByRole("button", { name: "New schedule" }).click();
  await expect(panel.locator(".schedule-editor")).toContainText("Assist conversations cannot run scheduled tasks");
  await expect(panel.locator('.schedule-editor [name="target_kind"] option[value="continue_thread"]')).toBeDisabled();
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
  for (const theme of ["light", "dark"]) {
    await panel.evaluate((node, value) => node.setAttribute("data-panel-theme", value), theme);
    await form.getByRole("button", { name: "Create task", exact: true }).hover();
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
    expect(accessibility.violations.filter((violation) => violation.id === "color-contrast")).toEqual([]);
    await form.screenshot({ path: test.info().outputPath(`scheduled-error-${theme}.png`) });
  }
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

test("centres feature loading and blends nested project actions into the selected row", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await page.evaluate(() => {
    const element = document.querySelector("codex-bridge-panel");
    element._stopPolling();
    element._activeDestination = "plugins";
    element._desktopFeatures.plugins.loading = true;
    element._render(true);
  });
  const surface = panel.locator(".desktop-feature-surface");
  const loading = surface.locator(".desktop-feature-loading");
  await expect(loading).toHaveText("Loading plugins…");
  const surfaceBox = await surface.boundingBox();
  const loadingBox = await loading.boundingBox();
  expect(Math.abs(loadingBox.y + loadingBox.height / 2 - surfaceBox.y - surfaceBox.height / 2)).toBeLessThan(100);
  await panel.screenshot({ path: testInfo.outputPath("plugins-loading-light.png") });
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(parseFloat(await loading.locator(".desktop-feature-spinner").evaluate((element) => getComputedStyle(element).animationDuration))).toBeLessThan(0.01);

  await page.reload();
  const more = panel.locator("#project-actions-toggle-prj_vba");
  await expect(more).toBeVisible();
  await panel.locator('[data-action="select-project"][data-project-id="prj_vba"]').click();
  await more.click();
  const menu = panel.locator("#project-secondary-actions-prj_vba");
  await expect(menu).toBeVisible();
  expect(await menu.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe("rgba(0, 0, 0, 0)");
  await panel.screenshot({ path: testInfo.outputPath("project-actions-light.png") });
  await panel.evaluate((element) => element.setAttribute("data-panel-theme", "dark"));
  expect(await menu.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe("rgba(0, 0, 0, 0)");
  await panel.screenshot({ path: testInfo.outputPath("project-actions-dark.png") });
});

test("keeps activity details and rebuilt navigation controls steady during HA updates", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);

  const panel = page.locator("codex-bridge-panel");
  for (const selector of ["#run-step-chip", '#direct-section [data-action="toggle-section"]', "#new-project-button"]) {
    const control = panel.locator(selector);
    await control.hover();
    if (selector === "#run-step-chip") {
      await expect(panel.locator("#run-step-tooltip")).toBeVisible();
    } else {
      await expect(panel.locator("#tooltip-layer")).toBeVisible();
    }
    const samples = await page.evaluate(async (controlSelector) => {
      const bridge = document.querySelector("codex-bridge-panel");
      const original = bridge.shadowRoot.querySelector(controlSelector);
      const results = [];
      for (let index = 0; index < 8; index += 1) {
        bridge.hass = { ...bridge._hass, states: { ...bridge._hass.states } };
        await new Promise(requestAnimationFrame);
        results.push({ connected: original.isConnected, hovered: original.matches(":hover") });
      }
      return results;
    }, selector);
    expect(samples.every(({ connected, hovered }) => connected && hovered)).toBe(true);
    if (selector === "#run-step-chip") {
      await expect(panel.locator("#run-step-tooltip")).toBeVisible();
    } else {
      await expect(panel.locator("#tooltip-layer")).toBeVisible();
    }
  }
});

test("centres feature headings before and after loading at desktop and mobile widths", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 800 });
    for (const destination of ["skills", "settings", "plugins"]) {
      for (const loading of [true, false]) {
        const positions = await panel.evaluate((element, next) => {
          element._activeDestination = next.destination;
          element._desktopFeatures[next.destination] = {
            ...element._desktopFeatures[next.destination],
            loading: next.loading,
            data: {},
          };
          element._render(true);
          const surface = element.shadowRoot.getElementById("desktop-feature-surface");
          const title = surface.querySelector(".desktop-feature-title");
          const summary = surface.querySelector(".desktop-feature-summary");
          const centre = (node) => {
            const bounds = node.getBoundingClientRect();
            return bounds.left + bounds.width / 2;
          };
          return { surface: centre(surface), title: centre(title), summary: centre(summary) };
        }, { destination, loading });
        expect(Math.abs(positions.title - positions.surface)).toBeLessThan(3);
        expect(Math.abs(positions.summary - positions.surface)).toBeLessThan(3);
      }
    }
  }
});

test("keeps sidebar search and New chat on neutral surfaces", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  const search = panel.locator("#search-input");
  const newChat = panel.locator("#new-direct-chat-button");
  const shell = panel.locator(".search-shell");
  const base = await newChat.evaluate((node) => getComputedStyle(node).backgroundColor);
  expect(base).toBe(await shell.evaluate((node) => getComputedStyle(node).backgroundColor));
  await search.focus();
  expect(await search.evaluate((node) => getComputedStyle(node).boxShadow)).toBe("none");
  expect(await shell.evaluate((node) => getComputedStyle(node).boxShadow)).not.toBe("none");
  await newChat.hover();
  expect(await newChat.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(base);
});

test("creates disposable chats and uses the menu to edit, archive and delete them", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  const createChat = async (title) => {
    await panel.locator("#new-direct-chat-button").click();
    await panel.locator("#thread-title-input").fill(title);
    await panel.locator('#thread-form-panel [data-action="save-thread"]').click();
    await expect(panel.locator("#thread-title-label")).toHaveText(title);
  };
  await createChat("Disposable archive check");
  await panel.locator("#chat-menu-button").click();
  const menu = panel.locator("#thread-menu");
  await expect(menu.locator('[data-action="edit-current-chat"]')).toBeVisible();
  expect(await menu.locator("button[data-tooltip]").count()).toBe(0);
  await menu.locator('[data-action="edit-current-chat"]').click();
  await expect(panel.locator("#thread-form-panel")).toContainText("Chat settings");
  await panel.locator("#thread-title-input").fill("Disposable renamed check");
  await panel.locator('#thread-form-panel [data-action="save-thread"]').click();
  await expect(panel.locator("#thread-title-label")).toHaveText("Disposable renamed check");
  await panel.locator("#chat-menu-button").click();
  await menu.locator('[data-action="archive-thread"]').click();
  await expect(panel.locator("#thread-title-label")).not.toHaveText("Disposable renamed check");
  await expect.poll(() => panel.evaluate((element) => element._threads.some((thread) =>
    thread.title === "Disposable renamed check" && Boolean(thread.archived_at)))).toBe(true);
  await createChat("Disposable delete check");
  await panel.locator("#chat-menu-button").click();
  await menu.locator('[data-action="delete-thread"]').click();
  await expect(panel.locator("#confirmation-dialog")).toBeVisible();
  await panel.locator("#confirm-delete-button").click();
  await expect.poll(() => panel.evaluate((element) => element._threads.some((thread) =>
    thread.title === "Disposable delete check"))).toBe(false);
});

test("audits every destination, settings tab and primary menu in both themes and layouts", async ({ page }, testInfo) => {
  const inventory = [];
  const capture = async (name) => {
    const controls = await page.locator("codex-bridge-panel").evaluate((element) => {
      const root = element.shadowRoot;
      return [...root.querySelectorAll("button, input, select, textarea, summary, a[href]")]
        .filter((control) => {
          const style = getComputedStyle(control);
          const box = control.getBoundingClientRect();
          return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none" && !control.closest("[inert]");
        })
        .map((control) => {
          const box = control.getBoundingClientRect();
          return {
            tag: control.tagName.toLowerCase(),
            action: control.dataset.action || control.dataset.desktopAction || "",
            name: (control.getAttribute("aria-label") || control.getAttribute("title") || control.textContent || "").trim().replace(/\s+/gu, " ").slice(0, 120),
            scrollable: Boolean(control.closest(".settings-tabs")?.scrollWidth > control.closest(".settings-tabs")?.clientWidth),
            x: Math.round(box.x), right: Math.round(box.right), width: Math.round(box.width),
          };
        });
    });
    const width = page.viewportSize().width;
    const clipped = controls.filter((control) => !control.scrollable && (control.x < -1 || control.right > width + 1));
    expect(clipped, `${name}: controls must fit the viewport`).toEqual([]);
    inventory.push({ state: name, controls });
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true, animations: "disabled" });
  };

  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
      await selectHarnessThread(page);
      const panel = page.locator("codex-bridge-panel");
      const state = `${scheme}-${width}`;
      await capture(`${state}-chat`);
      const mobileContext = panel.locator("#mobile-context-toggle");
      if (await mobileContext.isVisible()) await mobileContext.click();
      for (const tab of ["Activity", "Files", "Usage", "System"]) {
        await panel.getByRole("tab", { name: tab, exact: true }).click();
        await capture(`${state}-side-${tab.toLowerCase()}`);
      }
      if (await mobileContext.isVisible()) await panel.locator("#mobile-drawer-scrim").click({ position: { x: 8, y: 420 } });
      await panel.locator("#toggle-bottom-button").click();
      await capture(`${state}-bottom-preview`);
      await panel.locator("#bottom-terminal-button").click();
      await capture(`${state}-bottom-terminal`);
      await panel.locator("#toggle-bottom-button").click();
      for (const destination of ["scheduled", "skills", "plugins", "settings"]) {
        await panel.evaluate((element, target) => element._selectDesktopDestination(target), destination);
        await expect(panel.locator("#desktop-feature-surface .desktop-feature-title")).toHaveText(destination === "scheduled" ? "Scheduled" : destination[0].toUpperCase() + destination.slice(1));
        await expect(panel.locator("#desktop-feature-surface")).toHaveAttribute("aria-busy", "false");
        if (destination === "plugins") {
          await expect(panel.locator("#desktop-feature-surface")).toContainText("Gmail");
          await expect(panel.locator("#desktop-feature-surface")).not.toContainText("app-gmail-fixture");
        }
        await capture(`${state}-${destination}`);
        if (destination === "settings") {
          for (const tab of ["Access", "Appearance", "MCP servers", "Instructions", "Keyboard shortcuts", "About / security"]) {
            await panel.getByRole("tab", { name: tab, exact: true }).click();
            await capture(`${state}-settings-${tab.toLowerCase().replace(/[^a-z]+/gu, "-")}`);
          }
        }
      }
      await panel.evaluate((element) => element._selectDesktopDestination("chats"));
      await panel.locator("#chat-menu-button").click();
      await capture(`${state}-chat-menu`);
      await panel.locator("#chat-menu-button").click();
      await panel.locator("#add-menu-button").click();
      await capture(`${state}-add-menu`);
      await panel.locator("#add-menu-button").click();
      if (width === 390) {
        await panel.locator("#mobile-nav-toggle").click();
        await expect.poll(() => panel.locator("#workspace-drawer").evaluate((drawer) => Math.round(drawer.getBoundingClientRect().left))).toBeGreaterThanOrEqual(0);
        await capture(`${state}-navigation`);
      }
      await panel.locator("#app-menu-toggle").click();
      await expect(panel.locator("#tooltip-layer")).toBeHidden();
      await capture(`${state}-app-menu`);
      await panel.locator("#app-menu-toggle").click();
    }
  }
  await import("node:fs/promises").then(({ writeFile }) => writeFile(testInfo.outputPath("control-inventory.json"), JSON.stringify(inventory, null, 2)));
});

test("skill and plugin controls complete their advertised actions in the browser", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const surface = panel.locator("#desktop-feature-surface");
  await panel.evaluate((element) => element._selectDesktopDestination("skills"));
  await expect(surface).toContainText("docs:write");
  await surface.getByRole("button", { name: "Create skill" }).click();
  await expect(surface.locator('[data-desktop-form="skill"]')).toBeVisible();
  await surface.locator('[data-desktop-field="name"]').fill("audit:temporary");
  await surface.locator('[data-desktop-field="description"]').fill("Disposable audit skill");
  await surface.locator('[data-desktop-field="instructions"]').fill("Test the control.");
  await page.screenshot({ path: testInfo.outputPath("skill-form-mobile.png"), animations: "disabled" });
  await surface.locator('[data-desktop-action="submit-skill"]').click();
  await expect(surface).toContainText("audit:temporary");
  const skillRow = surface.locator("tr", { hasText: "audit:temporary" });
  await skillRow.getByRole("button", { name: "Disable" }).click();
  await expect(surface.locator("tr", { hasText: "audit:temporary" })).toContainText("Disabled");
  await surface.locator("tr", { hasText: "audit:temporary" }).getByRole("button", { name: "Delete" }).click();
  await expect(surface).toContainText("This action is destructive");
  await surface.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(surface).toContainText("audit:temporary");
  await surface.locator("tr", { hasText: "audit:temporary" }).getByRole("button", { name: "Delete" }).click();
  await surface.getByRole("button", { name: "Confirm" }).click();
  await expect(surface).not.toContainText("audit:temporary");

  await panel.evaluate((element) => element._selectDesktopDestination("plugins"));
  await expect(surface).toContainText("Documents");
  const documents = surface.locator("tr", { hasText: "Documents" });
  await documents.getByRole("button", { name: "Install" }).click();
  await expect(surface.locator("tr", { hasText: "Documents" })).toContainText("Enabled");
  await surface.locator("tr", { hasText: "Documents" }).getByRole("button", { name: "Uninstall" }).click();
  await expect(surface).toContainText("This action is destructive");
  await surface.getByRole("button", { name: "Confirm" }).click();
  await expect(surface.locator("tr", { hasText: "Documents" })).toContainText("Disabled");
  await surface.getByRole("button", { name: "Add marketplace" }).click();
  await surface.locator('[data-desktop-field="source"]').fill("https://catalogue.example.test/plugins");
  await page.screenshot({ path: testInfo.outputPath("marketplace-form-mobile.png"), animations: "disabled" });
  await surface.locator('[data-desktop-action="submit-marketplace"]').click();
  await expect(surface).toContainText("catalogue.example.test");
  const marketplace = surface.locator("tr", { hasText: "catalogue.example.test" });
  await marketplace.getByRole("button", { name: "Upgrade" }).click();
  await expect(surface).toContainText("Saved.");
  await surface.locator("tr", { hasText: "catalogue.example.test" }).getByRole("button", { name: "Remove" }).click();
  await surface.getByRole("button", { name: "Confirm" }).click();
  await expect(surface).not.toContainText("catalogue.example.test");
  const calls = await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.kind === "ws").map((call) => call.type));
  for (const action of ["create_skill", "set_skill", "delete_skill", "install_plugin", "uninstall_plugin", "add_marketplace", "upgrade_marketplace", "remove_marketplace"]) {
    expect(calls).toContain(`codex_bridge/${action}`);
  }
});

test("search, share, refresh and account-menu controls produce visible results", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const search = panel.locator("#search-input");
  await search.fill("Quick bridge");
  await expect(panel.locator("#direct-chat-list")).toContainText("Quick bridge note");
  await expect(panel.locator(".project-list")).not.toContainText("Attachment validation");
  await search.fill("");
  await expect(panel.locator(".project-list")).toContainText("Attachment validation");

  await panel.evaluate((element) => { element._writeClipboardText = async (value) => { window.__copiedChatLink = value; }; });
  await panel.locator("#share-chat-button").click();
  await expect(panel.locator("#share-status")).toContainText("Chat link copied");
  const copied = await page.evaluate(() => window.__copiedChatLink);
  expect(copied).toContain("thr_vba_1");
  expect(copied).not.toMatch(/access_token|Bearer/iu);

  const before = (await websocketCalls(page, "codex_bridge/get_thread")).length;
  await panel.locator("#chat-menu-button").click();
  await panel.locator('#thread-menu [data-action="refresh-thread"]').click();
  await expect.poll(async () => (await websocketCalls(page, "codex_bridge/get_thread")).length).toBeGreaterThan(before);
  await panel.locator("#app-menu-toggle").click();
  await expect(panel.locator("#app-menu")).toBeVisible();
  await expect(panel.locator("#tooltip-layer")).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(panel.locator("#app-menu")).toBeHidden();
});

test("updates chat ages without replacing their navigation controls", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const result = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const timestamp = new Date().toISOString();
    bridge._threads = bridge._threads.map((thread) =>
      thread.thread_id === "thr_vba_1" ? { ...thread, updated_at: timestamp } : thread);
    bridge._renderNavigationSections();
    const select = bridge.shadowRoot.querySelector('.chat-select[data-thread-id="thr_vba_1"]');
    const before = select.dataset.tooltip;
    const originalNow = Date.now;
    try {
      Date.now = () => originalNow() + 2 * 60_000;
      bridge._renderNavigationSections();
    } finally {
      Date.now = originalNow;
    }
    return { before, after: select.dataset.tooltip, connected: select.isConnected };
  });
  expect(result.before).toContain("· now");
  expect(result.after).toContain("· 2m");
  expect(result.connected).toBe(true);
});

test("keeps a hovered activity popover steady when unrelated run events render", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);
  const panel = page.locator("codex-bridge-panel");
  const chip = panel.locator("#run-step-chip");
  await chip.hover();
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
  const result = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#run-step-chip");
    bridge._renderRunActivity();
    return { connected: original.isConnected, hovered: original.matches(":hover") };
  });
  expect(result).toEqual({ connected: true, hovered: true });
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
});

test("keeps a hovered activity popover steady as its content changes", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);
  const panel = page.locator("codex-bridge-panel");
  const chip = panel.locator("#run-step-chip");
  await chip.hover();
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
  const state = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#run-step-chip");
    window.__codexHarness.emitThreadEvent("thr_vba_1", "item.started", {
      run_id: "run_stage_tooltip", item_id: "hover-command", item_type: "commandExecution", command_preview: "git status --short",
    });
    return { connected: original.isConnected, hovered: original.matches(":hover") };
  });
  expect(state).toEqual({ connected: true, hovered: true });
  await expect(panel.locator("#run-step-tooltip")).toContainText("Command details");
  const command = panel.locator(".run-command-details").first();
  const summary = command.locator("summary");
  await summary.click();
  await expect(command).toHaveAttribute("open", "");
  await expect(summary).toBeFocused();
  await page.evaluate(() => {
    window.__codexHarness.emitThreadEvent("thr_vba_1", "item.completed", {
      run_id: "run_stage_tooltip", item_id: "image-after-command", item_type: "imageView",
    });
  });
  await expect(command).toHaveAttribute("open", "");
  await expect(summary).toBeFocused();
});

test("keeps a failed-run popover open through a status refresh", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    window.__codexHarness.updateThread("thr_vba_1", { status: "error", last_error: "Codex usage limits have been reached." });
    window.__codexHarness.emitThreadEvent("thr_vba_1", "run.failed", {
      run_id: "failed-hover-refresh", error: "Codex usage limits have been reached.",
    });
  });
  const panel = page.locator("codex-bridge-panel");
  const label = panel.locator("#run-step-chip .run-step-label");
  await label.hover();
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
  const result = await page.evaluate(async () => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#run-step-chip .run-step-label");
    bridge._renderedRunActivityKey = "";
    bridge._renderRunActivity();
    await new Promise(requestAnimationFrame);
    return {
      sameLabel: bridge.shadowRoot.querySelector("#run-step-chip .run-step-label") === original,
      hovered: original.matches(":hover"),
      visible: getComputedStyle(bridge.shadowRoot.querySelector("#run-step-tooltip")).visibility === "visible",
    };
  });
  expect(result).toEqual({ sameLabel: true, hovered: true, visible: true });
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
});

test("keeps the hovered activity label mounted while its text changes", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);
  const panel = page.locator("codex-bridge-panel");
  const label = panel.locator("#run-step-chip .run-step-label");
  await label.hover();
  await expect(panel.locator("#run-step-tooltip")).toBeVisible();
  const samples = await page.evaluate(async () => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#run-step-chip .run-step-label");
    const results = [];
    for (let index = 0; index < 5; index += 1) {
      window.__codexHarness.emitThreadEvent("thr_vba_1", "item.started", {
        run_id: "run_stage_tooltip", item_id: `hover-update-${index}`,
        item_type: "commandExecution", command_preview: `echo ${index}`,
      });
      await new Promise(requestAnimationFrame);
      const tooltip = bridge.shadowRoot.querySelector("#run-step-tooltip");
      results.push({
        sameLabel: bridge.shadowRoot.querySelector("#run-step-chip .run-step-label") === original,
        hovered: original.matches(":hover"),
        visible: getComputedStyle(tooltip).visibility === "visible",
      });
    }
    return results;
  });
  expect(samples.every((sample) => sample.sameLabel && sample.hovered && sample.visible)).toBe(true);
});

test("keeps sidebar and menu tooltips readable at viewport edges", async ({ page }) => {
  for (const width of [390, 1755]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    if (width === 1755) {
      for (const label of ["View all workspace files", "Add source files"]) {
        const action = panel.locator(`[data-tooltip="${label}"]`);
        await action.hover();
        const tooltip = panel.locator("#tooltip-layer");
        await expect(tooltip).toHaveText(label);
        const size = await tooltip.boundingBox();
        expect(size.width, `${label} remains on one line`).toBeGreaterThan(100);
      }
    }
    if (width === 1755) await panel.locator("#side-tab-files").click();
    const targets = width === 390
      ? ["#mobile-nav-toggle"]
      : ["#workspace-archive-button", "#new-project-button"];
    for (const selector of targets) {
      const target = panel.locator(selector);
      if (!(await target.isVisible())) continue;
      await target.hover();
      const geometry = await page.evaluate(() => {
        const tooltip = document.querySelector("codex-bridge-panel").shadowRoot.querySelector("#tooltip-layer");
        const rect = tooltip.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width, text: tooltip.textContent };
      });
      expect(geometry.left, `${selector} tooltip left at ${width}px`).toBeGreaterThanOrEqual(7);
      expect(geometry.right, `${selector} tooltip right at ${width}px`).toBeLessThanOrEqual(width - 7);
      expect(geometry.width, `${selector} tooltip width at ${width}px`).toBeGreaterThan(width === 390 ? 35 : 70);
      expect(geometry.text.length).toBeGreaterThan(0);
    }
    if (width === 1755) {
      await panel.locator("#app-menu-toggle").click();
      const focus = panel.locator("#focus-mode-button");
      await focus.hover();
      await expect(panel.locator("#tooltip-layer")).toBeHidden();
      await expect(focus).not.toHaveAttribute("title", /.+/);
    }
  }
});

test("keeps new-chat actions reachable in a short sidebar", async ({ page }) => {
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 416 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    if (width === 390) await panel.locator("#mobile-nav-toggle").click();
    await panel.locator("#new-direct-chat-button").click();
    const create = panel.locator('#thread-form-panel [data-action="save-thread"]');
    await expect(create).toBeVisible();
    const layout = await page.evaluate(() => {
      const root = document.querySelector("codex-bridge-panel").shadowRoot;
      const stack = root.querySelector(".forms-stack");
      const button = root.querySelector('#thread-form-panel [data-action="save-thread"]');
      const stackBox = stack.getBoundingClientRect();
      const buttonBox = button.getBoundingClientRect();
      return { scrollable: stack.scrollHeight > stack.clientHeight, buttonTop: buttonBox.top, buttonBottom: buttonBox.bottom, stackBottom: stackBox.bottom };
    });
    expect(layout.scrollable).toBe(true);
    expect(layout.buttonTop).toBeGreaterThanOrEqual(0);
    expect(layout.buttonBottom).toBeLessThanOrEqual(Math.min(416, layout.stackBottom) + 1);
    await panel.locator("#thread-title-input").fill(`Short sidebar ${width}`);
    await create.click();
    await expect(panel.locator("#thread-form-panel")).not.toHaveClass(/visible/);
  }
});

test("places every visible custom hover label inside the viewport", async ({ page }) => {
  for (const width of [390, 1755]) {
    await page.setViewportSize({ width, height: 600 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    const panel = page.locator("codex-bridge-panel");
    if (width === 390) await panel.locator("#mobile-nav-toggle").click();
    const failures = await page.evaluate(() => {
      const bridge = document.querySelector("codex-bridge-panel");
      const root = bridge.shadowRoot;
      const layer = root.querySelector("#tooltip-layer");
      const failures = [];
      const controls = [...root.querySelectorAll("[data-tooltip]")].filter((node) => {
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= innerWidth
          && box.top >= 0 && box.bottom <= innerHeight && getComputedStyle(node).visibility === "visible";
      });
      for (const control of controls) {
        bridge._showTooltipForTarget(control);
        const rect = layer.getBoundingClientRect();
        if (rect.left < 7 || rect.right > innerWidth - 7 || rect.top < 7 || rect.bottom > innerHeight - 7 || control.hasAttribute("title")) {
          failures.push({ label: control.dataset.tooltip, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, nativeTitle: control.hasAttribute("title") });
        }
      }
      bridge._hideTooltip();
      return failures;
    });
    expect(failures).toEqual([]);
  }
});

test("keeps activity details open while the pointer crosses into the popover", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);
  const panel = page.locator("codex-bridge-panel");
  const chip = panel.locator("#run-step-chip");
  const tooltip = panel.locator("#run-step-tooltip");
  await chip.hover();
  await expect(tooltip).toBeVisible();
  const chipBox = await chip.boundingBox();
  const tooltipBox = await tooltip.boundingBox();
  expect(chipBox && tooltipBox).toBeTruthy();
  const below = tooltipBox.y > chipBox.y;
  await page.mouse.move(chipBox.x + 20, below ? chipBox.y + chipBox.height - 2 : chipBox.y + 2, { steps: 8 });
  await page.mouse.move(tooltipBox.x + 20, below ? tooltipBox.y + 4 : tooltipBox.y + tooltipBox.height - 4, { steps: 16 });
  await expect(tooltip).toBeVisible();
  expect(await chip.locator("..").evaluate((wrap) => wrap.matches(":hover"))).toBe(true);
});

test("keeps visible tooltip controls mounted during unchanged Bridge refreshes", async ({ page }) => {
  await page.setViewportSize({ width: 1755, height: 900 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await seedRunStageActivity(page);
  const disconnected = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const controls = [...bridge.shadowRoot.querySelectorAll("[data-tooltip]")]
      .filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== "hidden");
    bridge._render();
    return controls.filter((node) => !node.isConnected).map((node) => node.dataset.action || node.className || node.tagName);
  });
  expect(disconnected).toEqual([]);

  const panel = page.locator("codex-bridge-panel");
  await panel.locator('[data-side-tab="usage"]').click();
  const limit = panel.locator("#usage-panel .mini-limit").first();
  await limit.hover();
  await expect(panel.locator("#tooltip-layer")).toBeVisible();
  const limitState = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#usage-panel .mini-limit");
    bridge._render();
    return { connected: original.isConnected, hovered: original.matches(":hover") };
  });
  expect(limitState).toEqual({ connected: true, hovered: true });
});

test("keeps a failed run's details open on a narrow dark screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    window.__codexHarness.updateThread("thr_vba_1", { status: "error", last_error: "Codex usage limits have been reached." });
    window.__codexHarness.emitThreadEvent("thr_vba_1", "run.failed", {
      run_id: "failed-tooltip-run", error: "Codex usage limits have been reached.",
    });
  });
  const panel = page.locator("codex-bridge-panel");
  const chip = panel.locator("#run-step-chip");
  const tooltip = panel.locator("#run-step-tooltip");
  await expect(chip).toContainText("Run failed");
  await chip.click();
  await expect(tooltip).toBeVisible();
  const state = await page.evaluate(() => {
    const bridge = document.querySelector("codex-bridge-panel");
    const original = bridge.shadowRoot.querySelector("#run-step-chip");
    for (let index = 0; index < 8; index += 1) {
      bridge.hass = { ...bridge._hass, states: { ...bridge._hass.states } };
      bridge._renderRunActivity();
    }
    return { connected: original.isConnected, expanded: original.getAttribute("aria-expanded") };
  });
  expect(state).toEqual({ connected: true, expanded: "true" });
  await expect(tooltip).toContainText("Run failed");
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

test("keeps chat prose plain and completion singular across themes and widths", async ({ page }, testInfo) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._activeThread = { ...panel._activeThread, status: "idle", active_run_id: null };
    panel._pendingInteractions = [];
    panel._events = [
      ["message.created", { text: "Say hello" }],
      ["run.started", { run_id: "run-style" }],
      ["plan.updated", { run_id: "run-style", plan: [{ step: "Reply", status: "completed" }] }],
      ["message.completed", { run_id: "run-style", text: "Hello. This response uses plain, readable text.\n\nNo whole-message Copy action is shown." }],
      ["run.completed", { run_id: "run-style" }],
    ].map(([event_type, payload], index) => ({ event_id: `style-${index}`, thread_id: panel._selectedThreadId, sequence: 20000 + index, event_type, payload }));
    panel._forceMessageRebuild = true;
    panel._render(true);
    panel._renderInteractions();
  });
  const panel = page.locator("codex-bridge-panel");
  for (const theme of ["light", "dark"]) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await panel.evaluate((node, value) => node.setAttribute("data-panel-theme", value), theme);
      if (width === 390) {
        await expect.poll(() => panel.locator("#workspace-drawer").evaluate((node) => node.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
      }
      await expect(panel.locator(".message .avatar, .message-head")).toHaveCount(0);
      await expect(panel.getByRole("article", { name: "Your message" })).toHaveText("Say hello");
      const prose = panel.locator(".message.assistant .bubble-text");
      await expect(prose).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
      const appearance = await prose.evaluate((node) => {
        const style = getComputedStyle(node);
        const message = node.closest(".message").getBoundingClientRect();

        return { color: style.color, font: style.fontFamily, fits: message.left >= 0 && message.right <= innerWidth };
      });
      expect(appearance.font).not.toMatch(/monospace|consolas|courier/i);
      expect(appearance.color).toBe(theme === "light" ? "rgb(21, 27, 41)" : "rgb(240, 242, 246)");
      await expect(panel.locator(".message-actions")).toHaveCount(0);
      await expect(panel.locator(".message.user .bubble")).toHaveCSS("background-color", "rgb(0, 0, 0)");
      await expect(panel.locator(".message.user .bubble-text")).toHaveCSS("color", "rgb(255, 255, 255)");
      expect(appearance.fits).toBe(true);
      await expect(panel.locator("#run-activity .run-activity-copy, #run-activity .run-step-chip")).toHaveCount(1);
      await expect(panel.locator("#run-step-chip")).toContainText("Run completed");
      await expect(panel.getByRole("button", { name: "Copy response", exact: true })).toHaveCount(0);
      await panel.locator("#prompt-input").focus();
      await page.screenshot({ path: testInfo.outputPath(`chat-${theme}-${width}.png`) });
    }
  }
});

test("gives desktop chats wider space and one working control with code-only copying", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await page.evaluate(() => {
    const node = document.querySelector("codex-bridge-panel");
    node._stopPolling();
    node._activeThread = { ...node._activeThread, status: "running", active_run_id: "layout-run" };
    node._pendingInteractions = [];
    node._events = [
      ["message.created", { text: "Show an example", queued: true }],
      ["run.started", { run_id: "layout-run" }],
      ["item.started", { run_id: "layout-run", item_id: "command-layout", item_type: "commandExecution", command_preview: "git diff --stat" }],
      ["item.completed", { run_id: "layout-run", item_id: "image-layout-1", item_type: "imageView" }],
      ["item.completed", { run_id: "layout-run", item_id: "image-layout-2", item_type: "imageView" }],
      ["message.completed", { text: "Use this expression:\n```javascript\nconst answer = 'hello';\n```\nKeep the explanation readable." }],
    ].map(([event_type, payload], index) => ({ event_id: `layout-${index}`, thread_id: node._selectedThreadId, sequence: 21000 + index, event_type, payload, timestamp: new Date(Date.now() - 90000).toISOString() }));
    node._forceMessageRebuild = true;
    node._render(true);
    node._renderInteractions();
  });
  const columns = await panel.locator(".shell").evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").map(parseFloat));
  expect(columns[0]).toBeCloseTo(330 * 0.85, 1);
  expect(columns[2]).toBeCloseTo(372 * 0.85, 1);
  await expect(panel.locator("#run-activity .run-activity-copy, #run-activity .run-step-chip")).toHaveCount(1);
  await expect(panel.locator("#run-activity .activity-spinner, #run-activity .step-spinner")).toHaveCount(0);
  await expect(panel.locator("#run-activity .run-elapsed-dots span")).toHaveCount(3);
  await expect(panel.locator("#run-activity .run-elapsed-label")).toContainText("Working for 1m");
  await expect(panel.locator(".message.user time")).toContainText(/Today|Yesterday/);
  await expect(panel.locator("#run-step-chip")).toContainText("Running a command");
  await expect(panel.locator("#run-step-chip")).toContainText("Viewed 2 images");
  await panel.locator("#run-step-chip").click();
  await panel.locator(".run-command-details summary").click();
  await expect(panel.locator(".run-command-details pre")).toBeVisible();
  await expect(panel.locator(".run-command-details pre")).toHaveText("git diff --stat");
  await panel.locator("#run-step-chip").click();
  await expect(panel.locator(".message.assistant button")).toHaveCount(1);
  const copy = panel.getByRole("button", { name: "Copy code", exact: true });
  await copy.focus();
  await expect(copy).toBeFocused();
  await expect(panel.locator(".code-text")).toHaveText("const answer = 'hello';\n");
  for (const theme of ["light", "dark"]) {
    await panel.evaluate((node, value) => node.setAttribute("data-panel-theme", value), theme);
    await expect(panel.locator(".message.user .message-state")).toHaveCSS("color", "rgb(255, 255, 255)");
    await page.screenshot({ path: testInfo.outputPath(`chat-layout-${theme}.png`) });
    const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
    expect(accessibility.violations).toEqual([]);
  }
});

test("shows exhausted usage and completes a simulated reset credit", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._stopPolling();
    panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "reset_credits_v1"] };
    const status = {
      auth: { state: "ok", auth_required: false },
      account: { available: true, auth_mode: "chatgpt", plan_type: "pro" },
      limits: { available: true, blocked: true, reset_credits: {
        available_count: 1,
        credits: [{ id: "test-credit", title: "Codex reset", expires_at: 1_900_000_000 }],
      } },
    };
    window.__codexHarness.setStatus(status);
    panel._status = { ...panel._status, ...status };
    panel._render(true);
  });
  const panel = page.locator("codex-bridge-panel");
  await expect(panel.locator("#status-banner")).toContainText("Codex usage limits have been reached");
  const usageAction = panel.getByRole("button", { name: "View usage and resets" });
  for (const theme of ["light", "dark"]) {
    await panel.evaluate((node, value) => node.setAttribute("data-panel-theme", value), theme);
    for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 800 }]) {
      await page.setViewportSize(viewport);
      await usageAction.hover();
      await expect(usageAction).toBeInViewport({ ratio: 1 });
      const banner = panel.locator("#status-banner");
      expect(await banner.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
      expect(accessibility.violations.filter((violation) => violation.id === "color-contrast")).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`usage-banner-${theme}-${viewport.width}.png`) });
    }
  }
  await usageAction.click();
  await expect(panel.locator("#side-panel-usage")).toBeVisible();
  await expect(panel.locator(".reset-credit-section")).toContainText("1 reset credit available");
  await panel.getByRole("button", { name: "Use reset" }).click();
  await expect(panel.locator(".reset-credit-section")).toContainText("cannot be undone");
  await panel.getByRole("button", { name: "Cancel reset" }).click();
  await expect(panel.getByRole("button", { name: "Use reset" })).toBeVisible();
  await panel.getByRole("button", { name: "Use reset" }).click();
  await panel.getByRole("button", { name: "Confirm use of this reset credit" }).click();
  await expect(panel.locator(".reset-credit-section")).toContainText("Reset applied");
  await expect(panel.locator(".reset-credit-section")).toContainText("0 reset credits available");
  expect((await websocketCalls(page, "codex_bridge/consume_reset_credit")).length).toBe(1);
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
  expect(accessibility.violations).toEqual([]);
});

test("keeps filled controls legible and styled buttons responsive on hover", async ({ page }) => {
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  await panel.evaluate((node) => {
    const fixture = document.createElement("div");
    fixture.id = "filled-control-check";
    fixture.style.cssText = "position:fixed;inset:70px auto auto 280px;z-index:100;display:grid;grid-template-columns:repeat(3,max-content);gap:8px;padding:12px;background:var(--surface-bg)";
    for (const className of ["information-primary", "panel-button-primary", "empty-state-cta", "send-button", "schedule-submit", "copy-button", "stop-button"]) {
      const button = document.createElement("button");
      button.className = className;
      button.type = "button";
      button.dataset.hoverCheck = className;
      button.textContent = className;
      button.style.transition = "none";
      fixture.append(button);
    }
    for (const [wrapperClass, buttonName] of [
      ["desktop-toolbar", "desktop-toolbar"],
      ["settings-panel", "settings-panel"],
      ["bottom-panel-header", "bottom-panel-header"],
      ["auth-actions", "auth-primary"],
      ["decision-actions", "decision-accept"],
      ["decision-actions", "decision-answer"],
    ]) {
      const wrapper = document.createElement("div");
      wrapper.className = wrapperClass;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.hoverCheck = buttonName;
      button.textContent = buttonName;
      button.style.transition = "none";
      if (buttonName === "auth-primary") button.className = "primary";
      if (buttonName === "decision-accept") button.dataset.decision = "accept";
      if (buttonName === "decision-answer") button.dataset.action = "answer-interaction";
      if (buttonName === "bottom-panel-header") {
        const actions = document.createElement("div");
        actions.className = "row-actions";
        actions.append(button);
        wrapper.append(actions);
      } else {
        wrapper.append(button);
      }
      fixture.append(wrapper);
    }
    node.shadowRoot.append(fixture);
  });

  for (const theme of ["light", "dark"]) {
    await panel.evaluate((node, value) => node.setAttribute("data-panel-theme", value), theme);
    for (const name of ["information-primary", "panel-button-primary", "empty-state-cta", "send-button", "schedule-submit", "copy-button", "stop-button", "desktop-toolbar", "settings-panel", "bottom-panel-header", "auth-primary", "decision-accept", "decision-answer"]) {
      const button = panel.locator(`#filled-control-check [data-hover-check="${name}"]`);
      const normal = await button.evaluate((node) => {
        const style = getComputedStyle(node);
        return [style.backgroundColor, style.backgroundImage, style.borderColor];
      });
      await button.hover();
      const hovered = await button.evaluate((node) => {
        const style = getComputedStyle(node);
        return [style.backgroundColor, style.backgroundImage, style.borderColor];
      });
      expect(hovered, `${name} hover feedback in ${theme}`).not.toEqual(normal);
      if (!["information-primary", "panel-button-primary", "empty-state-cta", "send-button", "schedule-submit", "auth-primary", "decision-accept", "decision-answer"].includes(name)) continue;
      const contrast = await button.evaluate((node) => {
        const style = getComputedStyle(node);
        if (style.backgroundImage !== "none") return { gradient: true };
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const context = canvas.getContext("2d");
        const swatch = (value) => {
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = value;
          context.fillRect(0, 0, 1, 1);
          return context.getImageData(0, 0, 1, 1).data;
        };
        const foreground = swatch(style.color);
        const background = swatch(style.backgroundColor);
        const channel = (value) => {
          const scaled = value / 255;
          return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
        };
        const luminance = (rgb) => 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
        const light = Math.max(luminance(foreground), luminance(background));
        const dark = Math.min(luminance(foreground), luminance(background));
        return { ratio: (light + 0.05) / (dark + 0.05), opacity: background[3] };
      });
      if (contrast.gradient) continue;
      expect(contrast.opacity, `${name} background in ${theme}`).toBe(255);
      expect(contrast.ratio, `${name} hover contrast in ${theme}`).toBeGreaterThanOrEqual(4.5);
    }
  }
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
  await error.getByRole("button", { name: "Retry connection" }).hover();
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
  expect(accessibility.violations.filter((violation) => violation.id === "color-contrast")).toEqual([]);
  await error.screenshot({ path: test.info().outputPath(`connection-error-${viewport.name}.png`) });
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

for (const width of [390, 1280]) {
  test(`previews an Office file with a recognisable icon at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(async () => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      panel._config = { ...panel._config, capabilities: [...(panel._config?.capabilities || []), "office_preview_v1"] };
      const original = panel._callWS.bind(panel);
      panel._callWS = (action, payload) => action === "preview_artifact"
        ? Promise.resolve({ kind: "document", paragraphs: ["Hello from Word"], truncated: false })
        : original(action, payload);
      const artifact = {
        artifact_id: "art_word_preview", filename: "hello.docx", relative_path: "hello.docx",
        mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size_bytes: 929,
      };
      panel._artifacts = [...panel._artifacts, artifact];
      panel._selectedArtifactId = artifact.artifact_id;
      await panel._loadArtifactPreview(artifact.artifact_id);
      panel._sideTab = "files";
      panel._renderSideTabs();
      panel._renderArtifacts();
      panel._renderArtifactPreview();
    });
    const panel = page.locator("codex-bridge-panel");
    if (width === 390) await panel.locator("#mobile-context-toggle").click();
    const preview = panel.locator("#artifact-preview");
    await expect(preview.locator(".office-preview-page")).toContainText("Hello from Word");
    await expect(panel.locator('.file-select[data-artifact-id="art_word_preview"] .file-type-icon svg')).toBeVisible();
    await expect(preview.locator(".file-type-icon svg")).toBeVisible();
    expect(await preview.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    expect(await preview.locator("img, iframe, object, embed").count()).toBe(0);
  });
}

test("renders a local PDF on canvas without embeds or off-origin requests", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const requests = [];
  const workerResponses = [];
  const pageErrors = [];
  page.on("request", (request) => requests.push(request.url()));
  page.on("response", (response) => {
    if (new URL(response.url()).pathname.endsWith("/codex-bridge-pdf-worker.js")) {
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
  const refresh = panel.locator("#chat-menu-button");
  await expect(refresh).toHaveAttribute("aria-label", "Chat actions");
  await expect(refresh.locator("svg")).toBeVisible();
  await expect(refresh).toHaveCSS("width", "32px");
  await expect(refresh).toHaveCSS("height", "32px");
  await refresh.click();
  await expect(panel.locator('#thread-menu [data-action="refresh-thread"]')).toBeVisible();
  await page.keyboard.press("Escape");
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
    await expect(chip).toHaveAccessibleName(/Step 2 of 3/);
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
    const activityDot = root?.querySelector(".run-elapsed-dots span");
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
      activityDotDuration: activityDot ? getComputedStyle(activityDot).animationDuration : "",
      chipTransition: chipStyle?.transitionDuration || "",
      chipHeight: root?.querySelector("#run-step-chip")?.getBoundingClientRect().height || 0,
    };
  });
  expect(mobileLayout.tooltipRight).toBeLessThanOrEqual(mobileLayout.viewportWidth + 1);
  expect(mobileLayout.tooltipWidth).toBeLessThanOrEqual(mobileLayout.viewportWidth - 24);
  expect(mobileLayout.shellFits).toBe(true);
  expect(mobileLayout.tooltipTransition).toMatch(/0\.01ms|1e-05s|0s/);
  expect(mobileLayout.chipTransition).toMatch(/0\.01ms|1e-05s|0s/);
  expect(mobileLayout.activityDotDuration).toMatch(/0\.01ms|1e-05s|0s/);
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

test("answers an MCP form and opens an explicit HTTPS authorisation link", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const common = {
      thread_id: "thr_vba_1", status: "pending", expires_at: "2099-07-14T12:00:00Z",
    };
    window.__codexHarness.addPendingInteraction({
      ...common, interaction_id: "int_mcp_form_harness", event_id: 802,
      kind: "mcp_form", allowed_actions: ["answer", "decline", "cancel"],
      display: {
        title: "MCP server question", summary: "Choose a result", mcp_server: "calendar",
        mcp_fields: [{ name: "choice", label: "Choice", kind: "select", required: true,
          options: ["yes", "no"], option_labels: ["Yes", "No"] }],
      },
    });
    window.__codexHarness.addPendingInteraction({
      ...common, interaction_id: "int_mcp_url_harness", event_id: 803,
      kind: "mcp_url", allowed_actions: ["accept", "decline", "cancel"],
      display: {
        title: "MCP authorisation request", summary: "Sign in to continue",
        mcp_server: "calendar", mcp_url_host: "login.example.com",
      },
      authorization_url: "https://login.example.com/authorise?one_time=private-value",
    });
    document.querySelector("codex-bridge-panel")._refreshInteractions();
  });
  const panel = page.locator("codex-bridge-panel");
  const form = panel.locator('[data-interaction-id="int_mcp_form_harness"]');
  const url = panel.locator('[data-interaction-id="int_mcp_url_harness"]');
  await expect(form).toContainText("Server: calendar");
  await form.locator("select").selectOption("yes");
  await form.locator('[data-action="answer-mcp-form"]').click();
  await expect(form).toHaveCount(0);
  const answers = await websocketCalls(page, "codex_bridge/answer_mcp_form");
  expect(answers).toHaveLength(1);
  expect(answers[0].payload).toMatchObject({
    interaction_id: "int_mcp_form_harness", thread_id: "thr_vba_1", content: { choice: "yes" },
  });
  await expect(url).toContainText("Destination: login.example.com");
  await expect(url).not.toContainText("private-value");
  const link = url.getByRole("link", { name: "Open login.example.com to continue" });
  await expect(link).toHaveAttribute("href", "https://login.example.com/authorise?one_time=private-value");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
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
    expect(compactLayout.railWidth).toBeGreaterThanOrEqual(255);
    expect(compactLayout.railWidth).toBeLessThanOrEqual(280.5);
    expect(compactLayout.mainWidth).toBeCloseTo(width - compactLayout.railWidth, 0);
    expect(compactLayout.sideOffCanvas).toBe(true);
    expect(compactLayout.readingMeasure).toBeCloseTo(960, 0);
    expect(compactLayout.composerMeasure).toBeCloseTo(960, 0);
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

test("keeps mobile headers and drawers below the device safe area", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await panel.evaluate((element) => {
    element.style.setProperty("--safe-area-inset-top", "48px");
    element._syncViewportHeight();
  });

  const headerTop = await panel.locator(".main-header").evaluate((element) => element.getBoundingClientRect().top);
  expect(headerTop).toBeGreaterThanOrEqual(48);
  await panel.locator("#mobile-nav-toggle").click();
  await expect(panel.locator("#workspace-drawer")).toHaveAttribute("aria-hidden", "false");
  const navTop = await panel.locator("#workspace-drawer").evaluate((element) => element.getBoundingClientRect().top);
  const scrimTop = await panel.locator("#mobile-drawer-scrim").evaluate((element) => element.getBoundingClientRect().top);
  expect(navTop).toBeGreaterThanOrEqual(48);
  expect(scrimTop).toBeGreaterThanOrEqual(48);
  await page.screenshot({ path: testInfo.outputPath("safe-area-navigation.png"), animations: "disabled" });

  await panel.locator("#mobile-drawer-scrim").click({ position: { x: 380, y: 200 } });
  await panel.locator("#mobile-context-toggle").click();
  await expect(panel.locator("#context-drawer")).toHaveAttribute("aria-hidden", "false");
  const contextTop = await panel.locator("#context-drawer").evaluate((element) => element.getBoundingClientRect().top);
  expect(contextTop).toBeGreaterThanOrEqual(48);
  await page.screenshot({ path: testInfo.outputPath("safe-area-context.png"), animations: "disabled" });

  await panel.evaluate((element) => {
    document.body.style.paddingTop = "56px";
    element._syncViewportHeight();
  });
  const insetContextTop = await panel.locator("#context-drawer").evaluate((element) => element.getBoundingClientRect().top);
  const insetHeaderTop = await panel.locator(".main-header").evaluate((element) => element.getBoundingClientRect().top);
  expect(insetContextTop).toBeCloseTo(56, 0);
  expect(insetHeaderTop).toBeCloseTo(56, 0);
});

test("does not strand contextual labels over a touch conversation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = (query) => query === "(hover: none)" ? { matches: true } : original(query);
  });
  const panel = page.locator("codex-bridge-panel");
  await panel.locator("#mobile-context-toggle").click();
  await expect(panel.locator("#tooltip-layer")).toBeHidden();
  await panel.locator("#mobile-drawer-scrim").click({ position: { x: 10, y: 400 } });
  await panel.locator("#prompt-input").fill("Say only hello");
  await panel.locator("#send-button").click();
  await expect(panel.locator("#tooltip-layer")).toBeHidden();
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

  expect(layout.railWidth).toBeGreaterThanOrEqual(255);
  expect(layout.railWidth).toBeLessThanOrEqual(280.5);
  expect(layout.sideWidth).toBeGreaterThanOrEqual(278);
  expect(layout.sideWidth).toBeLessThanOrEqual(305);
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

test("fills the available viewport below the Home Assistant header at desktop and phone sizes", async ({ page }) => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 700 }, { width: 390, height: 520 }, { width: 320, height: 568 }]) {
    await page.setViewportSize(viewport);
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    const panel = page.locator("codex-bridge-panel");
    await page.evaluate(() => {
      document.body.style.height = "70vh";
      document.body.style.paddingTop = "56px";
      document.querySelector("codex-bridge-panel")._syncViewportHeight();
    });
    const layout = await panel.evaluate((element) => {
      const shell = element.shadowRoot.querySelector(".shell").getBoundingClientRect();
      const composer = element.shadowRoot.querySelector(".composer-shell").getBoundingClientRect();
      return {
        top: shell.top,
        bottom: shell.bottom,
        composerBottom: composer.bottom,
        viewport: window.innerHeight,
        horizontalOverflow: element.shadowRoot.querySelector(".main-pane").scrollWidth > element.shadowRoot.querySelector(".main-pane").clientWidth + 1,
      };
    });
    expect(layout.top).toBeCloseTo(56, 0);
    expect(layout.bottom).toBeCloseTo(layout.viewport, 0);
    expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewport);
    expect(layout.horizontalOverflow).toBe(false);
  }
  await page.setViewportSize({ width: 390, height: 620 });
  await expect.poll(() => page.locator("codex-bridge-panel").evaluate((element) =>
    Math.round(element.shadowRoot.querySelector(".shell").getBoundingClientRect().bottom))).toBe(620);
});

test("offers only supported Add actions and keeps uploads and navigation usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 700 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  const toggle = panel.locator("#add-menu-button");
  const menu = panel.locator("#add-menu");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(menu).toBeVisible();
  await expect(menu.locator("#upload-file-button")).toBeVisible();
  await expect(menu.locator("#upload-folder-button")).toBeVisible();
  await expect(menu.locator("#schedule-message-button")).toBeHidden();
  await expect(menu.locator("#add-plugins-button")).toBeHidden();
  await expect(menu.getByText("Attach Google Chrome")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).include("codex-bridge-panel").analyze()).violations).toEqual([]);
  const chooserPromise = page.waitForEvent("filechooser");
  await menu.locator("#upload-file-button").press("Enter");
  await (await chooserPromise).setFiles([]);
  await expect(menu).toBeHidden();
  await expect(toggle).toBeFocused();
  await toggle.press("Enter");
  await expect(menu).toBeVisible();
  const folderChooserPromise = page.waitForEvent("filechooser");
  await menu.locator("#upload-folder-button").press("Enter");
  await folderChooserPromise;
  await expect(menu).toBeHidden();
  await expect(toggle).toBeFocused();
  await toggle.click();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await expect(menu).toBeHidden();

  await panel.evaluate((element) => {
    element._config.capabilities = ["automation_proposals_v1", "plugins_v1"];
    element._renderComposerState(element._activeThread);
  });
  await toggle.click();
  await expect(menu.locator("#schedule-message-button")).toBeVisible();
  await expect(menu.locator("#add-plugins-button")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 320 });
  await expect.poll(() => panel.evaluate((element) => Math.round(element.shadowRoot.querySelector(".shell").getBoundingClientRect().bottom))).toBe(320);
  const menuBounds = await menu.evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { top: box.top, bottom: box.bottom, viewport: window.innerHeight };
  });
  expect(menuBounds.top).toBeGreaterThanOrEqual(0);
  expect(menuBounds.bottom).toBeLessThanOrEqual(menuBounds.viewport);
  await menu.locator("#add-plugins-button").focus();
  await page.keyboard.press("Enter");
  await expect(panel.locator("#desktop-feature-surface")).toContainText("Plugins");
  await expect(panel.locator("#desktop-feature-surface")).toBeFocused();
  await expect(menu).toBeHidden();
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


test("desktop controls show real references, Stop/Steer and context usage", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._stopPolling();
    panel._activeThread = { ...panel._activeThread, context_usage: null };
    panel._renderContextUsage();
  });
  const panel = page.locator("codex-bridge-panel");
  await expect(panel.locator("#context-usage-button")).toBeHidden();
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._stopPolling();
    panel._activeThread = { ...panel._activeThread, status: "running", active_run_id: "control-test", context_usage: { used_tokens: 25000, context_window: 100000 } };
    panel._events = [{ sequence: 1, event_type: "run.started", payload: { run_id: "control-test" } }, { sequence: 2, event_type: "message.completed", payload: { text: "[Login improvement](https://github.com/owner/repo/pull/12)" } }];
    panel._render(true);
  });
  await expect(panel.locator("#context-usage-button")).toBeVisible();
  await expect(panel.locator("#send-button")).toHaveAttribute("aria-label", "Stop");
  await panel.locator("#prompt-input").fill("Use the smaller change");
  await expect(panel.locator("#send-button")).toHaveAttribute("aria-label", "Steer");
  await expect(panel.locator("#stop-run-button")).toBeVisible();
  await expect(panel.locator("#context-usage-button")).toHaveAttribute("aria-label", /25% of context used/);
  await panel.locator('[data-section="pull-requests"] summary').click();
  await expect(panel.locator('[data-section="pull-requests"] a')).toHaveAttribute("href", "https://github.com/owner/repo/pull/12");
  await panel.locator("#toggle-context-button").click();
  await expect(panel.locator("#context-drawer")).toBeHidden();
  await panel.locator("#context-usage-button").click();
  await expect(panel.locator("#side-panel-usage")).toBeVisible();
  await panel.locator('#side-tab-activity').click();
  await page.screenshot({ path: testInfo.outputPath("chat-controls.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.locator("#chat-menu-button")).toBeVisible();
  const overflow = await panel.evaluate((element) => element.shadowRoot.querySelector(".main-header").scrollWidth > element.shadowRoot.querySelector(".main-header").clientWidth);
  expect(overflow).toBe(false);
});

test("workspace terminal accepts interactive input and closes when leaving the chat", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  await page.evaluate(() => {
    const panel = document.querySelector("codex-bridge-panel");
    panel._stopPolling();
    panel._config.capabilities = ["workspace_terminal_v1"];
    panel._activeThread.mode = "edit";
    const original = panel._callWS.bind(panel);
    window.terminalCalls = [];
    let cursor = 1;
    panel._callWS = async (type, payload) => {
      if (type !== "terminal") return original(type, payload);
      window.terminalCalls.push(payload);
      if (payload.operation === "open") return { session_id: "a".repeat(32), state: "running", chunks: [{ sequence: 1, data: btoa("Workspace terminal\r\n$ ") }], message: "Workspace terminal" };
      if (payload.operation === "read") return { state: "running", chunks: payload.after < cursor ? [{ sequence: cursor, data: btoa("\r\nterminal input accepted\r\n$ ") }] : [], message: "Workspace terminal" };
      if (payload.operation === "write") cursor++;
      return {};
    };
    panel._renderChatControls();
  });
  const panel = page.locator("codex-bridge-panel");
  await panel.locator("#toggle-bottom-button").click();
  await panel.locator("#bottom-terminal-button").click();
  await panel.locator("#open-terminal-button").click();
  await expect(panel.locator(".xterm")).toBeVisible();
  await panel.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("echo terminal");
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => window.terminalCalls.filter((item) => item.operation === "write").map((item) => item.data).join(""))).toContain("echo terminal\r");
  await page.keyboard.press("Control+Shift+M");
  await expect(panel.locator("#close-terminal-button")).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath("workspace-terminal.png") });
  const accessibility = await new AxeBuilder({ page }).include("codex-bridge-panel").analyze();
  expect(accessibility.violations).toEqual([]);
  await page.evaluate(() => document.querySelector("codex-bridge-panel")._selectDesktopDestination("settings"));
  await expect.poll(() => page.evaluate(() => window.terminalCalls.some((item) => item.operation === "close"))).toBe(true);
});


for (const width of [1440, 390]) {
  test(`isolated MCP package review fits and creates only a paused server at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      panel._config.capabilities = ["mcp_admin_v1", "mcp_management_v1", "mcp_tool_permissions_v1", "mcp_stdio_v1"];
      window.stdioCalls = [];
      const original = panel._callWS.bind(panel);
      panel._callWS = (method, args) => {
        if (method === "list_mcp") return Promise.resolve({ items: [] });
        if (method === "list_stdio_packages") return Promise.resolve({ items: [{
          package_id: "bridge-time", revision: "1.0.0", title: "Time and timezone",
          source: "https://example.org/bridge-time", licence: "MIT", python: "3.14",
          digest: "a".repeat(64), entrypoint: ["python3.14", "-m", "codex_bridge_time"],
          tools: ["get_current_time"], network: "none", files: "none", environment: [],
        }] });
        if (method === "add_stdio_mcp") { window.stdioCalls.push(args); return Promise.resolve({}); }
        return original(method, args);
      };
      panel._selectDesktopDestination("settings");
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.getByRole("tab", { name: "MCP servers", exact: true }).click();
    await panel.getByRole("button", { name: "Add isolated server" }).click();
    await expect(panel.getByText("Fixed command: python3.14 -m codex_bridge_time")).toBeVisible();
    await expect(panel.getByText(/no network, no workspace files/)).toBeVisible();
    const review = panel.getByLabel("I have reviewed this package, its fixed command, tools and access limits");
    await panel.getByLabel("Server name").fill("time");
    await panel.getByRole("button", { name: "Add paused server" }).click();
    expect(await page.evaluate(() => window.stdioCalls)).toEqual([]);
    await review.check();
    const bounds = await panel.locator(".stdio-package-details").boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await panel.screenshot({ path: testInfo.outputPath("stdio-package-review.png") });
    await panel.getByRole("button", { name: "Add paused server" }).click();
    await expect.poll(() => page.evaluate(() => window.stdioCalls.length)).toBe(1);
    expect(await page.evaluate(() => window.stdioCalls[0])).toEqual({
      name: "time", package_id: "bridge-time", revision: "1.0.0", acknowledged: true,
    });
  });

  test(`MCP tool selection is readable and keyboard accessible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      panel._config.capabilities = ["mcp_admin_v1", "mcp_management_v1", "mcp_tool_permissions_v1"];
      window.toolPolicyCalls = [];
      const original = panel._callWS.bind(panel);
      panel._callWS = (method, args) => {
        if (method === "list_mcp") return Promise.resolve({ items: [{ name: "vendor", endpoint: "https://mcp.example.com", enabled: true, startup: "ready", tool_policy: "all", tool_count: 2, resource_count: 0 }] });
        if (method === "list_mcp_tools") return Promise.resolve({ server: "vendor", endpoint: "https://mcp.example.com", mode: "all", enabled_tools: [], catalogue_available: true,
          revision: "a".repeat(64), catalogue_revision: "b".repeat(64), stale_tools: [], tools: [
            { name: "read", description: "Read a value", read_only: true },
            { name: "erase", description: "Delete a value", write_possible: true, destructive: true },
          ] });
        if (method === "set_mcp_tools") { window.toolPolicyCalls.push(args); return Promise.resolve({}); }
        return original(method, args);
      };
      panel._selectDesktopDestination("settings");
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.getByRole("tab", { name: "MCP servers", exact: true }).click();
    await panel.getByRole("button", { name: "Choose allowed tools" }).click();
    await expect(panel.getByRole("group", { name: "Allowed tools" })).toBeVisible();
    const erase = panel.getByRole("checkbox", { name: /erase/ });
    await erase.focus();
    await page.keyboard.press("Space");
    await expect(erase).not.toBeChecked();
    const bounds = await panel.locator(".mcp-tool-permissions").boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await panel.getByRole("button", { name: "Save allowed tools" }).click();
    await expect.poll(() => page.evaluate(() => window.toolPolicyCalls.length)).toBe(1);
    expect(await page.evaluate(() => window.toolPolicyCalls[0].enabled_tools)).toEqual(["read"]);
  });

  test(`MCP connection edits stay paused and recover from failed saves at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => { window.nativeManagementFetch = window.fetch.bind(window); });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      const fixtureFetch = window.fetch;
      window.fetch = (url, options) => url === "/api/codex_bridge/mcp/connections" ? window.nativeManagementFetch(url, options) : fixtureFetch(url, options);
      panel._config.capabilities = ["mcp_admin_v1", "mcp_credentials_v1", "mcp_management_v1"];
      panel._accessToken = () => "synthetic-ha-token";
      window.managedServer = {name:"secured",endpoint:"https://mcp.example.com",auth:"bearer",network:"public",credential_configured:true,enabled:true,startup:"ready",revision:"a".repeat(64),tool_count:1,resource_count:0};
      const call = panel._callWS.bind(panel);
      panel._callWS = (method, args) => method === "list_mcp" ? Promise.resolve({items:[{...window.managedServer}]}) : call(method, args);
      panel._selectDesktopDestination("settings");
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.getByRole("tab", {name:"MCP servers",exact:true}).click();
    await expect(panel.getByRole("button", {name:"Edit connection",exact:true})).toBeDisabled();
    let failedEdit = false;
    await page.route("**/api/codex_bridge/mcp/connections", async (route) => {
      const payload = route.request().postDataJSON();
      if (payload.operation === "edit" && !failedEdit) {
        failedEdit = true;
        await route.fulfill({status:503,contentType:"application/json",body:'{"code":"mcp_unavailable","message":"private-provider-error"}'});
        return;
      }
      await page.evaluate((change) => {
        window.managedServer.enabled = change.operation === "state" ? change.enabled : false;
        window.managedServer.startup = window.managedServer.enabled ? "ready" : "paused";
        window.managedServer.revision = "b".repeat(64);
      }, payload);
      await route.fulfill({status:200,contentType:"application/json",body:'{"saved":true}'});
    });
    await panel.getByRole("button", {name:"Pause",exact:true}).click();
    await expect(panel.getByRole("button", {name:"Resume",exact:true})).toBeVisible();
    await panel.getByRole("button", {name:"Edit connection",exact:true}).click();
    const url = panel.getByLabel("New connection URL", {exact:true});
    await expect(url).toHaveValue("");
    await url.fill("https://new.example.com/private-fixture");
    const auth = panel.getByRole("combobox", {name:"Authentication when changing destination",exact:true});
    await auth.focus();
    await page.keyboard.press("Enter");
    await panel.getByRole("option", {name:"Keep existing authentication",exact:true}).click();
    const consent = panel.getByLabel("I trust this destination and approve the authentication choice above");
    await consent.check();
    await panel.getByRole("button", {name:"Save paused connection",exact:true}).click();
    await expect(url).toHaveValue("");
    await expect(consent).not.toBeChecked();
    await expect(panel.getByRole("alert").filter({hasText:"Could not confirm"})).toBeVisible();
    await expect(panel).not.toContainText("private-provider-error");
    await url.fill("https://new.example.com/private-fixture");
    await consent.check();
    expect((await new AxeBuilder({page}).include("codex-bridge-panel").withTags(["wcag2a","wcag2aa"]).analyze()).violations).toEqual([]);
    await panel.screenshot({path:testInfo.outputPath("mcp-edit-connection.png")});
    await panel.getByRole("button", {name:"Save paused connection",exact:true}).click();
    await expect(panel.getByText("Connection saved and still paused. Resume it when ready.",{exact:true})).toBeVisible();
    await panel.getByRole("button", {name:"Resume",exact:true}).click();
    await expect(panel.getByRole("button", {name:"Pause",exact:true})).toBeVisible();
  });

  test(`MCP credentials stay write-only with accessible controls at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => { window.nativeCredentialFetch = window.fetch.bind(window); });
    await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
    await selectHarnessThread(page);
    await page.evaluate(() => {
      const panel = document.querySelector("codex-bridge-panel");
      panel._stopPolling();
      const fixtureFetch = window.fetch;
      window.fetch = (url, options) => url === "/api/codex_bridge/mcp/credentials" ? window.nativeCredentialFetch(url, options) : fixtureFetch(url, options);
      panel._config.capabilities = ["mcp_admin_v1", "mcp_local_v1", "mcp_credentials_v1"];
      panel._accessToken = () => "synthetic-ha-token";
      panel._selectDesktopDestination("settings");
    });
    const panel = page.locator("codex-bridge-panel");
    await panel.getByRole("tab", { name:"MCP servers", exact:true }).click();
    await panel.getByRole("button", { name:"Add MCP server", exact:true }).click();
    await panel.getByRole("button", { name:/^Other MCP server/ }).click();
    await panel.getByLabel("Name", { exact:true }).fill("secured");
    await panel.getByLabel("Public HTTPS URL", { exact:true }).fill("https://mcp.example.com");
    const auth = panel.getByRole("combobox", {name:"Authentication", exact:true});
    await auth.click();
    await panel.getByRole("option", {name:"Bearer token", exact:true}).click();
    await panel.getByLabel("Bearer token", {exact:true}).fill("synthetic-private-token");
    await expect(panel.getByLabel("Bearer token", {exact:true})).toHaveAttribute("type", "password");
    await panel.getByLabel("I trust this destination and understand credential transport and backup exposure").check();
    await page.route("**/api/codex_bridge/mcp/credentials", async (route) => {
      expect(route.request().postDataJSON().authentication).toEqual({mode:"bearer",token:"synthetic-private-token"});
      await route.fulfill({ status:400, contentType:"application/json", body:'{"code":"mcp_request_invalid"}' });
    });
    await panel.getByRole("button", {name:"Add server", exact:true}).click();
    await expect(panel.getByLabel("Bearer token", {exact:true})).toHaveValue("");
    await expect(panel.getByRole("alert").filter({hasText:"re-enter"})).toBeVisible();
    await auth.click();
    await panel.getByRole("option", {name:"API-key headers", exact:true}).click();
    await panel.getByLabel("Header name", {exact:true}).fill("X-Api-Key");
    await panel.getByLabel("API-key value", {exact:true}).fill("synthetic-key");
    await panel.getByRole("button", {name:"Add another header", exact:true}).click();
    await expect(panel.getByLabel("Header name", {exact:true})).toHaveCount(2);
    const bounds = await panel.locator(".mcp-authentication").boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({page}).include("codex-bridge-panel").withTags(["wcag2a","wcag2aa"]).analyze()).violations).toEqual([]);
    await panel.screenshot({path:testInfo.outputPath("mcp-credentials.png")});
    expect(await page.evaluate(() => JSON.stringify(document.querySelector("codex-bridge-panel")._desktopFeatures))).not.toContain("synthetic-key");
    await panel.getByRole("button", {name:"Cancel", exact:true}).click();
    await expect(panel.locator("[data-mcp-header-value]")).toHaveCount(0);
  });
}
