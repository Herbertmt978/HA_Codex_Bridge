const MAX_QUESTIONS = 3;
const MAX_OPTIONS = 3;
const MAX_FREE_TEXT = 4096;

function plainText(value, limit) {
  if (typeof value !== "string") return "";
  return [...value]
    .filter((character) => {
      const code = character.codePointAt(0);
      return code > 31 && code !== 127;
    })
    .join("")
    .trim()
    .slice(0, limit);
}

function safeId(value, fallback) {
  return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/u.test(value) ? value : fallback;
}

function expiryState(value, now) {
  if (typeof value !== "string") return { expired: true, label: "Expiry unavailable" };
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return { expired: true, label: "Expiry unavailable" };
  if (timestamp <= now) return { expired: true, label: "Expired" };
  return { expired: false, label: `Expires ${new Date(timestamp).toISOString().replace(".000", "")}` };
}

function selectedValues(value, options, allowFreeText) {
  const raw = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
  const allowed = new Set(options.map((option) => option.label));
  return raw
    .map((item) => plainText(item, MAX_FREE_TEXT))
    .filter((item) => item && (allowed.has(item) || allowFreeText))
    .slice(0, 32);
}

/** Build a bounded, render-safe form model from a user-input interaction. */
export function getUserInputViewModel(interaction = {}, { now = Date.now(), pending = false, stale = false, answers = {} } = {}) {
  const display = interaction && typeof interaction.display === "object" ? interaction.display : {};
  const expiry = expiryState(interaction?.expires_at, now);
  const interactionPending = interaction?.status === "pending";
  const unavailable = Boolean(pending || stale || expiry.expired || !interactionPending);
  const state = pending ? "submitting" : stale ? "stale" : expiry.expired ? "expired" : interactionPending ? "ready" : "unavailable";
  const questions = (Array.isArray(display.questions) ? display.questions : []).slice(0, MAX_QUESTIONS).map((raw, index) => {
    const question = raw && typeof raw === "object" ? raw : {};
    const id = plainText(question.question_id, 128) || `question-${index + 1}`;
    // The provider id remains the payload key. The index makes the DOM-only id
    // unconditionally unique, including a malformed id that falls back to the
    // same string as another valid provider id.
    const domId = `${safeId(question.question_id, `question-${index + 1}`)}-${index + 1}`;
    const options = (Array.isArray(question.options) ? question.options : []).slice(0, MAX_OPTIONS).map((rawOption) => {
      const option = rawOption && typeof rawOption === "object" ? rawOption : {};
      return { label: plainText(option.label, 160), description: plainText(option.description, 512) };
    }).filter((option) => option.label && option.description);
    const allowFreeText = Boolean(question.allow_free_text);
    const selected = selectedValues(answers?.[id], options, allowFreeText);
    return {
      id,
      domId,
      header: plainText(question.header, 160) || `Question ${index + 1}`,
      prompt: plainText(question.prompt, 2048) || "Choose an answer to continue.",
      options,
      multiple: Boolean(question.multiple),
      allowFreeText,
      selected,
      complete: selected.length > 0,
    };
  });
  const ready = questions.length > 0 && questions.every((question) => question.complete);
  const allowedActions = Array.isArray(interaction?.allowed_actions) ? interaction.allowed_actions : [];
  return {
    interactionId: typeof interaction?.interaction_id === "string" ? interaction.interaction_id : "",
    title: plainText(display.title, 160) || "Codex has a question",
    summary: plainText(display.summary, 512) || "Answer to continue this Codex turn.",
    expiry: expiry.label,
    state,
    disabled: unavailable,
    questions,
    submitDisabled: unavailable || !ready || !allowedActions.includes("answer"),
  };
}

