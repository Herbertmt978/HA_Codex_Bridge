import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("panel dynamic rendering boundary", () => {
  it("keeps innerHTML limited to the static template and trusted icon helper", async () => {
    const source = await readFile(resolve("frontend/src/codex-bridge-panel.js"), "utf8");
    const occurrences = [...source.matchAll(/\.innerHTML\s*=/gu)];

    expect(occurrences).toHaveLength(2);
    expect(source).toMatch(/template\.innerHTML\s*=/u);
    expect(source).toMatch(/iconTemplate\.innerHTML\s*=/u);
  });
});
