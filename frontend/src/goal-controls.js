/** Server-owned goals apply only to deliberate new turns; no timer or prompt submission. */
export class GoalControls {
  constructor(call, requestId) {
    this.call = call;
    this.requestId = requestId || (() => globalThis.crypto.randomUUID());
    this.epoch = 0;
    this.view = null;
    this.busy = false;
    this.pending = null;
  }

  invalidate() {
    this.epoch += 1;
    this.view = null;
    this.busy = false;
    this.pending = null;
    this.creating = false;
    this.error = "";
    this.threadId = undefined;
    if (this.element) this.element.replaceChildren();
  }

  setContext(element, { threadId, ownerId = null, available, eligible = true }) {
    if (!element) return;
    const changed = this.threadId !== threadId || this.ownerId !== ownerId
      || this.available !== available || this.eligible !== eligible;
    const moved = this.element !== element;
    this.element = element;
    this.available = available;
    this.eligible = eligible;
    element.hidden = !threadId;
    if (changed) {
      this.ownerId = ownerId;
      this.threadId = threadId;
      this.epoch += 1;
      this.view = null;
      this.busy = false;
      this.pending = null;
      this.creating = false;
      this.error = "";
    }
    if (changed || moved) this.render();
    if (changed && threadId && available && eligible) void this.refresh();
  }

  async refresh() {
    if (!this.available || !this.eligible || !this.threadId || this.busy) return;
    const epoch = this.epoch;
    const threadId = this.threadId;
    this.busy = true;
    this.updateButtons();
    try {
      const view = await this.call("get_goal", { thread_id: threadId });
      if (epoch !== this.epoch) return;
      if (!view || !Number.isSafeInteger(view.revision) || view.continuation !== "manual_turns_only") {
        throw new Error("unsupported goal response");
      }
      this.view = view;
      this.pending = null;
      this.error = "";
      this.creating = false;
      this.render();
    } catch {
      if (epoch === this.epoch) this.showError("The goal could not be loaded. Refresh to try again.");
    } finally {
      if (epoch === this.epoch) {
        this.busy = false;
        this.updateButtons();
      }
    }
  }

  async act(action, retry = false) {
    if (this.busy || !this.view || !this.available || !this.eligible) return;
    const epoch = this.epoch;
    const payload = retry && this.pending ? this.pending : {
      thread_id: this.threadId, action, expected_revision: this.view.revision,
      client_request_id: this.requestId(),
    };
    if (!retry) {
      const value = (name) => this.element.querySelector(`[name="${name}"]`)?.value || "";
      if (action === "create" || action === "edit") {
        payload.objective = value("goal-objective");
        payload.completion_criteria = value("goal-criteria").split("\n").map((text) => text.trim()).filter(Boolean);
        if (!payload.objective.trim() || !payload.completion_criteria.length
            || payload.completion_criteria.length > 16 || payload.completion_criteria.some((text) => text.length > 1024)) {
          this.showError("Enter an objective and one to sixteen completion criteria, one per line (up to 1,024 characters each).");
          return;
        }
      }
      if (action === "create" || action === "progress") payload.progress = value("goal-progress");
      if (action === "complete") {
        payload.completion_confirmed = this.element.querySelector('[name="goal-confirm"]')?.checked === true;
        if (!payload.completion_confirmed) return;
      }
    }
    this.pending = payload;
    this.busy = true;
    this.updateButtons();
    try {
      const view = await this.call("goal_action", payload);
      if (epoch !== this.epoch) return;
      this.view = view;
      this.pending = null;
      this.error = "";
      this.creating = false;
      this.render();
    } catch {
      if (epoch === this.epoch) {
        this.showError("The change was not confirmed. Retry the same change, or refresh to review the current goal.");
      }
    } finally {
      if (epoch === this.epoch) {
        this.busy = false;
        this.updateButtons();
      }
    }
  }

