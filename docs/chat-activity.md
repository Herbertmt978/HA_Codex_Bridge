# Chat appearance and activity

User messages use black bubbles with white text. Assistant prose stays on the
page background. Copy is available on fenced code blocks and copies their
contents, preserving indentation and line breaks.

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
