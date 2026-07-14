/** @vitest-environment jsdom */
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("shipped Home Assistant panel", () => {
  it("imports the committed custom element without remote runtime dependencies", async () => {
    await import("../../custom_components/codex_bridge/frontend/codex-bridge-panel.js");
    expect(customElements.get("codex-bridge-panel")).toBeTypeOf("function");

    const bundle = await readFile(
      resolve("custom_components/codex_bridge/frontend/codex-bridge-panel.js"),
      "utf8"
    );
    expect(bundle).not.toMatch(/(?:import\s*\(|\bfrom\s*)["']https?:\/\//u);
    expect(bundle).not.toMatch(/<iframe\b/iu);
    expect(bundle).not.toContain("/attachments");
    expect(bundle).toContain("/uploads");
  });
});
