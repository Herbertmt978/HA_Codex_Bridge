# Project context: Home Assistant Codex Bridge

## Purpose

Home Assistant Codex Bridge keeps Home Assistant as the browser-facing control
plane for Codex:

```text
Browser -> Home Assistant -> Codex Bridge Integration -> private Supervisor App or external Bridge -> Codex / OpenAI
```

Remote access terminates at Home Assistant. A browser must not connect directly
to the App or Bridge.

## Terms

| Term | Meaning | Do not use for |
| --- | --- | --- |
| **App** | The Supervisor-managed Codex Bridge runtime beside Home Assistant. | The Home Assistant integration. |
| **Integration** | The `codex_bridge` Home Assistant component, configuration flow, and administrator panel. | The App or Bridge process. |
| **Bridge** | A private service that receives authenticated Integration requests and coordinates Codex. | Codex itself. |
| **Workspace** | A deliberately granted project folder; in App mode, it is beneath `/config/workspaces`. | Home Assistant configuration or a generic broad share. |
| **Project** | A user-visible group of Codex chats with one workspace and defaults. | A workspace or repository. |
| **External Bridge** | An optional, separately operated private Bridge compatibility path. | A required Windows VM or browser endpoint. |
| **Host Access App** | An optional, separately installed companion for explicitly acknowledged root commands on the HAOS machine. | Normal workspace access, an MCP server, or permission to use another host. |
| **Automation** | A durable prompt definition whose due time is scheduled by Home Assistant and claimed idempotently by the Bridge. | A free-running background worker or unrestricted cron job. |
| **Skill** | A workspace-scoped Codex instruction under `.agents/skills/`. | A global executable or a path outside the workspace. |
| **MCP server** | An explicitly enabled outbound streamable-HTTP server: public HTTPS with optional OAuth, an acknowledged local endpoint through the confined relay, or a verified local stdio package behind a separate Bridge-owned private HTTP adapter. | A public listener for the App, Bridge, or Home Assistant; an arbitrary native Codex command. |
| **Global/project AGENTS.md** | Global Codex instructions or an `AGENTS.md` at the selected project workspace root. | A way to grant Codex additional filesystem access. |

## Current compatibility statement

- This release pairs App `1.8.11`, Integration and panel `1.8.11`, Bridge `0.15.0` and
  Codex `0.157.1`. App images support `amd64` Home Assistant OS. Historical
  release evidence remains in the changelog and GitHub Releases.
- App/Integration/panel `1.0.3` completed signed publication and bounded DEV
  and production checks on 19 September 2026: retained sign-in and chat history,
  Astra discovery and the larger plugin catalogue. Publication and target
  checks for later releases must be recorded separately.
- The panel preserves unchanged controls across Home Assistant state updates.
  Scheduled task forms accept a title, instructions, chat destination and
  frequency in HA's time zone; the panel translates this into the existing
  once/interval/RRULE API. Revisions and target IDs are internal details.
- Scheduled run history shows readable outcomes and fixed explanations in HA's
  configured time zone, with a labelled UTC fallback when it is unavailable.
  Selected-task title and instruction descriptions require a current/proposed
  review and revision-bound partial update. Unrelated edits preserve pending
  occurrences. Conversational timing edits, pause and cancellation remain open
  in TASK-01.
- Home Assistant owns automation timing. The Bridge owns durable definitions,
  claims, run history, admission and unattended execution. Preserve exact saved
  timing when editing unrelated fields. Do not add a browser-owned timer.
- Local chats, files, projects and automation targets survive ChatGPT account
  changes. The previous account's provider handle is detached; earlier local
  messages are not automatically replayed to the newly connected account.
  Unverified identity blocks new prompts and unattended turns.
- Saved ChatGPT accounts belong to the Home Assistant App, separately from
  desktop Codex profiles. Administrators can save and select private App
  sign-ins; switching replaces only the managed Codex app-server process and
  checks the selected provider identity before reopening task admission.
- Model and reasoning choices come from runtime discovery. Recovery catalogues
  are marked stale and must not silently change an existing selection.
- The browser communicates only with Home Assistant. App discovery supplies
  the private Supervisor connection; users do not copy endpoints or tokens.
- Skills and project instructions remain workspace-scoped. Global instructions
  stay in private Codex storage. MCP is disabled by default. Public HTTPS/OAuth keeps its existing best-effort
  DNS screen. The local MCP exception requires separate App enablement and
  endpoint acknowledgement, and uses the private relay with approved IP
  pinning, TLS verification and redirect refusal. DNS changes require fresh
  approval. No shell, browser or host grant follows from an MCP connection.
