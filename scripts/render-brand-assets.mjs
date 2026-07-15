import { mkdir, copyFile, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assets = [
  {
    source: "brand/icon.svg",
    width: 256,
    height: 256,
    output: "brand/icon.png",
    copies: [
      "codex_bridge_app/icon.png",
      "custom_components/codex_bridge/brand/icon.png",
    ],
  },
  {
    source: "brand/logo.svg",
    width: 1024,
    height: 256,
    output: "brand/logo.png",
    copies: [
      "codex_bridge_app/logo.png",
      "custom_components/codex_bridge/brand/logo.png",
    ],
  },
  {
    source: "brand/social-preview.svg",
    width: 1280,
    height: 640,
    output: "brand/social-preview.png",
    copies: [],
  },
];

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const asset of assets) {
    const source = await readFile(path.join(root, asset.source), "utf8");
    await page.setViewportSize({ width: asset.width, height: asset.height });
    await page.setContent(
      `<style>html,body{margin:0;background:transparent;overflow:hidden}svg{display:block}</style>${source}`,
      { waitUntil: "load" },
    );
    const output = path.join(root, asset.output);
    await mkdir(path.dirname(output), { recursive: true });
    await page.locator("svg").screenshot({
      path: output,
      omitBackground: true,
      animations: "disabled",
    });
    for (const copy of asset.copies) {
      const destination = path.join(root, copy);
      await mkdir(path.dirname(destination), { recursive: true });
      await copyFile(output, destination);
    }
  }
} finally {
  await browser.close();
}
