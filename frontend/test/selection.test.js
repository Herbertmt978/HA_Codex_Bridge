import { afterEach, describe, expect, it } from "vitest";
import { selection } from "../src/selection.js";
import { renderScheduleForm } from "../src/scheduled-tasks.js";

afterEach(() => document.body.replaceChildren());

describe("panel selections", () => {
  it("supports keyboard navigation, disabled items, commit and Escape", () => {
    const picker = selection(document, { name: "repeat", label: "Repeat", value: "daily", options: [["daily", "Daily"], ["disabled", "Not available", true], ["weekly", "Weekly"]] });
    document.body.append(picker);
    const trigger = picker.querySelector("button");
    const key = (name) => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true }));
    trigger.focus(); key("ArrowDown"); key("ArrowDown"); key("Enter");
    expect(picker.querySelector("select").value).toBe("weekly");
    expect(trigger.textContent).toContain("Weekly");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    key("ArrowDown"); key("Home"); key("Escape");
    expect(picker.querySelector("select").value).toBe("weekly");
    expect(document.activeElement).toBe(trigger);
    key("ArrowDown"); key("d"); key("Enter");
    expect(picker.querySelector("select").value).toBe("daily");
  });

  it("keeps saved unavailable model/reasoning choices, but updates reasoning on an explicit model change", () => {
    const form = renderScheduleForm(document, { editingAutomation: { model: "old-model", thinking: "ultra" } }, "UTC", {
      defaultModel: "astra", models: [{ model: "astra", display_name: "Astra", thinking_levels: ["medium", "high"] }],
    });
    document.body.append(form);
    const model = form.querySelector('[name="model"]');
    const reasoning = form.querySelector('[name="thinking"]');
    expect(model.tagName).toBe("SELECT"); expect(reasoning.tagName).toBe("SELECT");
    expect(model.value).toBe("old-model"); expect(reasoning.value).toBe("ultra");
    expect(model.selectedOptions[0].textContent).toContain("saved, unavailable");
    model.value = "astra"; model.dispatchEvent(new Event("change", { bubbles: true }));
    expect(reasoning.value).toBe("");
    expect([...reasoning.options].map((item) => item.value)).toEqual(["", "medium", "high"]);
  });
});
