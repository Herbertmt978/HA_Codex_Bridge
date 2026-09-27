# Previous chat context

When the connected App advertises `chat_context_v1`, **Attach previous chat
context** lets an administrator select a different chat and deliberately add
its currently retained public messages to an ordinary chat draft. Selection
never submits a prompt, starts a turn, forks a chat, switches accounts or grants
access. The browser talks only to Home Assistant.

Find a source chat by title, browse its messages, then choose the whole retained
chat, one message or an excerpt. Optional excerpt offsets count Unicode code
points from zero: the start is included and the end is excluded. An emoji counts
as one code point; a combined visible character can contain several. The panel
previews the entire attributed block that will be sent, including its source,
message labels, range and retention notice. **Remove chat context** discards the
draft item. **Refresh and review** deliberately replaces the preview with a new
selection of the current source.

Only public user and assistant text from the existing retained conversation
projection is eligible. System/developer instructions, reasoning, tool output,
terminal data and attachments are excluded. Existing credential screening is
reused. Unsafe or inaccessible source text is refused. Excerpts are labelled as
untrusted reference material and cannot grant permission or become system
instructions. This is a deliberate copy of reference text across chats, including
across selected ChatGPT accounts; account selection grants no additional access.

The Bridge validates both source and destination against the existing Home
Assistant, project and workspace boundaries. A missing source chat, an expired
message, changed source text or inaccessible source produces a reviewable refusal.
It never replaces the preview silently. Queued prompts retain the exact selected
block and revalidate it before native dispatch; a stale queue entry fails safely.
An already accepted response-loss retry returns its existing outcome first.

The whole-chat option means **currently retained public messages**, not lifetime
history. Earlier removed or expired messages cannot be recreated. At most 40
messages and 32 KiB of attributed UTF-8 text may be attached per item. Larger
whole-chat selections require a smaller message/excerpt; they are never silently
truncated. Workspace and chat context together share a maximum of eight items
and 96 KiB of rendered context. Existing final prompt/event bounds also apply.
Message pages contain 20 entries, with a server maximum of 50; the picker retains
at most 200 options. Source title filtering shows at most 100 chat choices.

Context drafts stay in memory for this visit, scoped to the Home Assistant user
and destination chat. They do not join browser draft recovery. Navigation and
user changes invalidate pending picker responses. Send captures immutable
references and the selection revision; acknowledgement clears only that captured
selection and cannot discard later selections or another user's context.

Older Apps omit the control, and explicit unsupported context requests are
rejected before submission. Generated assets, the combined release gates and
native acceptance are owned by the release coordinator. Local feature checks do
not establish release or native acceptance.
