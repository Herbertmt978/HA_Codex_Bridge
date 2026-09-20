# Browser tools

The optional browser lets Codex open public websites, read page text, click
controls, enter text, choose options and save screenshots or printed PDFs.
It runs inside the Home Assistant App. Your own browser remains connected
only to Home Assistant.

## Turn it on

1. Update both the Codex Bridge App and HACS Integration to `1.0.6` or later.
2. Open **Settings > Apps > Codex Bridge > Configuration**.
3. Turn on **Enable browser tools**, save, then restart the App.
4. Start a **New chat** in Codex Bridge.

The App checks browser isolation at startup. If those checks fail, ordinary
chats can still run, but browser tools stay unavailable. The App log reports
whether its browser checks passed. Do not change container privileges to
work around a failed check.

Existing chats keep the tools they received when their Codex session started.
Use a new chat after enabling this feature. To turn it off, disable the same
option and restart the App.

## Try it

For example:

> Open https://example.com with the browser tool, tell me the page title,
> and attach a screenshot and a PDF of the page.

Captures appear as private chat artifacts. Open them through Home Assistant
using the normal preview or download controls. Native web search is separate:
asking Codex to search the web does not necessarily use this browser.

## Limits

- Public HTTP and HTTPS websites on ports 80 and 443 only. Home Assistant,
  Supervisor, local devices and private network addresses are blocked, including
  redirects or DNS changes that would reach them.
- No saved Chrome login, cookies, account credentials, workspace files or
  Home Assistant configuration are supplied to the browser.
- Each browser session lasts at most five minutes or 100 actions. Only one
  session runs at a time. The session and its temporary profile are removed
  when the turn ends, is cancelled or fails. A later turn starts fresh.
- Screenshots are limited to 4 MiB and PDFs to 8 MiB. Page text is bounded.
  Resource limits can stop a large or unusually demanding page.
- This is a headless browser with a fixed set of actions. It does not provide
  an interactive desktop, arbitrary JavaScript, file uploads, downloads from
  websites or access to the browser's developer tools.

The feature currently requires the supported `amd64` Home Assistant OS App.
It is disabled by default and is unavailable on the external Bridge path.

If Codex cannot use it, check the App option and log, then try a new chat and
a simple public page. Websites can also require login, block automation or use
controls that the supported actions cannot operate.
