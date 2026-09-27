import { createReadStream } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, resolve, sep } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { build } from "esbuild";
import { expect, test } from "@playwright/test";
import { installGoalFixture } from "./cb010-goals-fixture.js";

const repositoryRoot = resolve(process.cwd());
const evidenceDirectory = process.env.CODEX_BRIDGE_COMPACT_EVIDENCE_DIR
  ? resolve(process.env.CODEX_BRIDGE_COMPACT_EVIDENCE_DIR)
  : resolve(tmpdir(), "codex-bridge-compact-composer", String(process.pid));
const bundlePath = resolve(evidenceDirectory, "codex-bridge-panel-source.js");
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

let origin;
let server;

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, { recursive: true });
  const result = await build({
    absWorkingDir: process.env.CODEX_BRIDGE_COMPACT_BASELINE_ROOT || repositoryRoot,
    entryPoints: ["frontend/src/codex-bridge-panel.js"],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2022",
    charset: "utf8",
    legalComments: "eof",
    loader: { ".css": "text" },
    outfile: bundlePath,
  });
  if (result.errors.length) throw new Error(result.errors.map((item) => item.text).join("\n"));

  server = createServer(async (request, response) => {
    const pathname = new URL(request.url || "/", "http://fixture.invalid").pathname;
    if (pathname === "/custom_components/codex_bridge/frontend/codex-bridge-panel.js") {
      response.writeHead(200, { "Cache-Control": "no-store", "Content-Type": contentTypes[".js"] });
      createReadStream(bundlePath).pipe(response);
      return;
    }
    const relativePath = pathname === "/" ? "frontend/e2e/panel-harness.html"
      : pathname === "/frontend/src/codex-bridge-pdf-worker.js"
        ? "custom_components/codex_bridge/frontend/codex-bridge-pdf-worker.js"
        : pathname.slice(1);
    const filePath = resolve(repositoryRoot, relativePath);
    if (!filePath.startsWith(`${repositoryRoot}${sep}`)) {
      response.writeHead(403).end();
      return;
    }
    try {
      const metadata = await stat(filePath);
      if (!metadata.isFile()) throw new Error("not a file");
      response.writeHead(200, {
        "Cache-Control": "no-store",
        "Content-Type": contentTypes[extname(filePath)] || "application/octet-stream",
      });
      if (pathname === "/frontend/src/pdf-preview.js") {
        response.end((await readFile(filePath, "utf8")).replace(
          'from "pdfjs-dist/legacy/build/pdf.min.mjs"',
          'from "/node_modules/pdfjs-dist/legacy/build/pdf.min.mjs"',
        ));
        return;
      }
      createReadStream(filePath).pipe(response);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolveListening, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListening);
  });
  const address = server.address();
  origin = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  if (server) await new Promise((resolveClose, reject) => {
    server.close((error) => (error ? reject(error) : resolveClose()));
  });
});


async function openCompact(page,width,theme) {
  await page.setViewportSize({width,height:900});
  await page.emulateMedia({colorScheme:theme,reducedMotion:"reduce"});
  await page.goto(`${origin}/frontend/e2e/panel-harness.html`);
  const panel=page.locator("codex-bridge-panel");
  await expect.poll(()=>panel.evaluate(p=>Boolean(p._config)&&!p._isLoading)).toBe(true);
  await panel.evaluate(async p=>{
    p._stopSystemEventSubscription(); await p._selectThread("thr_direct"); p._stopPolling(); p._stopEventSubscription();
    p._hass={...p._hass,user:{id:"compact-fixture-user"}}; p._loadPreferences();
    p._activeThread={...p._activeThread,status:"idle",active_run_id:null};
    const original=p._callWS.bind(p);
    p._config={...p._config,capabilities:["plan_mode_v1","git_review_v1","git_context_v1","conversation_search_v1","durable_goals_v1","web_search_v1","workspace_context_v1","chat_context_v1","task_usage_v1","elapsed_time_limit_v1","chat_operations_v1"]};
    p._compactCalls=[];
    p._callWS=async(command,parameters)=>{
      p._compactCalls.push({command,parameters});
      if(command==="get_config") return p._config;
      if(command==="get_goal") return {revision:0,goal:null,history:[],continuation:"manual_turns_only"};
      if(command==="git_context") return {repository:false};
      if(command==="git_review") return {files:[]};
      if(command==="conversation_search") return {results:[],total:0,complete:true};
      return original(command,parameters);
    };
    p._render(true);
  });
  return panel;
}

