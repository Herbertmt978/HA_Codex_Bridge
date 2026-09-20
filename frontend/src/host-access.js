export const HOST_MODE = "haos-full-access";
export const HOST_LABEL = "Full access · Home Assistant OS";
export const HOST_INSTALLATION_URL = "https://github.com/Herbertmt978/HA_Codex_Bridge/blob/main/codex_host_access_app/DOCS.md";

const node = (doc, tag, value, className = "") => {
  const result = doc.createElement(tag);
  result.textContent = value;
  if (className) result.className = className;
  return result;
};

export function renderHostAccessDialog(doc, state) {
  const dialog = node(doc, "section", "", "confirmation-dialog host-access-dialog");
  dialog.id = "host-access-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "host-access-title");
  dialog.setAttribute("aria-describedby", "host-access-summary");
  dialog.tabIndex = -1;
  const title = node(doc, "h2", "Allow Codex full access to Home Assistant OS?");
  title.id = "host-access-title";
  const ready = state.status?.state === "ready";
  const identity = state.status?.disclosure;
  const summary = node(doc, "p", ready
    ? `Codex will run commands as root on ${identity?.hostname || "this machine"} (Home Assistant OS ${identity?.os_version || ""}).`
    : "This optional mode gives Codex root access to the machine running Home Assistant OS.");
  summary.id = "host-access-summary";
  dialog.append(title, summary);
  if (state.loading) {
    const loading = node(doc, "p", "Checking the Host Access App…");
    loading.setAttribute("role", "status"); dialog.append(loading);
  }
  for (const warning of state.status?.warnings || []) {
    const section = doc.createElement("section");
    section.append(node(doc, "h3", warning.title), node(doc, "p", warning.description));
    dialog.append(section);
  }
  if (!state.loading && !ready) {
    dialog.append(node(doc, "p", state.status?.state === "unavailable"
      ? "The Host Access App is unavailable. Start it and check its log, then retry. Access remains disabled."
      : "Install the separate Codex Host Access App to use this mode. Normal Codex Bridge permissions stay unchanged."));
    const instructions = node(doc, "a", "Host Access installation instructions");
    instructions.href = HOST_INSTALLATION_URL;
    instructions.target = "_blank"; instructions.rel = "noopener noreferrer";
    dialog.append(instructions);
  }
  const checkbox = (id, label, checked) => {
    const wrap = doc.createElement("label"); wrap.className = "host-access-acknowledgement";
    const input = doc.createElement("input"); input.type = "checkbox"; input.id = id;
    input.checked = checked === true; input.disabled = state.busy === true;
    wrap.append(input, node(doc, "span", label)); dialog.append(wrap);
  };
  if (ready) {
    checkbox("host-access-acknowledged", identity?.acknowledgement || "I understand that Codex will have root access to this Home Assistant OS machine, its files, credentials and network.", state.acknowledged);
    if (state.context === "schedule") {
      checkbox("host-access-unattended", identity?.scheduled_acknowledgement || "I allow this scheduled task to use host access automatically while I am absent.", state.unattended);
    }
  }
  if (state.error) { const error = node(doc, "p", state.error, "desktop-error"); error.setAttribute("role", "alert"); dialog.append(error); }
  const actions = node(doc, "div", "", "confirmation-actions");
  const button = (label, action) => { const result = node(doc, "button", label, "panel-button"); result.type = "button"; result.dataset.action = action; return result; };
  const cancel = button("Cancel", "cancel-host-access"); cancel.id = "cancel-host-access"; cancel.disabled = !!state.busy;
  actions.append(cancel);
  if (ready) {
    const enable = button(state.busy ? "Enabling…" : state.status?.enabled ? "Use host access" : "Enable host access", "confirm-host-access");
    enable.id = "confirm-host-access";
    enable.classList.add("panel-button-primary");
    enable.disabled = !!state.busy || !state.acknowledged || (state.context === "schedule" && !state.unattended);
    actions.append(enable);
  } else if (!state.loading) {
    actions.append(button("Check again", "retry-host-access"));
  }
  dialog.append(actions);
  return dialog;
}