/** Render an accessible, text-only user-question form. Callers submit the resulting native values. */
export function renderUserInput(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = `user-input-card user-input-${model.state}`;
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "false");
  card.tabIndex = -1;
  const accessibleId = /^[A-Za-z0-9_.:-]{1,128}$/u.test(model.interactionId)
    ? model.interactionId
    : "pending";
  const titleId = `question-${accessibleId}-title`;
  const summaryId = `question-${accessibleId}-summary`;
  card.setAttribute("aria-labelledby", titleId);
  card.setAttribute("aria-describedby", summaryId);
  const title = document.createElement("h3");
  title.id = titleId;
  title.textContent = model.title;
  const summary = document.createElement("p");
  summary.id = summaryId;
  summary.textContent = model.summary;
  const status = document.createElement("p");
  status.className = "decision-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = model.state === "submitting" ? "Sending answer..." : model.expiry;
  card.append(title, summary, status);

  for (const question of model.questions) {
    const fieldset = document.createElement("fieldset");
    fieldset.disabled = model.disabled;
    const legend = document.createElement("legend");
    legend.textContent = question.header;
    const prompt = document.createElement("p");
    prompt.textContent = question.prompt;
    fieldset.append(legend, prompt);
    for (const [index, option] of question.options.entries()) {
      const id = `question-${accessibleId}-${question.domId}-option-${index + 1}`;
      const optionLabel = document.createElement("label");
      optionLabel.htmlFor = id;
      const control = document.createElement("input");
      control.type = question.multiple ? "checkbox" : "radio";
      control.id = id;
      control.name = `question-${accessibleId}-${question.domId}`;
      control.value = option.label;
      control.dataset.questionId = question.id;
      control.dataset.answerValue = option.label;
      control.checked = question.selected.includes(option.label);
      const copy = document.createElement("span");
      copy.textContent = option.label;
      const description = document.createElement("small");
      description.textContent = option.description;
      optionLabel.append(control, copy, description);
      fieldset.append(optionLabel);
    }
    if (question.allowFreeText) {
      const freeTextId = `question-${accessibleId}-${question.domId}-free-text`;
      const freeTextLabel = document.createElement("label");
      freeTextLabel.htmlFor = freeTextId;
      freeTextLabel.textContent = "Other answer";
      const textarea = document.createElement("textarea");
      textarea.id = freeTextId;
      textarea.name = `question-${accessibleId}-${question.domId}-free-text`;
      textarea.maxLength = MAX_FREE_TEXT;
      textarea.disabled = model.disabled;
      textarea.dataset.questionId = question.id;
      textarea.dataset.questionFreeText = "true";
      textarea.setAttribute("aria-label", `${question.header}: other answer`);
      const freeText = question.selected.find((value) => !question.options.some((option) => option.label === value));
      textarea.value = freeText || "";
      fieldset.append(freeTextLabel, textarea);
    }
    card.append(fieldset);
  }

  const actions = document.createElement("div");
  actions.className = "decision-actions";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.dataset.action = "answer-interaction";
  submit.textContent = "Submit answer";
  submit.disabled = model.submitDisabled;
  submit.setAttribute("aria-disabled", String(model.submitDisabled));
  actions.append(submit);
  card.append(actions);
  container.append(card);
}

/** Read bounded native form values into the Bridge `answers` payload shape. */
export function collectUserInputAnswers(container, model) {
  const answers = [];
  for (const question of model.questions) {
    const selected = [...container.querySelectorAll("[data-question-id][data-answer-value]:checked")]
      .filter((control) => control.dataset.questionId === question.id)
      .map((control) => plainText(control.value, MAX_FREE_TEXT));
    const freeText = [...container.querySelectorAll('[data-question-id][data-question-free-text="true"]')]
      .find((control) => control.dataset.questionId === question.id);
    let values = selected;
    if (freeText) {
      const value = plainText(freeText.value, MAX_FREE_TEXT);
      if (value) values = question.multiple ? [...selected, value] : [value];
    }
    const unique = [...new Set(values)].slice(0, question.multiple ? 32 : 1);
    if (unique.length) answers.push({ question_id: question.id, values: unique });
  }
  return answers;
}
