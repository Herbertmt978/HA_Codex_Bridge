import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

let origin;
let server;

test.beforeAll(async () => {
  server = createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://ha.invalid").pathname;
    const relativePath = pathname === "/" ? "frontend/e2e/panel-harness.html" : pathname.slice(1);
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

test("runs the Home Assistant first-run and ChatGPT device sign-in flow without exposing runtime secrets", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel = page.locator("codex-bridge-panel");
  const onboarding = panel.locator("#onboarding");
  const auth = panel.locator("#auth-panel");

  await expect(onboarding).toContainText("App connected");
  await expect(onboarding).toContainText("Integration confirmed");
  await expect(onboarding).toContainText("Bridge ready");
  await expect(onboarding).toContainText("Codex ready");
  await expect(panel.locator("#runtime-strip")).toContainText("App 0.6.0");
  await expect(panel.locator("#runtime-strip")).toContainText("Codex 0.144.1");
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
  await createdProject.locator('button[data-action="toggle-project-actions"]').click();
  await createdProject.locator('button[data-action="new-chat"]').click();
  await panel.locator("#thread-title-input").fill("First Home Assistant chat");
  await panel.locator('button[data-action="save-thread"]').click();
  await expect(panel.locator("#thread-title-label")).toContainText("First Home Assistant chat");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.locator("#runtime-strip")).toBeVisible();

  await page.emulateMedia({ colorScheme: "light" });
  await expect(panel.locator("#runtime-strip")).toBeVisible();
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
    run_id: "run_harness",
    turn_id: "turn_harness",
    item_id: "item_command_harness",
    decision: "accept",
  });
  expect(decisions[0].payload.client_request_id).toMatch(/^[A-Za-z0-9_.:-]{1,256}$/);

  await question.getByLabel("Source and tests").check();
  await question.locator('[data-action="answer-interaction"]').click();
  await expect(question).toHaveCount(0);
  const answers = await websocketCalls(page, "codex_bridge/answer_interaction");
  expect(answers).toHaveLength(1);
  expect(answers[0].payload).toMatchObject({
    interaction_id: "int_question_harness",
    thread_id: "thr_vba_1",
    run_id: "run_harness",
    turn_id: "turn_harness",
    item_id: "item_question_harness",
    answers: [{ question_id: "scope", values: ["Source and tests"] }],
  });
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

test("uses a Codex-like reading workspace with an adjacent context rail and mobile chat-first order", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  await selectHarnessThread(page);
  const panel = page.locator("codex-bridge-panel");
  await expect(panel.locator("#archived-chat-list")).toBeHidden();
  await expect(panel.locator("#onboarding-shell")).toBeHidden();

  const desktop = await page.evaluate(() => {
    const root = document.querySelector("codex-bridge-panel")?.shadowRoot;
    const rect = (selector) => root?.querySelector(selector)?.getBoundingClientRect();
    const main = rect(".main-pane");
    const side = rect(".side-pane");
    const messages = rect("#message-list");
    const composerElement = root?.querySelector(".composer-shell");
    const composer = composerElement?.getBoundingClientRect();
    const toolbar = root?.querySelector("#compact-toolbar");
    const bubble = root?.querySelector(".bubble-text");
    const railElement = root?.querySelector(".rail-pane");
    const mainElement = root?.querySelector(".main-pane");
    const sideElement = root?.querySelector(".side-pane");
    return {
      contextIsAdjacent: Boolean(main && side && side.left >= main.right - 1 && Math.abs(side.top - main.top) < 2),
      readingMeasure: messages?.width || 0,
      composerMeasure: composer?.width || 0,
      toolbarInComposer: Boolean(toolbar && composerElement?.contains(toolbar)),
      proseFont: bubble ? getComputedStyle(bubble).fontFamily : "",
      railBackground: railElement ? getComputedStyle(railElement).backgroundColor : "",
      mainBackground: mainElement ? getComputedStyle(mainElement).backgroundColor : "",
      sideBackground: sideElement ? getComputedStyle(sideElement).backgroundColor : "",
    };
  });
  expect(desktop.contextIsAdjacent).toBe(true);
  expect(desktop.readingMeasure).toBeLessThanOrEqual(900);
  expect(desktop.composerMeasure).toBeLessThanOrEqual(900);
  expect(desktop.toolbarInComposer).toBe(true);
  expect(desktop.proseFont).not.toMatch(/monospace|consolas|courier/i);
  expect(desktop.railBackground).not.toBe(desktop.mainBackground);
  expect(desktop.sideBackground).not.toBe(desktop.mainBackground);

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
    bridgePanel._render(true);
  });

  const emptyState = panel.locator(".empty-state-main");
  await expect(emptyState).toContainText("Start a new chat");
  await expect(emptyState.locator(".empty-state-mark svg")).toBeVisible();
  const newChat = emptyState.getByRole("button", { name: "Create a new direct chat" });
  await expect(newChat).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("empty-workspace.png"), fullPage: true });

  await newChat.click();
  await expect(panel.locator("#thread-form-panel")).toHaveClass(/visible/);
  await expect(panel.locator("#thread-title-input")).toBeFocused();
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
  expect(collapsedHeight).toBeLessThan(250);
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
