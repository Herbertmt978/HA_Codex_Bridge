const PLAN_NAMES = new Map([
  ["free", "Free"],
  ["go", "Go"],
  ["plus", "Plus"],
  ["pro", "Pro"],
  ["prolite", "Pro"],
  ["team", "Team"],
  ["self_serve_business_usage_based", "Business"],
  ["business", "Business"],
  ["enterprise_cbp_usage_based", "Enterprise"],
  ["enterprise", "Enterprise"],
  ["edu", "Education"],
]);
const TERMINAL_STATES = new Set(["ok", "signed_out", "cancelled", "login_failed", "expired"]);
const ACTIVE_STATES = new Set(["login_starting", "login_running"]);
const BUSY_STATES = new Set(["login_canceling", "login_completing", "logout_running"]);

export function normalizePlanType(value) {
  return typeof value === "string" ? PLAN_NAMES.get(value.trim().toLowerCase()) || "Unknown" : "Unknown";
}

/** Return only presentation-safe account state; auth URLs and credentials never leave this boundary. */
export function getAuthViewModel(auth = {}) {
  const state = typeof auth.state === "string" ? auth.state : "unknown";
  const loginActive = ACTIVE_STATES.has(state);
  const busy = BUSY_STATES.has(state);
  const code = loginActive && typeof auth.user_code === "string" && auth.user_code.trim() ? auth.user_code.trim() : null;
  const authMode = auth.auth_mode ?? auth.account?.auth_mode ?? null;
  const unsupported = state === "unsupported" || (typeof authMode === "string" && authMode !== "chatgpt");
  const signedIn = !unsupported && state === "ok" && !auth.auth_required;
  const actions = [];
  if (loginActive) {
    actions.push({ id: "open-chatgpt", label: "Open ChatGPT" });
    if (code) actions.push({ id: "copy-auth-code", label: "Copy code" });
    actions.push({ id: "cancel-sign-in", label: "Cancel" });
  } else if (busy) {
    // The coordinator owns the active operation; a second mutation would only conflict.
  } else if (unsupported) {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Sign out now" : "Sign out",
    });
  } else if (state === "logout_failed") {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Try sign-out again" : "Sign out",
    });
  } else if (!signedIn) {
    actions.push({ id: "start-auth-login", label: "Sign in with ChatGPT", primary: true });
  } else {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Sign out now" : "Sign out",
    });
  }
  let message = "Sign in with the ChatGPT account that includes your Codex access.";
  if (unsupported) message = "Only ChatGPT account sign-in is supported.";
  else if (signedIn) message = "Codex is connected through your ChatGPT account.";
  else if (state === "login_failed") message = "Sign-in did not complete. Try again from Home Assistant.";
  else if (state === "logout_failed") message = "Sign-out did not complete. Try again from Home Assistant.";
  else if (state === "expired") message = "Your ChatGPT sign-in expired. Sign in again to continue.";
  else if (state === "login_canceling") message = "Cancelling ChatGPT sign-in. Please wait.";
  else if (state === "login_completing") message = "Finishing ChatGPT sign-in. Please wait.";
  else if (state === "logout_running") message = "Signing out of ChatGPT. Please wait.";
  else if (loginActive) message = "Enter this one-time code in the ChatGPT sign-in page.";
  return {
    state: signedIn ? "signed_in" : unsupported ? "unsupported" : state,
    signedIn,
    busy,
    plan: normalizePlanType(auth.account?.plan_type ?? auth.plan_type),
    code: TERMINAL_STATES.has(state) ? null : code,
    canCopyCode: Boolean(code),
    canOpen: loginActive,
    message,
    guidance: loginActive ? "Continue in ChatGPT on your phone or another signed-in device." : "",
    actions,
  };
}

export function renderAuth(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = "auth-card";
  const title = document.createElement("strong");
  title.textContent = model.signedIn ? "ChatGPT connected" : "ChatGPT sign-in";
  card.append(title);
  if (model.plan !== "Unknown") {
    const plan = document.createElement("span");
    plan.className = "auth-plan";
    plan.textContent = `${model.plan} plan`;
    card.append(plan);
  }
  if (model.message) {
    const message = document.createElement("p");
    message.textContent = model.message;
    card.append(message);
  }
  if (model.code) {
    const code = document.createElement("code");
    code.className = "auth-code";
    code.textContent = model.code;
    card.append(code);
  }
  if (model.guidance) {
    const guidance = document.createElement("p");
    guidance.textContent = model.guidance;
    card.append(guidance);
  }
  const actions = document.createElement("div");
  actions.className = "auth-actions";
  for (const action of model.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = action.id;
    button.className = action.primary ? "primary" : "";
    button.textContent = action.label;
    actions.append(button);
  }
  card.append(actions);
  container.append(card);
}
