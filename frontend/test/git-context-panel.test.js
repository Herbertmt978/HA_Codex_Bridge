/** @vitest-environment jsdom */
import { beforeEach, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function panel() {
  const element = document.createElement("codex-bridge-panel");
  document.body.append(element);
  element._selectedThreadId = "a";
  element._config = { capabilities: ["git_context_v1"] };
  element._callWS = vi.fn();
  return element;
}
const context = (branch) => ({ repository: true, workspace_path: "workspaces/a", repository_path: ".", branch, dirty: false, files: [] });
beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

it("forced refresh supersedes a pending same-chat request", async () => {
  const p = panel(); const old = deferred(); const fresh = deferred();
  p._callWS.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
  const first = p._loadGitContext(); const second = p._loadGitContext(true);
  fresh.resolve(context("new")); await second;
  old.resolve(context("old")); await first;
  expect(p._gitContext.branch).toBe("new");
  expect(p._gitContextLoading).toBeNull();
});

it("A to B to A rejects the old A selection and refreshes without waiting for freshness", async () => {
  const p = panel(); const old = deferred();
  p._callWS.mockReturnValueOnce(old.promise).mockResolvedValueOnce(context("fresh"));
  const first = p._loadGitContext();
  p._setSelectedThreadId("b"); p._setSelectedThreadId("a");
  await p._loadGitContext(); old.resolve(context("old")); await first;
  expect(p._gitContext.branch).toBe("fresh");
  expect(p._callWS).toHaveBeenCalledTimes(2);
});

it("base changes bypass freshness and late results cannot show a former base", async () => {
  const p = panel(); const old = deferred();
  p.shadowRoot.getElementById("git-review-scope").value = "branch";
  const ref = p.shadowRoot.getElementById("git-review-ref"); ref.value = "main";
  p._callWS.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...context("new"), base_name: "develop", base_available: true });
  const first = p._loadGitContext(); ref.value = "develop";
  p._handleChange({ target: ref });
  await Promise.resolve(); old.resolve({ ...context("old"), base_name: "main" }); await first;
  expect(p._callWS).toHaveBeenLastCalledWith("git_context", { thread_id: "a", base_ref: "develop" });
  expect(p._gitContext.base_name).toBe("develop");
});

it("shows safe file text and clears it for a non-Git workspace", () => {
  const p = panel(); p._gitContextThreadId = "a"; p._gitContextSelectionEpoch = p._threadSelectionEpoch; p._gitContextBase = "";
  p._gitContext = { ...context("main"), dirty: true, changed_files: 1, files: [{ path: "<img src=x>.txt", status: " M" }] };
  p._renderGitContext();
  const details = p.shadowRoot.getElementById("git-context-files");
  expect(details.textContent).toContain("Working tree: Modified");
  expect(details.querySelector("img")).toBeNull();
  expect(p.shadowRoot.getElementById("git-context-status").textContent).toContain("Workspace: workspaces/a · Repository: .");
  p._gitContext = { repository: false }; p._renderGitContext();
  expect(details.hidden).toBe(true); expect(details.querySelectorAll("li")).toHaveLength(0);
  expect(p.shadowRoot.getElementById("git-context-status").textContent).toContain("not a Git repository");
});

it("capability hiding and failed request allow explicit retry", async () => {
  const p = panel(); p._config.capabilities = []; p._renderGitContext();
  expect(p.shadowRoot.getElementById("git-context").hidden).toBe(true);
  await p._loadGitContext(); expect(p._callWS).not.toHaveBeenCalled();
  p._config.capabilities = ["git_context_v1"];
  p._callWS.mockRejectedValueOnce(new Error("unavailable")).mockResolvedValueOnce(context("recovered"));
  await p._loadGitContext(); expect(p._gitContext.unavailable).toBe(true);
  await p._loadGitContext(true); expect(p._gitContext.branch).toBe("recovered");
});
