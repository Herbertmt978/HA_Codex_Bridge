# Chat controls

The composer shows **Stop** while Codex is working. Start typing to change the
main button to **Steer**; a separate Stop button remains available beside it.
Stopping does not discard your draft. Enter sends your draft, while Shift+Enter
adds a new line. Pressing Enter in an empty composer does not stop the run.

**Add** beside the composer opens file and folder uploads. When the connected
App supports them, it also offers **Schedule this message** and **Plugins**.
Actions the Bridge cannot perform are omitted. On a phone, **Chats** opens the
navigation drawer and **Context** opens the activity and usage drawer. **Chat
settings** in the chat actions menu opens the editable title and permission
settings in the navigation drawer. The panel fills the
visible space below the Home Assistant header as the window height changes.

Where the browser supports speech recognition, the microphone beside Send
dictates into the draft. Select it again to stop, review the text, then press
Send yourself. The browser may send audio to its speech service; audio is not
uploaded through the Bridge. The microphone is hidden when speech recognition
is unavailable.

The small ring beside Send appears once Codex reports context usage for this
chat. Select it for token counts and account limits. Context can decrease when
Codex compacts earlier conversation. Account limits and context usage measure
different things.

The Usage panel also lists any reset credits reported by the signed-in Codex
account, including their expiry when available. Select **Use reset** to review
one credit, then **Confirm use** to redeem it. This affects the account's
eligible usage windows and cannot be undone. If the connection drops after
confirmation, retrying that attempt uses the same idempotency key so it cannot
redeem a second credit. The panel does not offer redemption when the backend
reports only a credit count without individual credit details.

When usage is exhausted, the chat shows a banner linking to Usage and resets.
During a run, the activity line shows elapsed working time with three animated
dots; reduced-motion preferences stop the animation. Message timestamps use
the browser's local time.

## Sidebar and sharing

The sidebar groups pull-request links, workspace outputs and sources used in
the chat. Open an output to preview or download it. Expand a pull request and
follow its GitHub link to check its current review or merge status. The Bridge
does not infer that status from what Codex wrote. Sources include uploads and
links mentioned in the chat; a link's presence does not verify its contents.
The Subagents section shows working, completed and attention counts when Codex
reports them in the active run. It does not invent individual agent identities
or show unrelated Codex desktop tasks.

The plus button beside Outputs opens the workspace file list. The Sources plus
button uploads files to the chat. The activity button shows current run details,
and the side-panel button hides or restores the sidebar. On smaller screens,
use Context to open its drawer.

**Share** copies a link to this chat in your Home Assistant. Recipients must be
able to reach that Home Assistant instance and sign in as an administrator.
The link follows the live chat; it is not a published copy. Public, read-only
snapshots are [planned separately](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/95).

## Workspace terminal

Open the bottom panel, select **Terminal**, then **Open terminal**. This needs
App and Integration 1.1.2 or later and an editable, unarchived chat. Observe mode
does not permit a terminal.

Commands you type run immediately. They can read and change the selected chat's
workspace, including its source files and uploaded copies. The shell cannot
read sibling workspaces or private Home Assistant/App files, and it has no
network access. Choosing a host-access chat does not turn this terminal into a
host shell. The optional Host Access App remains a separate feature.

Only one terminal can run in the App at a time. Close it before asking Codex to
work, changing account/configuration, or uploading and modifying workspace files
elsewhere. The terminal temporarily owns that workspace operation to avoid
conflicting writes and to keep storage limits enforceable.

- Ctrl+C interrupts the foreground command. Type `exit` or select **Close
  terminal** to end the shell.
- Ctrl+Shift+M moves keyboard focus to Close terminal.
- Hiding the bottom panel keeps the shell running. Changing chats or leaving the
  panel ends it. If the connection disappears, the App closes it after 30 seconds
  without a heartbeat. Sessions also have a 30-minute maximum lifetime.
- Output is temporary and is not saved in chat history or public shares. The
  terminal keeps a bounded scrollback and stops if it reaches its output limit.
  Paste fewer than 16 KB at once.

An App restart ends all terminals. Already completed file changes remain; closing
a terminal does not undo them.
