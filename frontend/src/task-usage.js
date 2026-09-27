/** Reported usage is separate from context occupancy, allowances and billing. */
const number = new Intl.NumberFormat("en-GB");

export function usageLabel(item) {
  const count = item?.reported_tokens;
  if (!Number.isSafeInteger(count) || count < 0) return "Usage not reported";
  return `${number.format(count)} reported tokens${item.coverage === "reported" ? "" : " · partial coverage"}`;
}

export function durationLimit(value) {
  if (!["string", "number"].includes(typeof value)) return null;
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) && seconds >= 1 && seconds <= 86400 ? seconds : null;
}

export function renderUsageHistory(container, data, { openChat, timeZone } = {}) {
  const doc = container.ownerDocument;
  const element = (tag, text, className = "") => {
    const node = doc.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  };
  container.replaceChildren();
  const items = Array.isArray(data?.items) ? data.items : [];
  container.append(element("p", "Runtime-reported tokens are best-effort observations. Context occupancy, account allowances and billing are separate. Token limits are unavailable.", "usage-note"));
  container.append(element("p", `Retains up to ${number.format(data?.retention_limit || 1024)} runs. ${data?.evicted_runs ? "Earlier runs were removed by retention." : "No earlier usage is inferred."}`, "row-meta"));
  if (!items.length) {
    container.append(element("p", "No reported run history for this selection yet.", "empty-state"));
    return;
  }
  const table = doc.createElement("table");
  table.className = "task-usage-table";
  const caption = element("caption", "Task usage by execution account");
  table.append(caption);
  const head = doc.createElement("thead");
  const headings = doc.createElement("tr");
  for (const label of ["Started", "Account", "Reported usage", "Elapsed time", "Outcome"]) {
    const th = element("th", label); th.scope = "col"; headings.append(th);
  }
  head.append(headings); table.append(head);
  const body = doc.createElement("tbody");
  for (const item of items.slice(0, 1024)) {
    const row = doc.createElement("tr");
    let date = "Time not reported";
    if (typeof item.started_at === "string" && Number.isFinite(Date.parse(item.started_at))) {
      try {
        date = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: timeZone || "UTC" }).format(new Date(item.started_at));
      } catch {
        date = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(item.started_at));
        date += " UTC";
      }
      if (!timeZone) date += " UTC";
    }
    const started = doc.createElement("td");
    if (openChat && typeof item.thread_id === "string") {
      const button = element("button", date, "composer-limits-button");
      button.type = "button";
      button.setAttribute("aria-label", `Open chat for run started ${date}`);
      button.addEventListener("click", () => openChat(item.thread_id));
      started.append(button);
    } else started.textContent = date;
    const label = item.account_label || "Account not reported";
    const account = Number.isSafeInteger(item.account_group) && !label.startsWith("Account ")
      ? `${label} · account ${item.account_group}` : label;
    row.append(started, element("td", account), element("td", usageLabel(item)));
    const duration = Number.isFinite(item.duration_seconds) && item.duration_seconds >= 0
      ? `${number.format(Math.round(item.duration_seconds))} s` : "In progress / not reported";
    row.append(element("td", duration));
    const outcome = item.budget_stop_state === "unconfirmed" ? "Stop unconfirmed — new work blocked until recovery"
      : item.budget_stop_state === "requested" ? "Elapsed-time stop requested"
        : item.budget_stop_state === "finished" ? "Elapsed-time stop · partial results retained" : item.status || "Outcome not reported";
    row.append(element("td", outcome)); body.append(row);
  }
  table.append(body);
  const scroll = doc.createElement("div");
  scroll.className = "task-usage-table-scroll";
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Reported usage table");
  scroll.append(table); container.append(scroll);
}