for(const width of [1280,390]) for(const theme of ["light","dark"]) {
  test(`compact viewport evidence ${width} ${theme}`,async({page},testInfo)=>{
    const panel=await openCompact(page,width,theme);
    const geometry=await panel.evaluate(p=>{
      const box=id=>{const b=p.shadowRoot.querySelector(id).getBoundingClientRect();return {top:b.top,height:b.height,bottom:b.bottom};};
      return {composer:box(".composer-shell"),conversation:box("#conversation-scroll"),goal:box("#goal-controls"),search:box("#conversation-search")};
    });
    await writeFile(testInfo.outputPath("geometry.json"),JSON.stringify(geometry,null,2));
    await page.screenshot({path:testInfo.outputPath(`viewport-${width}-${theme}.png`),animations:"disabled"});
  });
  test(`compact full viewport and relocated menus ${width} ${theme}`,async({page},testInfo)=>{
    test.skip(Boolean(process.env.CODEX_BRIDGE_COMPACT_BASELINE_ROOT),"Baseline only captures original geometry");
    const panel=await openCompact(page,width,theme);
    const composer=panel.locator(".composer-shell"),bar=panel.getByRole("group",{name:"Message toolbar",exact:true});
    await expect(bar).toBeVisible();
    const size=await composer.boundingBox(); expect(size.height).toBeLessThanOrEqual(180);
    const conversation=await panel.getByRole("region",{name:"Conversation",exact:true}).boundingBox();
    expect(conversation.height).toBeGreaterThan(400);
    await expect(panel.locator("#goal-controls")).toBeHidden();
    await expect(panel.locator("#conversation-search")).toBeHidden();
    await expect(panel.locator("#elapsed-limit-note")).toBeHidden();
    await page.screenshot({path:testInfo.outputPath(`compact-default-${width}-${theme}.png`),animations:"disabled"});
    const options=panel.getByRole("button",{name:/^Turn options/});
    await options.click();
    const dialog=panel.getByRole("dialog",{name:/^Turn options/});
    await expect(dialog).toBeVisible();
    await dialog.getByRole("combobox",{name:"Collaboration",exact:true}).selectOption("plan");
    await dialog.getByRole("combobox",{name:"Web search",exact:true}).selectOption("disabled");
    await dialog.getByRole("combobox",{name:"Elapsed-time limit",exact:true}).selectOption("300");
    await expect(dialog.locator("#elapsed-limit-note")).toBeVisible();
    await page.keyboard.press("Escape"); await expect(dialog).toBeHidden(); await expect(options).toBeFocused();
    await expect(options).toHaveAccessibleName(/Plan.*5 minutes.*Search Off/);
    await panel.getByRole("button",{name:/Model and thinking:/}).click();
    const models=panel.getByRole("dialog",{name:"Model, thinking and allowance",exact:true});
    await expect(models.getByRole("combobox",{name:"Chat model override",exact:true})).toBeVisible();
    await expect(models.getByRole("combobox",{name:"Chat thinking level override",exact:true})).toBeVisible();
    await page.keyboard.press("Escape");
    await panel.getByRole("button",{name:"Add to chat",exact:true}).click();
    await panel.getByRole("button",{name:"Files and workspace context",exact:true}).click();
    const files=panel.getByRole("dialog",{name:"Files and workspace context",exact:true});
    await expect(files.getByRole("button",{name:"Upload files",exact:true})).toBeVisible();
    await expect(files.getByRole("textbox",{name:"Workspace-relative file",exact:true})).toBeVisible();
    await page.keyboard.press("Escape"); await expect(panel.getByRole("button",{name:"Add to chat",exact:true})).toBeFocused();
    await panel.getByRole("button",{name:"Add to chat",exact:true}).click();
    await panel.getByRole("button",{name:"Previous chat context",exact:true}).click();
    await expect(panel.getByRole("dialog",{name:"Previous chat context",exact:true})).toBeVisible();
    await page.keyboard.press("Escape");
    await panel.getByRole("button",{name:/Chat actions/}).click();
    await panel.getByRole("menuitem",{name:"Goal",exact:true}).click();
    await expect(panel.getByRole("dialog",{name:"Goal — manual continuation",exact:true})).toBeVisible();
    await expect(panel.getByRole("button",{name:"Save paused goal",exact:true})).toBeVisible();
    await page.keyboard.press("Escape");
    await panel.getByRole("button",{name:/Chat actions/}).click();
    await panel.getByRole("menuitem",{name:"Find in chat",exact:true}).click();
    await panel.getByRole("searchbox",{name:"Find in selected chat",exact:true}).fill("needle");
    await page.keyboard.press("Escape"); await expect(panel.locator("#conversation-search")).toBeHidden();
    await panel.getByRole("button",{name:/Chat actions/}).click();
    await panel.getByRole("menuitem",{name:"Repository review",exact:true}).click();
    await expect(panel.getByRole("dialog",{name:"Repository review",exact:true})).toContainText("not a Git repository");
    await page.keyboard.press("Escape");
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect.poll(async()=> (await new AxeBuilder({page}).include("codex-bridge-panel").withTags(["wcag2a","wcag2aa"]).analyze()).violations).toEqual([]);
    expect(await panel.evaluate(p=>p._compactCalls.some(c=>c.command==="send_prompt"))).toBe(false);
    await page.screenshot({path:testInfo.outputPath(`compact-selected-${width}-${theme}.png`),animations:"disabled"});
  });
}




