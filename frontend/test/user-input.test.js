import { describe, expect, it } from "vitest";

import { collectUserInputAnswers, getUserInputViewModel, renderUserInput } from "../src/views/user-input.js";

const NOW = Date.parse("2026-07-14T12:00:00Z");

function question(overrides = {}) {
  return {
    interaction_id: "interaction-question-1",
    status: "pending",
    expires_at: "2026-07-14T12:05:00Z",
    allowed_actions: ["answer", "cancel"],
    display: {
      title: "Choose the scope",
      summary: "Codex needs an answer before continuing.",
      questions: [{
        question_id: "scope",
        header: "Scope",
        prompt: "Which files should Codex update?",
        options: [
          { label: "Source only", description: "Update source files." },
          { label: "Source and docs", description: "Keep docs aligned." },
        ],
        multiple: false,
        allow_free_text: true,
      }],
    },
    ...overrides,
  };
}

describe("user input view", () => {
  it("requires an answer before the submit action is enabled", () => {
    const unanswered = getUserInputViewModel(question(), { now: NOW });
    const answered = getUserInputViewModel(question(), { now: NOW, answers: { scope: "Source only" } });

    expect(unanswered.submitDisabled).toBe(true);
    expect(answered.submitDisabled).toBe(false);
    expect(answered.questions[0].selected).toEqual(["Source only"]);
  });

  it("renders bounded options, free text, labels, and live status without HTML execution", () => {
    const container = document.createElement("div");
    const model = getUserInputViewModel(question({
      display: {
        title: '<img src=x onerror="window.__questionXss=1">',
        summary: "<script>window.__questionXss=2</script>",
        questions: [{
          question_id: "scope",
          header: "<b>Scope</b>",
          prompt: "<svg onload=window.__questionXss=3>",
          options: [
            { label: "<img>", description: "<script>description</script>" },
            { label: "Valid", description: "Safe option" },
            { label: "Third", description: "Safe third" },
            { label: "Fourth", description: "Must be bounded" },
          ],
          multiple: false,
          allow_free_text: true,
        }],
      },
    }), { now: NOW });
    renderUserInput(container, model);

    expect(container.querySelectorAll("img, script, svg, [onerror], [onload]")).toHaveLength(0);
    expect(container.querySelectorAll("input[type='radio']")).toHaveLength(3);
    expect(container.querySelector("textarea")?.getAttribute("aria-label")).toBe("<b>Scope</b>: other answer");
    expect(container.querySelector("textarea")?.maxLength).toBe(4096);
    expect(container.querySelector("[role='status']")?.getAttribute("aria-live")).toBe("polite");
  });

  it("prefers a free-text answer over a radio selection for a single-answer question", () => {
    const container = document.createElement("div");
    const model = getUserInputViewModel(question(), { now: NOW, answers: { scope: "Source only" } });
    renderUserInput(container, model);
    const freeText = container.querySelector("textarea");
    freeText.value = "Also update the changelog";

    expect(collectUserInputAnswers(container, model)).toEqual([{
      question_id: "scope",
      values: ["Also update the changelog"],
    }]);
  });

  it("names repeated provider question IDs uniquely across interactions", () => {
    const first = document.createElement("div");
    const second = document.createElement("div");
    renderUserInput(first, getUserInputViewModel(question({ interaction_id: "interaction-one" }), { now: NOW }));
    renderUserInput(second, getUserInputViewModel(question({ interaction_id: "interaction-two" }), { now: NOW }));

    const firstId = first.querySelector("input")?.id;
    const secondId = second.querySelector("input")?.id;
    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
    expect(first.querySelector("label")?.htmlFor).toBe(firstId);
    expect(second.querySelector("label")?.htmlFor).toBe(secondId);
  });

  it("preserves provider question IDs separately from safe DOM IDs", () => {
    const container = document.createElement("div");
    const providerId = "scope [provider value]";
    const interaction = question({
      display: {
        ...question().display,
        questions: [{ ...question().display.questions[0], question_id: providerId }],
      },
    });
    const model = getUserInputViewModel(interaction, {
      now: NOW,
      answers: { [providerId]: "Source only" },
    });
    renderUserInput(container, model);

    expect(container.querySelector("input")?.id).not.toContain("[");
    expect(collectUserInputAnswers(container, model)).toEqual([{
      question_id: providerId,
      values: ["Source only"],
    }]);
  });

  it("makes question control IDs unique when a malformed id falls back to a valid provider id", () => {
    const container = document.createElement("div");
    const interaction = question({
      display: {
        ...question().display,
        questions: [
          { ...question().display.questions[0], question_id: "bad id" },
          { ...question().display.questions[0], question_id: "question-1" },
        ],
      },
    });
    const model = getUserInputViewModel(interaction, { now: NOW });
    renderUserInput(container, model);

    const controls = [...container.querySelectorAll("input")];
    expect(new Set(controls.map((control) => control.id)).size).toBe(controls.length);
    expect(new Set(controls.map((control) => control.name)).size).toBe(2);
    expect(controls.map((control) => control.dataset.questionId)).toContain("question-1");
  });

  it.each([
    [{ pending: true }, "submitting"],
    [{ stale: true }, "stale"],
    [{ now: Date.parse("2026-07-14T12:06:00Z") }, "expired"],
  ])("disables questions and the answer action while %s", (state, expected) => {
    const container = document.createElement("div");
    const model = getUserInputViewModel(question(), { now: NOW, answers: { scope: "Source only" }, ...state });
    renderUserInput(container, model);

    expect(model.state).toBe(expected);
    expect(container.querySelector("fieldset")?.disabled).toBe(true);
    expect(container.querySelectorAll("button:disabled")).toHaveLength(1);
  });

  it("fails closed for missing/invalid questions and unavailable server actions", () => {
    const invalid = getUserInputViewModel(question({ display: { questions: [{ question_id: "bad id", options: [] }] } }), { now: NOW });
    const blocked = getUserInputViewModel(question({ allowed_actions: [] }), { now: NOW, answers: { scope: "Source only" } });

    expect(invalid.submitDisabled).toBe(true);
    expect(blocked.submitDisabled).toBe(true);
  });
});
