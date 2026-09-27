import { createReadStream } from "node:fs";
import { mkdir, mkdtemp, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { tmpdir } from "node:os";

import AxeBuilder from "@axe-core/playwright";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(process.cwd());
const evidenceRoot = resolve(process.env.CODEX_BRIDGE_E2E_OUTPUT_DIR || tmpdir());
let evidenceDirectory;
let bundlePath;
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

let origin;
let server;

test.beforeAll(async () => {
  await mkdir(evidenceRoot, { recursive: true });
  evidenceDirectory = await mkdtemp(resolve(evidenceRoot, "codex-bridge-enhancements-"));
  bundlePath = resolve(evidenceDirectory, "codex-bridge-panel-source.js");
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

async function openPanel(page, { width = 1280, height = 900, theme = "light" } = {}) {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ colorScheme: theme });
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  return waitForPanel(page);
}

async function waitForPanel(page) {
  const panel = page.locator("codex-bridge-panel");
  await expect.poll(() => panel.evaluate((element) => Boolean(element._config) && !element._isLoading)).toBe(true);
  await panel.evaluate((element) => element._stopSystemEventSubscription());
  return panel;
}

async function setHaUser(panel, userId) {
  await panel.evaluate((element, id) => { element.hass = { ...element.hass, user: { id } }; }, userId);
}

async function selectChat(panel, threadId = "thr_direct") {
  await panel.evaluate(async (element, id) => {
    await element._selectThread(id);
    element._stopPolling();
    element._stopEventSubscription();
  }, threadId);
  await expect(panel.locator("#thread-title-label")).not.toBeEmpty();
}

async function addMessage(panel, { key, text, role = "assistant", title = "Fixture chat" }) {
  await panel.evaluate((element, { messageKey, messageText, messageRole, chatTitle }) => {
    element._activeThread = { ...element._activeThread, title: chatTitle };
    const article = element._renderMessage(messageRole, messageText, messageKey);
    element.shadowRoot.getElementById("message-list").append(article);
  }, { messageKey: key, messageText: text, messageRole: role, chatTitle: title });
  return panel.locator(`.message[data-sequence="${key}"]`);
}

async function setDraftRecoveryPreference(panel, value) {
  await panel.evaluate((element) => element._selectDesktopDestination("settings"));
  await expect(panel.getByRole("tab", { name: "General", exact: true })).toBeVisible();
  const picker = panel.getByRole("combobox", { name: "Recover unsent drafts in this browser" });
  await picker.click();
  await panel.getByRole("option", { name: value === "on" ? "On" : "Off", exact: true }).click();
  await expect(panel.getByText("Saved for this Home Assistant user in this browser.", { exact: true })).toBeVisible();
  await panel.evaluate((element) => element._selectDesktopDestination("chats"));
}

async function readOwnedDraftRows(page, ownerKey) {
  return page.evaluate((key) => new Promise((resolveResult, reject) => {
    const request = indexedDB.open("codex-bridge-draft-recovery", 1);
    request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
    request.onsuccess = () => {
      const database = request.result;
      const transaction = database.transaction("drafts", "readonly");
      const rows = transaction.objectStore("drafts").index("ownerKey").getAll(key);
      rows.onsuccess = () => resolveResult(rows.result);
      rows.onerror = () => reject(rows.error || new Error("Draft read failed"));
      transaction.oncomplete = () => database.close();
      transaction.onabort = () => { database.close(); reject(transaction.error || new Error("Draft read aborted")); };
    };
  }), ownerKey);
}

test("real Shadow DOM passage selection copies and quotes only the selected public message", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await page.addInitScript(() => {
    window.__composedRangeCalls = 0;
    const method = Selection.prototype.getComposedRanges;
    if (typeof method === "function") {
      Selection.prototype.getComposedRanges = function (...args) {
        window.__composedRangeCalls += 1;
        return method.apply(this, args);
      };
    }
  });
  const panel = await openPanel(page);
  await setHaUser(panel, "e2e-user-copy");
  await selectChat(panel);
  const response = "Select this complete response passage.";
  const article = await addMessage(panel, { key: 99001, text: response });
  const paragraph = article.locator(".message-content .assistant-markdown-paragraph");
  await expect(paragraph).toHaveText(response);
  expect(await page.evaluate(() => typeof window.getSelection()?.getComposedRanges)).toBe("function");

  await paragraph.selectText();
  await article.getByRole("button", { name: "Copy passage", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(response);
  expect(await page.evaluate(() => window.__composedRangeCalls)).toBeGreaterThan(0);

  const input = panel.locator("#prompt-input");
  await input.fill("Existing editable draft");
  await paragraph.selectText();
  await article.getByRole("button", { name: "Quote passage", exact: true }).click();
  await expect(input).toHaveValue(`Existing editable draft\n\nAssistant response in “Fixture chat”:\n> ${response}`);
  expect(await panel.evaluate((element) => element._draftForThread(element._selectedThreadId))).toContain(response);
  expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(0);

  const quoteButton = article.getByRole("button", { name: "Quote message", exact: true });
  await quoteButton.focus();
  await quoteButton.press("Enter");
  await expect(input).toHaveValue(`Existing editable draft\n\nAssistant response in “Fixture chat”:\n> ${response}\n\nAssistant response in “Fixture chat”:\n> ${response}`);
  expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(0);
});

for (const width of [390, 1280]) {
  for (const theme of ["light", "dark"]) {
    test(`compact chat controls stay readable and keyboard-operable at ${width}px in ${theme} mode`, async ({ page, context }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
      const panel = await openPanel(page, { width, height: 844, theme });
      await setHaUser(panel, `e2e-user-controls-${width}-${theme}`);
      await panel.evaluate((element, selectedTheme) => {
        element._preferences = { ...element._preferences, theme: selectedTheme };
        element._applyPreferences();
        element._config = {
          ...element._config,
          capabilities: [...new Set([...(element._config.capabilities || []), "web_search_v1"])],
          web_search_mode: "live",
        };
        element._render(true);
      }, theme);
      await selectChat(panel);
      await setDraftRecoveryPreference(panel, "on");
      const article = await addMessage(panel, {
        key: 99002,
        text: "Code follows.\n\n```js\nconst answer = 42;\n  keep indentation\n```",
      });

      const search = panel.getByRole("combobox", { name: "Web search" });
      await expect(search).toBeVisible();
      await expect(panel.getByRole("button", { name: "Discard draft", exact: true })).toBeVisible();
      const actionButtons = article.locator(".message-actions button");
      await expect(actionButtons).toHaveCount(4);
      for (const button of await actionButtons.all()) {
        const box = await button.boundingBox();
        expect(box.height).toBeGreaterThanOrEqual(32);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      const searchBox = await search.boundingBox();
      expect(searchBox.height).toBe(32);
      expect(searchBox.x + searchBox.width).toBeLessThanOrEqual(width);

      const codeBlock = article.locator(".code-block");
      const code = codeBlock.locator(".code-text");
      const exactBefore = await code.textContent();
      await codeBlock.getByRole("button", { name: "Line numbers", exact: true }).click();
      await codeBlock.getByRole("button", { name: "Wrap lines", exact: true }).click();
      await expect(codeBlock.getByRole("button", { name: "Line numbers", exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect(codeBlock.getByRole("button", { name: "Wrap lines", exact: true })).toHaveAttribute("aria-pressed", "true");
      expect(await code.textContent()).toBe(exactBefore);
      expect(await code.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      const copyCode = codeBlock.getByRole("button", { name: "Copy original code", exact: true });
      await copyCode.focus();
      const focusStyle = await copyCode.evaluate((button) => {
        const style = getComputedStyle(button);
        return { height: button.getBoundingClientRect().height, outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth };
      });
      expect(focusStyle.height).toBeGreaterThanOrEqual(32);
      expect(focusStyle.outlineStyle).not.toBe("none");
      expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThan(0);
      await copyCode.press("Enter");
      await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("const answer = 42;\n  keep indentation\n");
      const reducedMotion = await copyCode.evaluate((button) => Number.parseFloat(getComputedStyle(button).transitionDuration));
      expect(reducedMotion).toBeLessThan(0.001);
      const colors = await copyCode.evaluate((button) => ({ color: getComputedStyle(button).color, background: getComputedStyle(button).backgroundColor }));
      expect(colors.color).not.toBe(colors.background);

      await expect.poll(async () => (await new AxeBuilder({ page }).include("codex-bridge-panel").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
      await page.screenshot({ path: resolve(evidenceDirectory, `controls-${width}-${theme}.png`), animations: "disabled" });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    });
  }
}

test("touch users can operate per-message actions at a narrow viewport", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, colorScheme: "dark" });
  try {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
    const page = await context.newPage();
    const panel = await openPanel(page, { width: 390, height: 844, theme: "dark" });
    await setHaUser(panel, "e2e-user-touch");
    await selectChat(panel);
    const article = await addMessage(panel, { key: 99003, text: "Touch copy stays inside this message." });
    await article.getByRole("button", { name: "Copy message", exact: true }).tap();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("Touch copy stays inside this message.");
    expect(await article.locator(".message-actions").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  } finally {
    await context.close();
  }
});

test("IndexedDB draft recovery survives reload and isolates user/chat, send, discard and opt-out", async ({ page, context }) => {
  const panel = await openPanel(page);
  await setHaUser(panel, "e2e-user-drafts-a");
  await selectChat(panel, "thr_direct");
  await setDraftRecoveryPreference(panel, "on");

  const input = panel.locator("#prompt-input");
  await input.fill("Unsent draft for direct chat");
  await expect(panel.locator("#draft-recovery-status")).toHaveText("Draft saved in this browser.");
  await selectChat(panel, "thr_vba_1");
  const otherInput = panel.locator("#prompt-input");
  await otherInput.fill("Different project chat draft");
  await expect(panel.locator("#draft-recovery-status")).toHaveText("Draft saved in this browser.");

  await page.reload();
  const reloadedPanel = await openPanel(page);
  await setHaUser(reloadedPanel, "e2e-user-drafts-a");
  await expect(reloadedPanel.locator("#prompt-input")).toHaveValue("Unsent draft for direct chat");
  await expect(reloadedPanel.locator("#draft-recovery-status")).toHaveText("Unsent draft restored. Review and edit it before sending.");
  expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(0);
  await selectChat(reloadedPanel, "thr_vba_1");
  await expect(reloadedPanel.locator("#prompt-input")).toHaveValue("Different project chat draft");

  const secondTab = await context.newPage();
  const secondPanel = await openPanel(secondTab);
  await setHaUser(secondPanel, "e2e-user-drafts-a");
  await selectChat(secondPanel, "thr_direct");
  const firstStore = reloadedPanel.evaluate(async (element) => {
    element._draftRecoveryStore.now = () => Date.now() + 5_000;
    return element._draftRecoveryStore.saveDraft("thr_direct", "newer edit wins across tabs");
  });
  expect(await firstStore).toMatchObject({ ok: true, saved: true });
  const delayedStore = await secondPanel.evaluate(async (element) => {
    element._draftRecoveryStore.now = () => Date.now() + 1_000;
    return element._draftRecoveryStore.saveDraft("thr_direct", "stale delayed edit");
  });
  expect(delayedStore).toMatchObject({ ok: false, reason: "stale_write" });
  expect(await readOwnedDraftRows(page, "codex-bridge:preferences:e2e-user-drafts-a")).toEqual(expect.arrayContaining([
    expect.objectContaining({ chatId: "thr_direct", text: "newer edit wins across tabs" }),
    expect.objectContaining({ chatId: "thr_vba_1", text: "Different project chat draft" }),
  ]));

  const userB = await context.newPage();
  const userBPanel = await openPanel(userB);
  await setHaUser(userBPanel, "e2e-user-drafts-b");
  await selectChat(userBPanel, "thr_direct");
  await setDraftRecoveryPreference(userBPanel, "on");
  await expect(userBPanel.locator("#prompt-input")).toHaveValue("");
  await expect(userBPanel.locator("#draft-recovery-status")).not.toHaveText("Unsent draft restored. Review and edit it before sending.");

  await setHaUser(reloadedPanel, "e2e-user-drafts-a");
  await selectChat(reloadedPanel, "thr_vba_1");
  await reloadedPanel.getByRole("button", { name: "Discard draft", exact: true }).click();
  await expect(reloadedPanel.locator("#prompt-input")).toHaveValue("");
  await expect(reloadedPanel.locator("#draft-recovery-status")).toHaveText("Saved draft text removed.");
  expect(await readOwnedDraftRows(page, "codex-bridge:preferences:e2e-user-drafts-a")).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ chatId: "thr_vba_1" }),
  ]));

  await selectChat(reloadedPanel, "thr_direct");
  await reloadedPanel.locator("#prompt-input").fill("Send this draft exactly once");
  await expect(reloadedPanel.locator("#draft-recovery-status")).toHaveText("Draft saved in this browser.");
  await reloadedPanel.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(1);
  await expect(reloadedPanel.locator("#prompt-input")).toHaveValue("");
  await page.reload();
  const finalPanel = await openPanel(page);
  await setHaUser(finalPanel, "e2e-user-drafts-a");
  await expect(finalPanel.locator("#prompt-input")).toHaveValue("");

  await setDraftRecoveryPreference(finalPanel, "off");
  await expect.poll(() => readOwnedDraftRows(page, "codex-bridge:preferences:e2e-user-drafts-a")).toEqual([]);
  await page.reload();
  const optedOutPanel = await openPanel(page);
  await setHaUser(optedOutPanel, "e2e-user-drafts-a");
  await expect(optedOutPanel.locator("#draft-recovery-controls")).toBeHidden();
  await expect(optedOutPanel.locator("#prompt-input")).toHaveValue("");
});


test("IndexedDB send acknowledgement preserves a newer same-text second-tab revision after reload", async ({ page, context }) => {
  const userId = "e2e-draft-cas-owner";
  const ownerKey = `codex-bridge:preferences:${userId}`;
  const chatId = "thr_direct";
  const text = "Same text, distinct edit revisions";
  async function selectDraftOwner(panel) {
    // Assign the fixture identity before deliberate selection, avoiding an in-flight
    // restore for the harness's provisional default selection.
    await panel.evaluate((element) => element._setSelectedThreadId(null));
    await setHaUser(panel, userId);
    await selectChat(panel, chatId);
  }
  const panel = await openPanel(page);
  await selectDraftOwner(panel);
  // This focused case exercises storage and acknowledgement, not the settings picker.
  await panel.evaluate((element) => element._savePreferences({ ...element._preferences, draftRecovery: "on" }));
  await panel.locator("#prompt-input").fill(text);
  await expect(panel.locator("#draft-recovery-status")).toHaveText("Draft saved in this browser.");

  await page.reload();
  const sender = await waitForPanel(page);
  await selectDraftOwner(sender);
  await expect(sender.locator("#prompt-input")).toHaveValue(text);
  expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(0);

  const secondPage = await context.newPage();
  try {
    const other = await openPanel(secondPage);
    await selectDraftOwner(other);
    await expect(other.locator("#prompt-input")).toHaveValue(text);
    await sender.evaluate((element) => {
      const original = element._callWS.bind(element);
      element._callWS = async (action, payload) => {
        const result = await original(action, payload);
        if (action === "send_prompt") {
          await new Promise((resolveAcknowledgement) => { window.__releaseDraftAcknowledgement = resolveAcknowledgement; });
        }
        return result;
      };
    });
    await sender.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => page.evaluate(() => typeof window.__releaseDraftAcknowledgement)).toBe("function");
    const sentSave = await sender.evaluate(async (element) => {
      window.__sentDraftMutation = element._promptMutation;
      return element._promptMutation.draftSave;
    });
    expect(sentSave).toMatchObject({ ok: true, saved: true });

    // A genuine second tab commits identical text through the actual IndexedDB store.
    const newerSave = await other.evaluate((element, draftText) => element._setDraftForThread(element._selectedThreadId, draftText), text);
    expect(newerSave).toMatchObject({ ok: true, saved: true });
    expect(newerSave.revision).not.toEqual(sentSave.revision);
    await page.evaluate(() => window.__releaseDraftAcknowledgement());
    await expect.poll(() => sender.evaluate((element) => element._promptMutationForThread(element._selectedThreadId))).toBeNull();
    await page.evaluate(() => window.__sentDraftMutation.draftRemoval);
    await expect(sender.locator("#prompt-input")).toHaveValue("");
    expect(await readOwnedDraftRows(page, ownerKey)).toEqual([
      expect.objectContaining({ chatId, text, revision: newerSave.revision }),
    ]);
    expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(1);

    await page.reload();
    const restored = await waitForPanel(page);
    await selectDraftOwner(restored);
    await expect(restored.locator("#prompt-input")).toHaveValue(text);
    expect(await page.evaluate(() => window.__codexHarness.calls.filter((call) => call.type === "codex_bridge/send_prompt").length)).toBe(0);
    expect(await readOwnedDraftRows(page, ownerKey)).toEqual([
      expect.objectContaining({ chatId, text, revision: newerSave.revision }),
    ]);
  } finally {
    await secondPage.close();
  }
});