test("chat search shortcuts open the compact surface and retain its query when already open", async ({ page }) => {
  const panel = await openCompact(page, 390, "dark");
  const search = panel.getByRole("searchbox", { name: "Find in selected chat", exact: true });
  await panel.locator("#prompt-input").focus();
  await page.keyboard.press("Control+f");
  await expect(search).toBeVisible();
  await expect(search).toBeFocused();
  await search.fill("needle");
  await page.keyboard.press("Control+f");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("needle");
  await page.keyboard.press("Escape");
  await expect(search).toBeHidden();
  await panel.locator("#prompt-input").focus();
  await page.keyboard.press("Meta+f");
  await expect(search).toBeVisible();
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("needle");
  expect(await panel.evaluate((p) => p._compactCalls.some((c) => c.command === "send_prompt"))).toBe(false);
});

test("compact options, inspected file context and captured acknowledgement preserve newer edits",async({page})=>{
  test.skip(Boolean(process.env.CODEX_BRIDGE_COMPACT_BASELINE_ROOT));
  const panel=await openCompact(page,390,"dark");
  await panel.evaluate(p=>{
    const call=p._callWS.bind(p);
    p._callWS=async(command,parameters)=>{
      if(command==="read_workspace_context") return {status:"ready",text:"const example = 42;\n".repeat(200),content_revision:"a".repeat(64)};
      if(command==="send_prompt") { p._capturedSend=parameters; return new Promise(resolveResult=>{p._releaseSend=()=>resolveResult({run_id:"compact-accepted"});}); }
      return call(command,parameters);
    };
    p._refreshActiveThread=async()=>{};
  });
  await panel.getByRole("button",{name:"Add to chat",exact:true}).click();
  await panel.getByRole("button",{name:"Files and workspace context",exact:true}).click();
  const files=panel.getByRole("dialog",{name:"Files and workspace context",exact:true});
  await files.getByRole("textbox",{name:"Workspace-relative file",exact:true}).fill("src/example.js");
  await files.getByRole("button",{name:"Inspect and attach",exact:true}).click();
  await expect(files.locator("pre")).toHaveText("const example = 42;\n".repeat(200));
  expect(await files.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(panel.getByRole("button",{name:/Inspect selected context: 1 items/})).toBeVisible();
  await panel.getByRole("button",{name:/^Turn options/}).click();
  const options=panel.getByRole("dialog",{name:"Turn options",exact:true});
  await options.getByRole("combobox",{name:"Collaboration",exact:true}).selectOption("plan");
  await options.getByRole("combobox",{name:"Web search",exact:true}).selectOption("disabled");
  await options.getByRole("combobox",{name:"Elapsed-time limit",exact:true}).selectOption("300");
  await page.keyboard.press("Escape");
  await panel.getByRole("textbox",{name:"Message Codex",exact:true}).fill("Use the inspected file");
  await panel.locator("#send-button").click();
  await expect.poll(()=>panel.evaluate(p=>Boolean(p._capturedSend))).toBe(true);
  const payload=await panel.evaluate(p=>p._capturedSend);
  expect(payload).toMatchObject({prompt:"Use the inspected file",collaboration_mode:"plan",web_search:"disabled",max_duration_seconds:300});
  expect(payload.workspace_context).toEqual([{path:"src/example.js",start_line:null,end_line:null,content_revision:"a".repeat(64)}]);
  // Simulate a newer local edit while an earlier captured submission is awaiting
  // acknowledgement. The existing revision owners must retain those edits.
  await panel.evaluate(p=>{
    const input=p.shadowRoot.getElementById("prompt-input"); input.value="Newer draft";
    input.dispatchEvent(new Event("input",{bubbles:true}));
    p._setDurationChoice(p._selectedThreadId,900);
    p._workspaceContext.setItems(p._selectedThreadId,[{path:"newer.js",start_line:null,end_line:null,content_revision:"b".repeat(64),text:"newer",stale:false}]);
    p._releaseSend();
  });
  await expect(panel.getByRole("textbox",{name:"Message Codex",exact:true})).toHaveValue("Newer draft");
  await expect.poll(()=>panel.evaluate(p=>p._durationChoices.get(p._selectedThreadId))).toBe(900);
  expect(await panel.evaluate(p=>p._workspaceContext.current()[0].path)).toBe("newer.js");
  await expect.poll(()=>panel.evaluate(p=>p._promptMutations.size)).toBe(0);
  await panel.evaluate(p=>{ p._activeThread={...p._activeThread,status:"idle",active_run_id:null}; p._renderComposerState(p._activeThread); });
  await panel.getByRole("button",{name:/Inspect selected context/}).click();
  await panel.getByRole("button",{name:"Files and workspace context",exact:true}).click();
  await expect(files.locator("pre")).toHaveText("newer");
  await files.getByRole("button",{name:"Remove context",exact:true}).click();
  await expect(files.locator("pre")).toHaveCount(0);
});

test("compact Goal submenu persists manual goals and resume never sends",async({page})=>{
  test.skip(Boolean(process.env.CODEX_BRIDGE_COMPACT_BASELINE_ROOT));
  const panel=await openCompact(page,390,"light");
  await panel.evaluate(installGoalFixture);
  await panel.getByRole("button",{name:/Chat actions/}).click();
  await panel.getByRole("menuitem",{name:"Goal",exact:true}).click();
  const goal=panel.getByRole("dialog",{name:"Goal — manual continuation",exact:true});
  await goal.getByRole("textbox",{name:"Objective",exact:true}).fill("Keep the composer compact");
  await goal.getByRole("textbox",{name:"Completion criteria — one per line",exact:true}).fill("One toolbar\nAll controls accessible");
  await goal.getByRole("button",{name:"Save paused goal",exact:true}).click();
  await expect(goal.getByRole("button",{name:"Resume for manual turns",exact:true})).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(panel.locator("#goal-controls")).toBeHidden();
  await panel.getByRole("button",{name:/Chat actions/}).click();
  await panel.getByRole("menuitem",{name:"Goal",exact:true}).click();
  await expect(goal.getByRole("textbox",{name:"Objective",exact:true})).toHaveValue("Keep the composer compact");
  await goal.getByRole("button",{name:"Resume for manual turns",exact:true}).click();
  await expect(goal.getByRole("button",{name:"Pause goal",exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>window.goalFixture.requests.some(r=>r.command==="send_prompt"))).toBe(false);
  await page.keyboard.press("Escape");
});

test("compact menus work with touch and leave all secondary state intact",async({browser})=>{
  test.skip(Boolean(process.env.CODEX_BRIDGE_COMPACT_BASELINE_ROOT));
  const context=await browser.newContext({hasTouch:true,isMobile:true});
  try {
    const page=await context.newPage(),panel=await openCompact(page,390,"dark");
    await panel.getByRole("button",{name:"Add to chat",exact:true}).tap();
    await panel.getByRole("button",{name:"Previous chat context",exact:true}).tap();
    const previous=panel.getByRole("dialog",{name:"Previous chat context",exact:true});
    await expect(previous.getByRole("combobox",{name:"Previous chat",exact:true})).toBeVisible();
    const box=await previous.boundingBox(); expect(box.x).toBeGreaterThanOrEqual(0); expect(box.y).toBeGreaterThanOrEqual(0); expect(box.x+box.width).toBeLessThanOrEqual(390);
    await previous.getByRole("button",{name:"Close Previous chat context",exact:true}).tap();
    await expect(previous).toBeHidden();
    await panel.getByRole("button",{name:/^Turn options/}).tap();
    await expect(panel.getByRole("dialog",{name:"Turn options",exact:true})).toBeVisible();
  } finally {await context.close();}
});



for (const width of [1280,390]) {
  test(`bottom pane resize pointer keyboard bounds and lifecycle at ${width}`, async ({page},testInfo)=>{
    const panel=await openCompact(page,width,'dark');
    await panel.evaluate(p=>{ p._resizeCalls=0; p._terminal.resize=()=>p._resizeCalls++; p._toggleBottomPanel(); });
    const handle=panel.getByRole('separator',{name:'Resize file preview and terminal'});
    const pane=panel.locator('#bottom-panel');
    await expect.poll(async()=>Math.round((await pane.boundingBox()).height)).toBe(Number(await handle.getAttribute("aria-valuenow")));
    const initial=(await pane.boundingBox()).height;
    await handle.hover();
    await page.screenshot({path:testInfo.outputPath('pane-hover.png')});
    const box=await handle.boundingBox();
    await page.mouse.move(box.x+box.width/2,box.y+4); await page.mouse.down();
    await page.mouse.move(box.x+box.width/2,box.y-65); await page.mouse.up();
    await expect.poll(async()=>(await pane.boundingBox()).height).toBeGreaterThan(initial);
    await expect(panel.locator('.bottom-pane-drag-shield')).toBeHidden();
    await handle.focus(); await handle.press('Home');
    expect(Number(await handle.getAttribute('aria-valuenow'))).toBe(Number(await handle.getAttribute('aria-valuemin')));
    await handle.press('End');
    expect(Number(await handle.getAttribute('aria-valuenow'))).toBe(Number(await handle.getAttribute('aria-valuemax')));
    await handle.press('Enter'); const chosen=await handle.getAttribute('aria-valuenow');
    await panel.getByRole('button',{name:'Terminal',exact:true}).click();
    await expect(handle).toHaveAttribute('aria-valuenow',chosen);
    await panel.getByRole('button',{name:'Hide bottom panel',exact:true}).click();
    await panel.evaluate(p=>p._toggleBottomPanel());
    await expect(handle).toHaveAttribute('aria-valuenow',chosen);
    await page.setViewportSize({width,height:600});
    await expect.poll(()=>handle.getAttribute('aria-valuemax')).not.toBe(null);
    await expect.poll(async()=>Number(await handle.getAttribute('aria-valuenow'))<=Number(await handle.getAttribute('aria-valuemax'))).toBe(true);
    expect(await panel.evaluate(p=>p._resizeCalls)).toBeGreaterThan(3);
    const next=await handle.boundingBox(); await page.mouse.move(next.x+50,next.y+3); await page.mouse.down(); await page.mouse.move(next.x+50,next.y-30);
    await panel.evaluate(p=>p._toggleBottomPanel());
    await page.mouse.up(); await expect(panel.locator('.bottom-pane-drag-shield')).toBeHidden();
    await panel.evaluate(p=>{p._toggleBottomPanel();p.remove();});
    expect(await page.locator('.bottom-pane-drag-shield').count()).toBe(0);
  });
}

test('bottom pane touch drag cancellation preserves preview and terminal content',async({browser})=>{
  const context=await browser.newContext({hasTouch:true,isMobile:true,viewport:{width:390,height:900}});
  try {
    const page=await context.newPage(),panel=await openCompact(page,390,'light');
    await panel.evaluate(p=>{p._toggleBottomPanel(); const preview=p.shadowRoot.getElementById('artifact-preview-section'); preview.hidden=false; const block=document.createElement('pre');block.id='resize-preview-fixture';block.textContent='Preview content\n'.repeat(200);block.style.cssText='height:100px;overflow:auto';preview.append(block);block.scrollTop=80;});
    const handle=panel.getByRole('separator',{name:'Resize file preview and terminal'});
    await expect.poll(async()=>Math.round((await panel.locator('#bottom-panel').boundingBox()).height)).toBe(Number(await handle.getAttribute('aria-valuenow')));
    const initial=Number(await handle.getAttribute('aria-valuenow')),box=await handle.boundingBox();
    const session=await context.newCDPSession(page);
    await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+80,y:box.y+4}]});
    await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:box.x+80,y:box.y-60}]});
    await expect.poll(async()=>Number(await handle.getAttribute('aria-valuenow'))).toBeGreaterThan(initial);
    await session.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});
    await expect(handle).toHaveAttribute('aria-valuenow',String(initial));
    await expect(panel.locator('.bottom-pane-drag-shield')).toBeHidden();
    expect(await panel.locator('#resize-preview-fixture').evaluate(n=>n.scrollTop)).toBe(80);
    await panel.getByRole('button',{name:'Terminal',exact:true}).tap();
    await panel.getByRole('button',{name:'File preview',exact:true}).tap();
    expect(await panel.locator('#resize-preview-fixture').evaluate(n=>n.scrollTop)).toBe(80);
    await session.detach();
  } finally {await context.close();}
});

