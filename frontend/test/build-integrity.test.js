/** @vitest-environment jsdom */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("shipped Home Assistant panel", () => {
  // Vitest transforms the complete generated bundle before importing it; a cold
  // Windows run can exceed the default 5s even when the shipped bundle is valid.
  it("imports the committed custom element without remote runtime dependencies", async () => {
    await import("../../custom_components/codex_bridge/frontend/codex-bridge-panel.js");
    expect(customElements.get("codex-bridge-panel")).toBeTypeOf("function");

    const bundle = await readFile(
      resolve("custom_components/codex_bridge/frontend/codex-bridge-panel.js"),
      "utf8"
    );
    const pdfWorker = await readFile(
      resolve("custom_components/codex_bridge/frontend/codex-bridge-pdf-worker.js"),
      "utf8"
    );
    expect(bundle).not.toMatch(/(?:import\s*\(|\bfrom\s*)["']https?:\/\//u);
    expect(pdfWorker).not.toMatch(/(?:import\s*\(|\bfrom\s*)["']https?:\/\//u);
    expect(bundle).not.toMatch(/<iframe\b/iu);
    expect(bundle).toContain("codex-bridge-pdf-worker.js");
    expect(bundle).not.toContain("/attachments");
    expect(bundle).toContain("/uploads");
  }, 15_000);
});
