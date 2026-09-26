# Chat operations

The Home Assistant Integration negotiates `chat_operations_v1` before using
navigation metadata, custom sections, native chat forks, or project moves.
Older Apps leave those controls unavailable. Bridge-owned navigation fields are
additive on existing chat records: `pinned`, `unread`, `section_id`, and
`navigation_revision`. Navigation writes must include the revision returned by
the latest chat projection; stale writes return a conflict and the panel should
refresh the chat.

Section records live in the Bridge's durable state. Names are unique without
regard to case. Deleting a section clears its memberships and increments the
affected chats' navigation revisions. Existing chat records without navigation
fields load with unpinned, read, unsectioned defaults.

Fork and project move require a verified current ChatGPT account, an idle chat
with a native thread handle, and an unchanged App-server generation throughout
the native `thread/fork` request. The Bridge validates the returned provider
thread ID and canonical working directory before changing local ownership. It
does not replay local history to Codex. Forks share their current project
workspace, copy chat-owned uploaded and generated files within confined
boundaries, and project only user/assistant text into the new local chat. Tool
logs and approval payloads are not copied. A move copies owned uploads and
generated/archive outputs into the destination workspace, retains original
files, then binds the returned same-account native fork to the existing local
chat. An uncertain provider result leaves the prior local mapping authoritative.

Moves can target normal Bridge projects. Ordinary files in a shared project
workspace do not establish chat ownership: the move review leaves these files
unchecked and copies only the administrator's selected files, up to 100. The
request submits their known `workspace_artifact_ids`; an empty selection copies
only chat-owned uploads and private generated images, captures and archives.
Unselected workspace files remain in their original project and are removed
from the moved chat's file projection. Subsequent moves retain copied outputs
without requiring the administrator to select those owned copies again.

Each move places its copies in a new revision-specific directory, so revisiting
a project does not overwrite an earlier copy. Confined reads and exclusive writes
check file identity and enforce storage reservations. Original private files
retain their cleanup ownership, while forks never inherit authority to delete
another chat's private files. A fork's provider binding and safe message history
are published in one durable operation. An interrupted durable save retains the
provider and copied files for recovery, including their quota accounting; it
does not report success or delete resources that recovery may still require.

A failed copy also retains quota accounting when its exact file cannot safely
be removed. Known retained bytes are committed to the ledger. Uncertain file
identity blocks new reservations and growth through existing reservations in
that pool; restarting the Bridge requires fresh bounded disk measurements.

Chats with scheduled continuation automations cannot move until those
automations are explicitly retargeted or removed. This preserves their existing
workspace trust boundary. Section membership is separate from project
membership; project moves preserve the section assignment.
