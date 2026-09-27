/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "../src/codex-bridge-panel.js";

function panelFixture() {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._selectedThreadId = "compact-chat";
  panel._activeThread = { thread_id:"compact-chat", title:"Compact", status:"idle", mode:"edit", attachments:[], schedule_eligible:true };
  panel._config = {capabilities:["plan_mode_v1","web_search_v1","git_context_v1","conversation_search_v1"]};
  panel._status = {auth:{state:"ok",auth_required:false},account:{available:true}};
  panel._callWS = vi.fn(async () => ({}));
  panel._renderComposerState(panel._activeThread);
  return panel;
}

describe("compact composer presentation", () => {
  beforeEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
  it("moves existing controls into closed surfaces without clearing drafts or choices", () => {
    const p = panelFixture(), root=p.shadowRoot, ui=p._compactComposer;
    const input=root.getElementById("prompt-input"), mode=root.getElementById("collaboration-mode");
    input.value="Unsent draft"; mode.value="plan";
    ui.open("options",ui.options);
    expect(root.getElementById("compact-surface-options").contains(mode)).toBe(true);
    ui.close(true);
    expect(mode.value).toBe("plan"); expect(input.value).toBe("Unsent draft");
    expect(root.getElementById("compact-surface-options").hidden).toBe(true);
    expect(root.querySelector(".main-pane > #goal-controls")).toBeNull();
    expect(root.querySelector(".main-pane > #conversation-search")).toBeNull();
    expect(root.getElementById("compact-surface-goal").contains(root.getElementById("goal-controls"))).toBe(true);
    expect(p._callWS.mock.calls.some(([command])=>command==="send_prompt")).toBe(false);
  });
  it("keeps search state on close, hides its inactive surface and restores focus", () => {
    const p=panelFixture(), ui=p._compactComposer, trigger=p.shadowRoot.getElementById("chat-menu-button");
    trigger.disabled=false;
    p._conversationSearchState={query:"needle",results:[],total:0,index:-1,pageNumber:0};
    ui.open("search",trigger);
    expect(p.shadowRoot.getElementById("conversation-search").hidden).toBe(false);
    ui.close(true);
    expect(p._conversationSearchState.query).toBe("needle");
    expect(p.shadowRoot.getElementById("conversation-search").hidden).toBe(true);
    expect(p.shadowRoot.activeElement).toBe(trigger);
  });
  it("summarises non-default choices and stale context without taking send ownership", () => {
    const p=panelFixture(), ui=p._compactComposer;
    p._collaborationMode="plan";
    p.shadowRoot.getElementById("elapsed-time-limit").value="300";
    p.shadowRoot.getElementById("web-search-mode").value="disabled";
    p._workspaceContext.setItems("compact-chat",[{path:"example.js",stale:true}]);
    ui.sync();
    expect(ui.options.getAttribute("aria-label")).toContain("Plan, 5 minutes, Search Off");
    expect(ui.context.hidden).toBe(false);
    expect(ui.context.getAttribute("aria-label")).toContain("changed context requires review");
    ui.open("files",ui.context); ui.close();
    expect(p._workspaceContext.current()[0].stale).toBe(true);
    expect(p.shadowRoot.getElementById("elapsed-time-limit").value).toBe("300");
  });
  it("rejects unsupported surfaces and closes an open surface when capabilities disappear", () => {
    const p=panelFixture(), ui=p._compactComposer, root=p.shadowRoot, trigger=root.getElementById("chat-menu-button");
    root.getElementById("prompt-input").value="Preserved draft";
    ui.open("search",trigger);
    expect(ui.openPage).toBe("search");
    p._config.capabilities=[]; ui.sync();
    expect(ui.openPage).toBeNull();
    expect(root.getElementById("compact-surface-search").hidden).toBe(true);
    ui.open("search",trigger); ui.open("review",trigger);
    expect(ui.openPage).toBeNull();
    expect(root.getElementById("prompt-input").value).toBe("Preserved draft");
    expect(p._callWS.mock.calls.some(([command])=>["git_context","git_review","conversation_search","send_prompt"].includes(command))).toBe(false);
    p._config.capabilities=["git_review_v1"];
    expect(ui.canOpen("review")).toBe(true);
    expect(ui.canOpen("search")).toBe(false);
  });
  it("closes a surface when the selected chat changes without carrying context over", () => {
    const p=panelFixture(), ui=p._compactComposer;
    ui.sync(); ui.open("options",ui.options);
    p._selectedThreadId="other-chat"; ui.sync();
    expect(ui.openPage).toBeNull();
  });
});
