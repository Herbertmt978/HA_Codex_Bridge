# Chat appearance and activity

User messages use black bubbles with white text. Assistant prose stays on the
page background and supports safe Markdown headings, lists, quotations and tables.
Raw HTML and filesystem download links stay inert; indexed file cards retain
authenticated downloads. Copy is available on fenced code blocks and copies their
contents, preserving indentation and line breaks. Long code fences stay in one
code block even when they exceed the 200,000-character prose formatting budget;
large blocks use plain text instead of expensive syntax highlighting. Message
copy and code copy retain the complete original source.

Integration 1.13.11 removes the earlier 4,096-character chat-event cutoff.
The Integration accepts message text up to 1 MiB of UTF-8 within the existing
Bridge event contract, and the panel retains up to 1 MiB of streamed characters.
The Bridge's existing default completed-message limit remains 512 KiB of UTF-8;
this is not an unlimited provider-output guarantee. Replies longer than the
prose formatting budget remain readable as plain text. Reloading a chat also
recovers complete replies still retained by the Bridge when an older Integration
had shortened their display; text already omitted by the provider or Bridge
cannot be recovered by the panel.

App, Integration and panel 1.13.12 support native Codex question forms in normal
attended chats, as well as Plan mode. Native Codex questions offer choices and a
labelled custom written answer. The panel also accepts question requests that
provide only a free-text field. Optional questions appear without moving keyboard focus away
from the composer. Submit the answer on its card while the request is available;
the expiry is shown on the card. Normal-mode questions expire after at most one
minute (or the configured shorter interaction timeout), then Codex receives an
empty answer and can continue. This never selects an option or grants approval.
Plan questions retain their existing blocking timeout.

Native questions belong to their active turn. Completion, cancellation, provider
resolution, account/runtime changes or restart makes an old card unavailable.
The panel cannot turn an ordinary sentence into a form or recover a question
that an older Bridge already dismissed. Unattended tasks and isolated Assist
sessions keep questions disabled or decline them safely.

The desktop side columns are 15% narrower and the central reading column can
grow to 960 pixels. Mobile drawers keep their existing dimensions.

One activity control shows the current runtime action, including Thinking,
running tools, changed-file totals and completed image views. Open it for the
plan, recent actions and expandable command details. Image counts reflect
completed image-view items reported by the runtime; they do not include uploaded
attachments merely because they are present in a chat.

Command previews are administrator-visible and saved with chat activity. They
can contain filesystem paths and arguments. The Bridge omits commands containing
recognised credential patterns, including authentication headers, token/password
names, credential URLs, environment assignments and long encoded values. This
is a conservative display filter, not a guarantee of finding every secret.
An unrecognised secret in a command can therefore remain in saved chat activity.
Avoid putting credentials in command arguments, including commands sent through
an MCP tool. Omitted previews retain the generic tool label.
Output, environment values and image paths are not added to activity events.
Command previews are limited to 2,000 characters; the UI retains the most recent
eight previews in its activity details. Older Apps show generic tool activity
without command text. Existing history cannot recover previews previously omitted.
