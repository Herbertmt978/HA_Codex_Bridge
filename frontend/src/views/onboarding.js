const STAGE_DEFINITIONS = [
  ["App connected", "Connect the Home Assistant App.", (state) => state.appConnected],
  ["Integration confirmed", "Confirm the integration in Home Assistant.", (state) => state.integrationReady],
  ["Bridge ready", "Wait for the bridge to become ready.", (state) => state.bridgeReady],
  ["Codex ready", "Sign in, create a workspace, and start your first chat.", (state) => state.signedIn && state.workspaceReady && state.threadCount > 0],
];

/** Build the safe, HA-first onboarding state without carrying connection details. */
export function getOnboardingViewModel(state = {}) {
  const normalized = {
    appConnected: Boolean(state.appConnected),
    integrationReady: Boolean(state.integrationReady),
    bridgeReady: Boolean(state.bridgeReady),
    signedIn: Boolean(state.signedIn),
    workspaceReady: Boolean(state.workspaceReady),
    threadCount: Number.isFinite(state.threadCount) ? state.threadCount : 0,
  };
  const stages = STAGE_DEFINITIONS.map(([label, note, complete], index) => ({
    id: ["app", "integration", "bridge", "codex"][index],
    label,
    note,
    complete: complete(normalized),
    action: index === 0 && !normalized.appConnected ? "retry-app" : null,
  }));
  return { stages, complete: stages.every((stage) => stage.complete) };
}

/** Render onboarding using only nodes and text content. */
export function renderOnboarding(container, model) {
  container.replaceChildren();
  const list = document.createElement("ol");
  list.className = "onboarding-checklist";
  for (const stage of model.stages) {
    const item = document.createElement("li");
    item.className = `onboarding-stage ${stage.complete ? "complete" : "pending"}`;
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = stage.label;
    const note = document.createElement("span");
    note.textContent = stage.complete ? "Complete" : stage.note;
    copy.append(title, note);
    item.append(copy);
    if (stage.action) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.dataset.action = stage.action;
      retry.textContent = "Retry";
      item.append(retry);
    }
    list.append(item);
  }
  container.append(list);
}