- Isolated stdio MCP is opt-in through a separate, default-disabled App option.
  Its boundary is [ADR 0009](docs/aegis/adr/0009-isolated-stdio-mcp.md): a
  Bridge-owned stdio-to-private-HTTP adapter and an independently attested
  worker. Its first supported scope is approved Python 3.14 packages on amd64
  with no network or workspace file grant.
  Package approval, tool selection, quota checks and native HAOS acceptance
  precede activation; an App update alone grants nothing.
- The optional Host Access App privately pairs through Supervisor discovery.
  Pairing never grants access. An administrator must acknowledge the warning,
  then select host access for each chat or scheduled task. Scheduled work needs
  its own unattended acknowledgement. Native Codex shell tools retain their
  workspace sandbox; only the grant-bound host tool sends root commands to the
  companion. Revocation cannot undo completed changes or contain deliberate
  host-root actions. Production activation is separate from a Bridge upgrade.
- HA-MCP is a recommended optional community server for Home Assistant tasks.
  It is installed separately, uses the existing MCP connection requirements,
  and does not require or imply a host-access grant.
- The App-owned browser worker requires the explicit enable_browser option
  and a separate root startup proof under ADR 0006. Only new Codex sessions
  receive its typed tools. Native search and local PDF/image previews do not
  imply browser capability.
  Full PDF workflows, external proxy routes, cold restores and arbitrary
  previous-image rollback require their own target acceptance evidence.

## Product language

- MCP-05 adds pause/resume and destination editing. Native enabled state owns
  pause; saved private settings survive restart. Edits require pause and an
  explicit authentication choice, and stay paused after saving. Configuration
  changes exclude active/queued work and use revisions. Failed recovery blocks
  new work until restart. Upgrade alone enables no connection or permission.

- MCP-02 adds write-only bearer tokens and named authentication headers. The
  private relay owns their storage and injection for one approved destination;
  neither Codex configuration nor the browser can read a saved credential.
  Replacement and removal revoke active requests. Public credential connections
  require HTTPS and connection-time public-address validation. Existing public
  OAuth retains its native path. See ADR 0008 for the credential boundary.

- MCP-03 is an opt-in stdio capability. A reviewed package
  manifest fixes its source, version and command; the administrator reviews
  its grants and selects tools before enabling it. The worker receives only
  bounded MCP messages, an allowlisted environment, immutable package files
  and scratch space. Its private HTTP adapter never forwards the capability
  header or credentials to the worker. File and network expansion needs a
  separate security review; pause, update, rollback and removal must terminate
  owned processes and fail closed on uncertain recovery.

- The workspace terminal is an ephemeral HA administrator session in a dedicated
  Codex app-server process whose startup directory is the exact chat workspace.
  A request cwd alone does not narrow command/exec's workspace roots. Keep the
  managed minimal-read profile, disabled network, runtime/config lease and quota
  reservation. No host-access grant broadens this terminal. Output stays out of
  durable chat history; an idle lease and process deadline clean up disconnections.
- Assist-managed conversations are grouped under **HA Assistant Chats** in the
  sidebar. This is a presentation group; workspace and project membership remain
  unchanged, and ordinary chat submission stays blocked by the Assist policy.
  Assist-only project controls also live in this group, with archived project
  restore/delete controls inside its Archived disclosure.
- Share currently copies an authenticated Home Assistant chat link. Public
  snapshots remain planned and need a separate publication/privacy design.

- Keep **Integration** and **App** distinct. HACS installs the Integration;
  Supervisor installs the App from this repository.
- ChatGPT device login and Home Assistant login are separate. Use the exact UI
  labels **Sign in with ChatGPT**, **Cancel**, and **Sign out**. Cancellation is
  only for an in-progress sign-in; sign-out removes an established session.
- Normal panel use can remain on Home Assistant after sign-in. Initial sign-in
  and re-authentication require browser access to the approved ChatGPT
  device-auth page.
- Codex discovers available models and reasoning levels at runtime. A marked
  last-known-good catalogue must not silently change a chat to another model.
- App images are immutable. Never imply that the current Supervisor App can
  roll back to an arbitrary earlier image.
- Do not describe an automation as guaranteed execution: capacity, overlap,
  pause, and misfire policies can produce a recorded skipped run. Keep the
  public contract that Home Assistant schedules and the Bridge claims.
- Keep MCP documentation explicit that configured endpoints are outbound,
  disabled by default. Distinguish public HTTPS/OAuth from opt-in LAN/App
  endpoints through the private relay and isolated stdio workers behind their
  private adapter. Never expose the App or Bridge to the browser as an MCP
  endpoint. Public DNS screening remains best effort. Never disclose secret
  URL paths, relay credentials or OAuth authorisation URLs.
