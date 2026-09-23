const MAX_FIELDS = 16;

function safeText(value, limit) {
  return typeof value === "string" ? value.slice(0, limit) : "";
}

function safeUrl(value, expectedHost) {
  if (typeof value !== "string" || value.length > 8192) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && parsed.hostname === expectedHost && !parsed.username && !parsed.password
      ? parsed.href : null;
  } catch {
    return null;
  }
}

export function renderMcpElicitation(container, interaction, { pending = false, draft = {} } = {}) {
  container.replaceChildren();
  const display = interaction?.display || {};
  const card = document.createElement("section");
  card.className = "approval-card mcp-elicitation-card";
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "false");
  card.tabIndex = -1;
  const safeId = /^[A-Za-z0-9_.:-]{1,128}$/u.test(interaction?.interaction_id)
    ? interaction.interaction_id : "pending";
  const heading = document.createElement("h3");
  heading.id = `mcp-${safeId}-title`;
  heading.textContent = safeText(display.title, 160) || "MCP request";
  const server = document.createElement("p");
  server.className = "decision-label";
  server.textContent = `Server: ${safeText(display.mcp_server, 128)}`;
  const purpose = document.createElement("p");
  purpose.id = `mcp-${safeId}-purpose`;
  purpose.textContent = safeText(display.summary, 512);
  card.setAttribute("aria-labelledby", heading.id);
  card.setAttribute("aria-describedby", purpose.id);
  const notice = document.createElement("p");
  notice.className = "decision-status";
  notice.textContent = "Review this request. Do not enter passwords, tokens or API keys here.";
  card.append(heading, server, purpose, notice);

  if (interaction?.kind === "mcp_url") {
    const host = safeText(display.mcp_url_host, 253);
    const destination = document.createElement("p");
    destination.textContent = `Destination: ${host}`;
    card.append(destination);
    const url = safeUrl(interaction.authorization_url, host);
    if (url) {
      const link = document.createElement("a");
      link.className = "mcp-open-link";
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.referrerPolicy = "no-referrer";
      link.textContent = `Open ${host} to continue`;
      card.append(link);
    }
  } else {
    const form = document.createElement("div");
    form.className = "mcp-form-fields";
    const fields = Array.isArray(display.mcp_fields) ? display.mcp_fields.slice(0, MAX_FIELDS) : [];
    for (const field of fields) {
      const row = document.createElement("div");
      row.className = "mcp-form-field";
      row.dataset.mcpField = field.name;
      row.dataset.mcpKind = field.kind;
      const label = document.createElement("label");
      label.textContent = `${safeText(field.label, 160)}${field.required ? " *" : ""}`;
      const saved = draft[field.name];
      let control;
      if (field.kind === "multi_select") {
        control = document.createElement("fieldset");
        const legend = document.createElement("legend");
        legend.textContent = label.textContent;
        control.append(legend);
        for (const [index, option] of (field.options || []).entries()) {
          const choice = document.createElement("label");
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = option;
          checkbox.checked = Array.isArray(saved) && saved.includes(option);
          checkbox.disabled = pending;
          choice.append(checkbox, document.createTextNode(safeText(field.option_labels?.[index] || option, 160)));
          control.append(choice);
        }
      } else if (field.kind === "select" || field.kind === "boolean") {
        control = document.createElement("select");
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "Select an answer";
        control.append(empty);
        const choices = field.kind === "boolean" ? [["true", "Yes"], ["false", "No"]]
          : (field.options || []).map((option, index) => [option, field.option_labels?.[index] || option]);
        for (const [value, text] of choices) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = safeText(text, 160);
          control.append(option);
        }
        control.value = saved === undefined ? "" : String(saved);
        control.required = Boolean(field.required);
      } else {
        control = document.createElement("input");
        control.type = ({ email: "email", uri: "url", date: "date", "date-time": "datetime-local" })[field.format]
          || (field.kind === "integer" || field.kind === "number" ? "number" : "text");
        control.value = saved === undefined ? "" : String(saved);
        control.required = Boolean(field.required);
        if (field.kind === "integer") control.step = "1";
        if (field.kind === "number") control.step = "any";
        if (Number.isInteger(field.min_length)) control.minLength = field.min_length;
        if (Number.isInteger(field.max_length)) control.maxLength = field.max_length;
        if (typeof field.minimum === "number") control.min = field.minimum;
        if (typeof field.maximum === "number") control.max = field.maximum;
      }
      control.disabled = pending;
      if (field.kind !== "multi_select") {
        label.append(control);
        row.append(label);
      } else {
        row.append(control);
      }
      if (field.description) {
        const description = document.createElement("p");
        description.textContent = safeText(field.description, 512);
        row.append(description);
      }
      form.append(row);
    }
    card.append(form);
  }

  const actions = document.createElement("div");
  actions.className = "decision-actions";
  const actionList = interaction?.kind === "mcp_url"
    ? [["accept", "I've completed this"], ["decline", "Decline"], ["cancel", "Cancel"]]
    : [["answer", "Send answer"], ["decline", "Decline"], ["cancel", "Cancel"]];
  for (const [action, label] of actionList) {
    if (!interaction?.allowed_actions?.includes(action)) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = action === "answer" ? "answer-mcp-form" : `${action}-interaction`;
    if (action !== "answer") button.dataset.decision = action;
    button.textContent = label;
    button.disabled = pending || interaction.status !== "pending" || (action === "accept" && !safeUrl(interaction.authorization_url, display.mcp_url_host));
    actions.append(button);
  }
  card.append(actions);
  container.append(card);
}

export function collectMcpDraft(container, interaction) {
  const draft = {};
  for (const field of interaction?.display?.mcp_fields || []) {
    const row = [...container.querySelectorAll("[data-mcp-field]")].find((item) => item.dataset.mcpField === field.name);
    if (!row) continue;
    draft[field.name] = field.kind === "multi_select"
      ? [...row.querySelectorAll("input:checked")].map((input) => input.value)
      : row.querySelector("input, select")?.value ?? "";
  }
  return draft;
}

export function collectMcpContent(container, interaction) {
  const draft = collectMcpDraft(container, interaction);
  const content = {};
  for (const field of interaction?.display?.mcp_fields || []) {
    const value = draft[field.name];
    const row = [...container.querySelectorAll("[data-mcp-field]")].find((item) => item.dataset.mcpField === field.name);
    const control = row?.querySelector("input, select");
    if (field.kind !== "multi_select" && control && !control.checkValidity()) {
      control.reportValidity();
      return null;
    }
    if (value === "" || (Array.isArray(value) && !value.length && !field.required)) {
      if (field.required) return null;
      continue;
    }
    if (field.kind === "boolean") content[field.name] = value === "true";
    else if (field.kind === "number" || field.kind === "integer") {
      const number = Number(value);
      if (!Number.isFinite(number) || (field.kind === "integer" && !Number.isInteger(number))) return null;
      content[field.name] = number;
    } else if (field.format === "date-time") {
      const timestamp = new Date(value);
      if (!Number.isFinite(timestamp.valueOf())) return null;
      content[field.name] = timestamp.toISOString();
    } else content[field.name] = value;
  }
  return content;
}
