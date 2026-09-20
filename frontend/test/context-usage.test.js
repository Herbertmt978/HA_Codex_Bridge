import { describe, expect, it } from "vitest";
import { contextUsage } from "../src/context-usage.js";

describe("reported context usage", () => {
  it("distinguishes unavailable readings from an empty context", () => {
    for (const value of [null, {}, { used_tokens: "200" }, { used_tokens: -1 }, { used_tokens: Infinity }]) {
      expect(contextUsage(value).known).toBe(false);
    }
    expect(contextUsage({ used_tokens: 0, context_window: 1000 }).percent).toBe(0);
  });
  it("clamps the ring and handles an unreported window without inventing a percentage", () => {
    expect(contextUsage({ used_tokens: 1200, context_window: 1000 }).percent).toBe(100);
    expect(contextUsage({ used_tokens: 200, context_window: null }).percent).toBeNull();
    expect(contextUsage({ used_tokens: 200, context_window: null }).label).toContain("window size not reported");
  });
});
