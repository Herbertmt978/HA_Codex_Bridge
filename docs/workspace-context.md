# Workspace context

When the paired runtime advertises `workspace_context_v1`, **Chat settings and
limits → Attach workspace context** can browse this chat’s workspace or read a
workspace-relative file path. Leave both line fields blank to attach the whole
file. Enter both inclusive endpoints to attach a precise excerpt. Open the
attached item to inspect the exact text; **Remove context** removes it before
sending. Context selections remain in memory for this visit, separately from
optional saved text drafts, and clear when the Home Assistant user changes.

The composer labels whole files and line excerpts distinctly. It sends a typed
reference, never a client-authored excerpt. The Bridge checks the selected source
again and supplies the reviewed text as visible untrusted reference material in
the user message and native turn. Edited, moved or deleted files block submission
without changing the draft or reviewed text. **Refresh excerpt** explicitly reads
the current selection; inspect it before sending again, or remove the item.
An interrupted response retries the original request and references, with the
original mode and web-search settings. Controls remain disabled during that
uncertain request until it settles.

References are confined to the selected thread’s approved workspace. No-follow
descriptor reads reject symbolic links, including intermediate directories;
traversal and sibling workspaces are excluded. Read and list responses are
administrator-authenticated through Home Assistant and are not cached. Context
adds no workspace, host, network or instruction permission.

Limits are eight context items, 96 KiB combined UTF-8 text, 32 KiB per item and a
2 MiB source file. Whole files are limited to 400 lines; selected excerpts to
200 lines. Larger files need a smaller range. Binary and invalid UTF-8 files are
rejected. Browsing returns at most 200 entries, scans at most 500, limits depth
to 16 and bounds the response to 64 KiB. Partial listings are labelled; a known
workspace-relative path can still be entered directly.

Queue and Steer retain the same immutable selection references. New dispatches
revalidate them, including recovered queued work. Accepted idempotent retries
return the original outcome rather than rereading or resubmitting a changed file.
Local unit checks do not establish release or native Home Assistant acceptance.
