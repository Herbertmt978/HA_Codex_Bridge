# Conversation and Git review enhancements

This development candidate covers CB-001, CB-005, CB-008, CB-009 and CB-015.
Release and native Home Assistant qualification are recorded separately.

| Requirement | Delivery issue |
| --- | --- |
| CB-001: assistant Markdown | [#160](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/160) |
| CB-005: transcript search | [#162](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/162) |
| CB-008: Queue and Steer | [#163](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/163) |
| CB-009: native Plan | [#164](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/164) |
| CB-015: scoped Git review | [#165](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/165) |

## Assistant responses

Assistant messages render headings, paragraphs, lists, inline code, quotations,
tables and fenced code. User messages remain plain text. Raw HTML cannot create
elements or execute code. Unsafe URL schemes and credential-bearing URLs remain
inert. External links require HTTPS and opener protection; supported local links
stay on Home Assistant. Filesystem paths are not browser download URLs: indexed
files retain the existing authenticated file-card actions.

Fenced-code copying preserves the original code, indentation and line breaks.
Formatting is bounded for browser responsiveness. A longer response retains its
remaining text in a labelled, scrollable plain-text section. Markdown image syntax
does not load arbitrary images; the existing decoded, bounded and authenticated
attachment/artifact image views remain responsible for image presentation.

## Search messages

Sidebar search also searches visible user and assistant message text when the
App advertises `transcript_search_v1`. Results show a matching excerpt and open
the original message anchor. Include archived chats to find archived responses;
More matching messages follows a stable cursor to older results. Search does not
start Codex or replay prompts. Administrator checks apply to both search and
retrieval of an earlier message.

The search projection excludes runtime/tool context and recognised credential
patterns. Credential recognition is conservative, so credentials should never be
put in chat messages. Chat deletion removes the corresponding indexed messages.
Queued-prompt edits update the original searchable message rather than creating
a second prompt. Removing an unsent queued message removes its transcript/search
projection while retaining the cancelled run lifecycle.

The index is a bounded, derived projection, separate from activity retention.
It survives activity compaction while space permits. It may evict its oldest
indexed text to preserve normal chat availability; it never deletes chats or
activity to make space. The search response reports its coverage and budget,
and the panel explains when earlier messages are outside retained searchable
history. Upgrade migration can recover only message events still retained at
upgrade: it cannot recreate text removed by previous compaction. Earlier search
matches can be opened through the authenticated transcript projection when their
activity event is no longer available.

## Queue and Steer

When `prompt_queue_v1` is negotiated, Chat settings and limits offers an explicit
follow-up choice. Queue waits for the active response to finish; Steer targets
the active native turn. An omitted follow-up field retains the older automatic
behaviour for existing clients. An explicitly requested unsupported feature is
rejected before prompt submission, rather than silently changing its meaning.

Queued messages are visible in the conversation and can be edited or removed.
Each edit uses the revision the administrator reviewed. Claiming, editing and
removing share the runtime lock: after a worker claims a message, an edit or
removal is refused and the current state must be refreshed. Durable prompt
identity makes response-loss retries and reconnects safe. Safe, unclaimed
explicitly queued work survives a Bridge restart; an uncertain provider start
is interrupted rather than submitted again. Existing account, workspace,
admission, deadline and cancellation rules still apply.

## Native Plan mode

When the App advertises `plan_mode_v1`, Collaboration offers Plan independently
of the chat's permission mode. Native planning uses the pinned Codex runtime's
experimental `turn/start.collaborationMode` field and built-in Plan instructions.
The schema projection imports only that verified field; unrelated experimental
methods are not enabled by this change.

A planning turn uses the observe/read-only execution policy. It retains the
selected model, reasoning level and workspace, and does not grant edit or Host
Access authority. Planning questions use the existing authenticated interaction
controls. Write and command approvals cannot expand Plan authority. Host commands
and interactive browser form actions are refused during Plan; available browser
research remains subject to its existing grants. Plans remain reviewable in
assistant messages and activity. The
selected collaboration mode persists through refresh and reload.

Implement reviewed plan deliberately requests the default collaboration mode,
using the chat's existing execution permissions and selected scope/model. A
mode change during an active turn must be queued because native steering does
not accept a collaboration-mode change. Unsupported runtimes show the limit
and reject explicit Plan requests; they do not simulate Plan with a prompt.

## Authentic Git review

Review changes opens a file list and bounded per-file patches for Unstaged,
Staged, Commit and Branch scopes. Commit defaults to HEAD and compares the selected
commit with its first parent; a root commit compares with the empty tree. Branch
requires an explicit comparison base. References are resolved by Git, and a state token
prevents a later file load from silently using a different repository state.
The token is an internal verification detail, not a user-facing identifier.

Review is read-only and confined to the chat's granted workspace. Content-reading
Git operations use a bounded private metadata snapshot with trusted configuration.
Review uses its own Git configuration, so line-ending normalisation and presentation
can differ from a local checkout's global settings.
Repository hooks, external diff/textconv and clean/process filter commands cannot
be enabled by repository configuration or a concurrent configuration change.
Diffs compare raw repository and worktree content without external transformations.
No review action grants file, network, terminal or Host Access permission.

Each request has a 20-second deadline. Metadata copying is limited to 256 MiB
and 50,000 entries, with 16 MiB for index/reference consistency checks. Changed
worktree hashing is limited to 256 MiB across the request. File lists retain at
most 200 entries and each displayed patch is limited to 48 KiB. Larger requests
return a labelled unavailable or truncated result.

Binary files, oversized patches and truncated file lists are labelled. Symlinked
worktree paths are unavailable rather than followed. Long lines
scroll inside their patch on a phone. Missing repositories, invalid references,
unsafe metadata and snapshot limits produce unavailable states. Linked worktree
metadata, common directories and object alternates are unsupported; metadata
outside the grant is refused. Last-turn review remains explicitly
unavailable because this release does not record a trustworthy Git baseline for
each Codex turn; activity file counts are not presented as a substitute for a diff.

## Verification boundary

Unit and browser fixtures exercise these controls and negative paths. They do not
prove native Codex execution or acceptance on HAOS. The delivery work record and
release coordinator retain the actual local, Linux, image and native DEV evidence.