  node(tag, text = "", className = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  button(parent, text, action, callback) {
    const button = this.node("button", text, "composer-limits-button");
    button.type = "button";
    button.dataset.goalAction = action;
    button.addEventListener("click", (event) => { event.stopPropagation(); callback(); });
    parent.append(button);
  }

  render() {
    if (!this.element || !this.threadId) return;
    const disclosure = this.node("details", "", "goal-disclosure");
    disclosure.open = this.element.querySelector("details")?.open || false;
    const goal = this.view?.goal;
    const status = { active: "applies to manual turns", paused: "paused", completed: "completed", cancelled: "cancelled" }[goal?.status];
    disclosure.append(this.node("summary", `Goal${status ? ` — ${status}` : " — manual continuation"}`));
    const description = this.node("p", "Resume applies this goal to new prompts you send; it starts no work. Pause or Cancel prevents future starts using it. A turn already started continues; use the existing Stop control to stop that turn.", "row-meta");
    description.id = "goal-manual-description";
    disclosure.append(description);
    const actions = this.node("div", "", "row-actions");
    if (!this.available || !this.eligible) {
      disclosure.append(this.node("p", this.eligible
        ? "Goals are unavailable in this App version. Update the paired App and Integration to use them."
        : "Goals are available only in ordinary, unarchived chats and projects.", "row-meta"));
      this.element.replaceChildren(disclosure);
      return;
    }
    this.button(actions, "Refresh goal", "refresh", () => void this.refresh());
    disclosure.append(actions);
    const error = this.node("p", this.error || (this.view ? "" : "Loading goal…"), "row-meta");
    error.dataset.goalStatus = "";
    error.setAttribute("role", "status");
    error.setAttribute("aria-live", "polite");
    disclosure.append(error);
    if (!this.view) {
      this.element.replaceChildren(disclosure);
      this.updateButtons();
      return;
    }
    const terminal = goal && ["completed", "cancelled"].includes(goal.status);
    const create = !goal || this.creating;
    const form = this.node("div", "", "goal-fields");
    for (const [name, title, maximum, value, readOnly] of [
      ["goal-objective", "Objective", 4096, create ? "" : goal.objective, !create && goal.status !== "paused"],
      ["goal-criteria", "Completion criteria — one per line", 16400, create ? "" : goal.completion_criteria.join("\n"), !create && goal.status !== "paused"],
      ["goal-progress", "Progress notes", 8192, create ? "" : goal.progress, !create && terminal],
    ]) {
      const label = this.node("label", title, "goal-field");
      const input = this.node("textarea");
      input.name = name;
      input.rows = name === "goal-criteria" ? 3 : 2;
      input.maxLength = maximum;
      input.value = value;
      input.readOnly = readOnly;
      input.setAttribute("aria-describedby", description.id);
      label.append(input);
      form.append(label);
    }
    disclosure.append(form);
    const controls = this.node("div", "", "row-actions");
    if (create) {
      this.button(controls, "Save paused goal", "create", () => void this.act("create"));
    } else if (terminal) {
      this.button(controls, "New goal", "new", () => {
        this.creating = true; this.render(); this.element.querySelector("textarea")?.focus();
      });
    } else {
      if (goal.status === "paused") this.button(controls, "Save objective and criteria", "edit", () => void this.act("edit"));
      this.button(controls, "Save progress", "progress", () => void this.act("progress"));
      this.button(controls, goal.status === "active" ? "Pause goal" : "Resume for manual turns", goal.status === "active" ? "pause" : "resume",
        () => void this.act(goal.status === "active" ? "pause" : "resume"));
      this.button(controls, "Cancel goal", "cancel", () => void this.act("cancel"));
      const label = this.node("label", "", "composer-utility");
      const confirm = this.node("input");
      confirm.type = "checkbox";
      confirm.name = "goal-confirm";
      confirm.addEventListener("change", () => this.updateButtons());
      label.append(confirm, this.node("span", "I confirm the completion criteria are met"));
      disclosure.append(label);
      this.button(controls, "Mark goal complete", "complete", () => void this.act("complete"));
    }
    this.button(controls, "Retry change", "retry", () => void this.act(null, true));
    disclosure.append(controls);
    if (this.view.history?.length) {
      const history = this.node("details");
      history.append(this.node("summary", "Previous finished goals"));
      for (const item of this.view.history) history.append(this.node("p", `${item.objective} (${item.status})`, "row-meta"));
      disclosure.append(history);
    }
    this.element.replaceChildren(disclosure);
    this.updateButtons();
  }

  updateButtons() {
    if (!this.element) return;
    for (const button of this.element.querySelectorAll("button[data-goal-action]")) {
      const action = button.dataset.goalAction;
      button.disabled = this.busy || (action === "complete" && !this.element.querySelector('[name="goal-confirm"]')?.checked);
      if (action === "retry") button.hidden = !this.pending || !this.error;
    }
    this.element.setAttribute("aria-busy", String(this.busy));
  }

  showError(message) {
    this.error = message;
    const status = this.element?.querySelector("[data-goal-status]");
    if (status) status.textContent = message;
    this.updateButtons();
  }
}

export const goalStyles = `
  #goal-controls { margin: 0 var(--space-4, 16px) var(--space-3, 12px); }
  .goal-fields { display: grid; gap: 12px; margin-block: 12px; }
  .goal-field { display: grid; gap: 6px; min-width: 0; }
  .goal-field textarea { width: 100%; box-sizing: border-box; resize: vertical; min-height: 48px; font: inherit; }
  #goal-controls .row-actions { flex-wrap: wrap; }
  #goal-controls summary { overflow-wrap: anywhere; cursor: pointer; }
`;
