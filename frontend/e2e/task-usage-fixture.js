/** Synthetic HA responses around the actual source-bundled panel, never a runtime. */
export async function installTaskUsageFixture(element, { theme = "light", owner = "cb060-fixture" } = {}) {
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const calls = [];
  const state = { failHistory: false, supported: true };
  const baseRow = {
    thread_id: "thr_vba_2", project_id: "prj_vba", started_at: "2026-09-27T10:00:00Z",
    status: "completed", duration_seconds: 20, budget: null,
    budget_stop_requested_at: null, budget_stop_state: null,
  };
  const rows = [
    { ...baseRow, run_id: "fixture-unknown", reported_tokens: null, coverage: "not_reported", account_label: "Saved work account", account_group: 1 },
    { ...baseRow, run_id: "fixture-partial", reported_tokens: 24, coverage: "partial", account_label: "Personal account", account_group: 2 },
    { ...baseRow, run_id: "fixture-zero", reported_tokens: 0, coverage: "reported", account_label: "Saved work account", account_group: 1 },
    {
      ...baseRow, run_id: "fixture-stop", reported_tokens: 128, coverage: "reported", duration_seconds: null,
      account_label: '<img src=x onerror="window.cb060Unsafe=true">', account_group: 3,
      status: "running", budget: { max_duration_seconds: 60 },
      budget_stop_requested_at: "2026-09-27T10:01:00Z", budget_stop_state: "unconfirmed",
    },
    {
      ...baseRow, run_id: "fixture-project-peer", thread_id: "thr_vba_1", reported_tokens: 77,
      coverage: "partial", account_label: `Long saved account label ${"W".repeat(120)}`, account_group: 4,
    },
  ];
  const original = element.hass.connection.sendMessagePromise.bind(element.hass.connection);
  element.hass = {
    ...element.hass, user: { id: owner }, config: { ...element.hass.config, time_zone: "Europe/London" },
    connection: { ...element.hass.connection, sendMessagePromise: async (payload) => {
      calls.push(clone(payload));
      if (payload.type === "codex_bridge/usage_history") {
        if (state.failHistory) throw new Error("Synthetic history unavailable");
        const items = rows.filter((row) => payload.project_id ? row.project_id === payload.project_id : row.thread_id === payload.thread_id);
        return {
          items: clone(items), known_reported_tokens: items.reduce((sum, row) => sum + (row.reported_tokens || 0), 0),
          coverage: "partial", retention_limit: 1024, evicted_runs: 2,
          billing: "not_reported", token_limit_supported: false, max_duration_seconds: 3600,
        };
      }
      const response = await original(payload);
      if (payload.type === "codex_bridge/get_config") return {
        ...response, capabilities: state.supported ? ["usage_history_v1", "elapsed_time_limit_v1"] : [],
      };
      return response;
    } },
  };
  element._stopSystemEventSubscription();
  element._preferences = { ...element._preferences, theme, motion: "reduced" };
  element._applyPreferences();
  element._config = { ...element._config, capabilities: ["usage_history_v1", "elapsed_time_limit_v1"] };
  window.__codexHarness.updateThread("thr_vba_2", { status: "idle", schedule_eligible: true });
  window.__codexHarness.emitThreadEvent("thr_vba_2", "message.completed", {
    run_id: "fixture-retained", role: "assistant", text: "Partial results retained after the elapsed-time stop. Saved progress remains available for deliberate review.",
  });
  window.__codexHarness.emitThreadEvent("thr_vba_2", "run.cancelled", {
    run_id: "fixture-retained", message: "Elapsed-time stop requested. Partial results are retained.",
  });
  await element._selectThread("thr_vba_2");
  element._stopPolling();
  element._stopEventSubscription();
  element._render(true);
  window.__cb060UsageFixture = { calls, rows, state };
}
