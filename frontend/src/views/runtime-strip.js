function safeVersion(value) {
  return typeof value === "string" && /^[0-9][0-9A-Za-z.+-]{0,31}$/u.test(value) ? value : null;
}

function runtimeItem(label, ready, version) {
  return { label, state: ready ? "ready" : "attention", version: safeVersion(version) };
}

/** Project runtime health to a minimal, non-sensitive status strip. */
export function getRuntimeStripViewModel(status = {}) {
  const apiVersion = Number(status.api_version);
  const diagnostics = status.diagnostics || {};
  const appReady = status.app?.connected === true;
  const integrationReady = status.integration?.ready === true;
  const bridgeReady = apiVersion === 1 && status.bridge_ready !== false;
  const codexVersion = diagnostics.app_server_version || diagnostics.active_codex_version;
  const codexReady = codexVersion ? bridgeReady : status.codex_ready === true && bridgeReady;
  const legacy = apiVersion === 0 || String(status.connection_type || "").startsWith("external");
  const items = [
    runtimeItem("App", appReady, status.app?.version || diagnostics.app_version),
    runtimeItem("Integration", integrationReady, status.integration?.version),
    runtimeItem("Bridge", bridgeReady, diagnostics.bridge_version),
    runtimeItem("Codex", codexReady, codexVersion),
  ];
  return {
    items,
    healthy: items.every((item) => item.state === "ready"),
    notice: legacy ? "This older connection is capability-limited and supported for existing setups only." : "",
  };
}

export function renderRuntimeStrip(container, model) {
  container.replaceChildren();
  container.hidden = Boolean(model.healthy && !model.notice);
  const strip = document.createElement("div");
  strip.className = "runtime-strip";
  for (const item of model.items) {
    const status = document.createElement("span");
    status.className = `runtime-item ${item.state}`;
    status.textContent = item.version ? `${item.label} ${item.version}` : item.label;
    strip.append(status);
  }
  container.append(strip);
  if (model.notice) {
    const notice = document.createElement("p");
    notice.className = "runtime-notice";
    notice.textContent = model.notice;
    container.append(notice);
  }
}
