# Saved ChatGPT accounts in Home Assistant

Codex Bridge will keep saved ChatGPT sign-ins inside the private Home Assistant
App. These are separate from Codex Account Switcher profiles on a Windows or
Mac computer: copying a desktop credential into Home Assistant is not part of
the browser flow. The switcher's ordered save, activate, verify and restore
sequence is the reference pattern; its desktop process control is not reused.

The account menu lists a user-chosen label and the current selection. An HA
administrator can save the current ChatGPT sign-in, add another through device
sign-in, select a saved account, or remove an inactive saved profile. The
browser receives only labels, opaque profile IDs and safe state. The menu
never returns credential files, token claims, account IDs or device login
secrets beyond the existing one-time device code flow.

Credentials live under the App's private data directory as bounded, regular,
no-follow files with owner-only permissions. Up to 16 sign-ins can be saved.
The active Codex home remains the only runtime credential source. Saving or
switching checks a provider-backed request because `account/read` may report
a cached identity even when a token refresh fails. A switch excludes active/queued turns and
authentication/configuration changes, refreshes the saved active credential,
atomically activates the selected credential, restarts only the App-managed Codex
app-server, verifies the selected identity and only then commits the selected
profile. A failed verification restores the original credential and runtime.
The HA App and Windows Codex desktop keep running. If restoration or final
status is uncertain, task admission stays closed until recovery; a final
status race may leave the selected credential active for an administrator to
recheck.

Switching accounts detaches provider-side continuation handles while keeping
local chats, projects, files and history. This also separates personal and
workspace account contexts that may share an email address. The App does not
rotate accounts automatically or route individual tasks to inactive profiles.
The account-binding marker moves from email to the private ChatGPT account ID;
existing provider continuation handles are detached once on upgrade, while
local records remain. If the identity is unavailable, admission fails closed.
The existing Usage panel reflects the selected account after verification.

Acceptance requires a native HAOS check with two test sign-ins and no active
turns, same-email context coverage, failed and expired credential recovery,
restart/reload durability, safe menu behaviour at desktop and phone widths,
and no credential material in HTTP, WebSocket, events or logs.
