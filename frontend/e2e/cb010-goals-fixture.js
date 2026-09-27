/** Browser-only HA adapter: the real panel controls still own every interaction. */
export function installGoalFixture(panel) {
  panel._stopPolling(); panel._stopSystemEventSubscription(); panel._stopEventSubscription();
  panel._hass = { ...panel._hass, user: { id: "goal-owner-one" } };
  panel._loadPreferences();
  panel._selectedThreadId = "goal-chat";
  panel._activeThread = { ...panel._activeThread, thread_id: "goal-chat", project_id: "goal-project",
    title: "Manual goals", status: "idle", mode: "edit", attachments: [], archived_at: null, schedule_eligible: true };
  panel._projects = [{ project_id: "goal-project", name: "Goal project", root_path: "projects/goals", kind: "project", archived_at: null }];
  panel._threads = [panel._activeThread];
  panel._config.capabilities = [...(panel._config.capabilities || []), "durable_goals_v1"];
  const original = panel._callWS.bind(panel);
  const states = new Map();
  const receipts = new Map();
  const fixture = { requests: [], delayed: [], holdNext: null };
  const stateFor = (owner) => {
    if (!states.has(owner)) states.set(owner, { revision: 0, goal: null, history: [], continuation: "manual_turns_only" });
    return states.get(owner);
  };
  fixture.changeOwner = (owner) => {
    panel._hass = { ...panel._hass, user: { id: owner } };
    panel._loadPreferences();
    panel._renderGoals(panel._activeThread);
  };
  fixture.release = () => {
    const delayed = fixture.delayed.splice(0);
    for (const { resolve, value } of delayed) resolve(value);
  };
  panel._callWS = async (command, payload) => {
    const owner = panel._hass.user.id;
    fixture.requests.push({ command, payload: structuredClone(payload), owner });
    if (command === "send_prompt") throw new Error("Goal controls must never send a prompt");
    if (!["get_goal", "goal_action"].includes(command)) return original(command, payload);
    const state = stateFor(owner);
    if (command === "goal_action") {
      const key = `${owner}:${payload.client_request_id}`;
      if (!receipts.has(key)) {
        if (payload.expected_revision !== state.revision) throw new Error("revision conflict");
        const action = payload.action;
        if (action === "create") {
          if (state.goal) state.history.push(structuredClone(state.goal));
          state.goal = { objective: payload.objective, completion_criteria: payload.completion_criteria,
            progress: payload.progress || "", status: "paused" };
        } else if (action === "progress") state.goal.progress = payload.progress;
        else if (action === "edit") Object.assign(state.goal, { objective: payload.objective, completion_criteria: payload.completion_criteria });
        else if (action === "complete") {
          if (payload.completion_confirmed !== true) throw new Error("confirmation required");
          state.goal.status = "completed";
        } else state.goal.status = { resume: "active", pause: "paused", cancel: "cancelled" }[action];
        state.revision += 1;
        receipts.set(key, true);
      }
    }
    const value = structuredClone(state);
    if (fixture.holdNext === command) {
      fixture.holdNext = null;
      return new Promise((resolve) => fixture.delayed.push({ resolve, value }));
    }
    return value;
  };
  panel._refreshActiveThread = async () => {};
  window.goalFixture = fixture;
  panel._render();
}