test('compact options recover and discard an opted-in unsent draft after reload',async({page})=>{
  let panel=await openCompact(page,1280,'light');
  await panel.evaluate(p=>p._selectDesktopDestination('settings'));
  const preference=panel.getByRole('combobox',{name:'Recover unsent drafts in this browser',exact:true});
  await preference.click(); await panel.getByRole('option',{name:'On',exact:true}).click();
  await panel.evaluate(p=>p._selectDesktopDestination('chats'));
  const draft='Recovered compact draft';
  await panel.locator('#prompt-input').fill(draft);
  await expect.poll(()=>panel.evaluate(async p=>(await p._draftRecoveryStore.restoreDraft('thr_direct')).draft)).toBe(draft);
  panel=await openCompact(page,1280,'light');
  await expect(panel.locator('#prompt-input')).toHaveValue(draft);
  await panel.getByRole('button',{name:/^Turn options/}).click();
  await expect(panel.getByRole('button',{name:'Discard draft',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Discard draft',exact:true}).click();
  await expect(panel.locator('#prompt-input')).toHaveValue('');
  expect(await panel.evaluate(p=>p._compactCalls.some(c=>c.command==='send_prompt'))).toBe(false);
});

test('pane sizing changes actual terminal fit rows and honours panned visual viewport',async({page})=>{
  const panel=await openCompact(page,1280,'light');
  await panel.evaluate(async p=>{
    p._toggleBottomPanel();p._selectBottomTab('terminal');
    p._terminal.call=async(command)=>command==='open'?{session_id:'fixture',state:'running',chunks:[]}:{state:'running',chunks:[]};
    await p._terminal.open('thr_direct');clearTimeout(p._terminal.timer);p._terminal._schedule=()=>{};
  });
  const handle=panel.getByRole('separator',{name:'Resize file preview and terminal'});
  await handle.focus();await handle.press('Home');
  const small=await panel.evaluate(p=>({height:p.shadowRoot.getElementById('terminal-host').clientHeight,rows:p._terminal.term.rows}));
  await handle.press('End');
  await expect.poll(()=>panel.evaluate(p=>p._terminal.term.rows)).toBeGreaterThan(small.rows);
  expect(await panel.locator('#terminal-host').evaluate(n=>n.clientHeight)).toBeGreaterThan(small.height);
  const bounds=await panel.evaluate(p=>{
    Object.defineProperty(window,'visualViewport',{configurable:true,value:{offsetTop:200,height:300}});
    p._bottomPaneResize.resize();return p._bottomPaneResize.bounds();
  });
  expect(bounds.max).toBeLessThanOrEqual(120);
  await expect(handle).toHaveAttribute('aria-valuenow',String(bounds.max));
  await panel.evaluate(async p=>{delete window.visualViewport;await p._terminal.close();});
});

for(const width of [1280,390]) for(const theme of ['light','dark']) {
  test(`compact transcript evidence ${width} ${theme}`,async({page},testInfo)=>{
    const panel=await openCompact(page,width,theme);
    await panel.evaluate(p=>p.shadowRoot.getElementById('message-list').replaceChildren(...Array.from({length:6},(_,i)=>p._renderMessage(i%2?'assistant':'user',`Message ${i+1}: a short response keeps the conversation readable.`,900+i))));
    const geometry=await panel.evaluate(p=>({transcript:p.shadowRoot.getElementById('message-list').scrollHeight,messages:[...p.shadowRoot.querySelectorAll('.message')].map(n=>({height:n.getBoundingClientRect().height,actionHeight:n.querySelector('.message-actions').getBoundingClientRect().height}))}));
    await writeFile(testInfo.outputPath('transcript-geometry.json'),JSON.stringify(geometry,null,2));
    await page.screenshot({path:testInfo.outputPath(`transcript-${width}-${theme}.png`)});
  });
}

test('minimum pane keeps its divider usable with visible controls in a panned viewport',async({page})=>{
  const panel=await openCompact(page,390,'light');
  await panel.evaluate(p=>{p._toggleBottomPanel();p.shadowRoot.getElementById('git-review-button').hidden=false;p.shadowRoot.getElementById('task-usage-button').hidden=false;Object.defineProperty(window,'visualViewport',{configurable:true,value:{offsetTop:200,height:300}});p._bottomPaneResize.resize();});
  const handle=panel.getByRole('separator',{name:'Resize file preview and terminal'});
  await handle.focus();await handle.press('Home');
  expect((await handle.boundingBox()).height).toBeGreaterThanOrEqual(8);
  await panel.evaluate(()=>{delete window.visualViewport;});
  await page.setViewportSize({width:390,height:900});
  await panel.evaluate(p=>p._bottomPaneResize.resize());
  const box=await handle.boundingBox();
  await page.mouse.move(box.x+50,box.y+4);await page.mouse.down();await page.mouse.move(box.x+50,box.y-60);await page.mouse.up();
  await expect.poll(async()=>Number(await handle.getAttribute('aria-valuenow'))).toBeGreaterThan(Number(await handle.getAttribute('aria-valuemin')));
});
