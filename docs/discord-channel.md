# Discord channel candidate

Discord access is optional and closed by default. The Home Assistant App opens
an outbound Discord Gateway connection. No Discord callback, Bridge listener,
or bot credential is exposed to the browser. A signed-in Home Assistant
administrator manages the connection through `GET`, `PUT`, and `DELETE`
`/api/codex_bridge/discord`; the Integration checks the App's
`discord_channel_v1` capability before forwarding these requests. The `PUT`
body contains `enabled`, `dm_user_ids`, `guilds`, and an optional `bot_token`.
`GET` returns the policy, connection state and a secret-free diagnostic, but
never the bot token. `DELETE` revokes the credential and suppresses pending
delivery. Updating the policy also cancels pending channel work and starts a
new identity epoch, so a removed user cannot later regain an old conversation
by being added again.

Create a dedicated Discord application and bot for this connection. Use the
**bot** installation scope; Discord includes `applications.commands` with it.
Grant **View Channel** and **Send Messages** only in each chosen shared text
channel. The commands use the Gateway interaction event and do not need the
privileged Message Content intent, guild member/presence intents, Read Message
History, Manage Messages, Manage Channels, Administrator or webhook permissions.
Disable the bot's public installation option where practical. Give the App
the bot token through the authenticated Home Assistant endpoint only. App
private storage holds it in a mode `0600` database below a mode `0700`
directory; it is absent from API responses, browser storage, Codex prompts,
native Codex configuration and logs. Revoking disables the connection and
clears the active saved credential. SQLite secure deletion is enabled for
database updates, but local revocation is not a guarantee of forensic erasure.
If the token may have been copied or disclosed, also reset it in Discord's
Developer Portal; local revocation cannot invalidate a token outside this App.
Home Assistant Supervisor backups may contain earlier copies of the App's
private database. Reset the token in Discord when retiring this connection;
local revocation does not scrub existing backups.

The policy requires exact Discord snowflake IDs. `dm_user_ids` permits those
users to invoke the commands in their bot DM. Every server rule requires its
`guild_id`, at least one `channel_id`, and at least one `user_id`. All three
must match on each command. Discord roles, channel membership, command
visibility and server administrator status confer no Bridge permission.
Bots and webhooks cannot invoke these slash commands as users; ordinary
messages and attachments are ignored. The supported commands are `/codex`,
`/codex_status`, and `/codex_cancel`.

Each authorised DM user has a separate continuing chat and workspace. Shared
channel commands create a fresh standalone chat and workspace for every
request. There is no way to select another user's chat or upload an artifact
through Discord. A shared result appears as a plain text message visible to
everyone who can read that channel; the command acknowledgement and status
are private to the invoker. The App omits artifact links and external links
from shared answers and disables all mentions and embeds. Treat the prompt
and its answer as shared with that channel's readers. Do not allow a private
or sensitive prompt in a shared channel.

Discord turns use the existing constrained Home Assistant task-action path:
observe mode, unattended interaction refusal, disabled web search, isolated
direct-project workspace, no Host Access grant and no Home Assistant
administrator identity. While the App's MCP connection manager is enabled,
this path declines new Discord work rather than granting tools through a
different identity. All privileged approvals remain in Home Assistant.

Interaction IDs are durably claimed before a Codex turn is submitted. The
Bridge's task-action ID then makes admission idempotent across Gateway replay
and restart. A result is fenced as attempted before the outbound HTTP call.
Discord's `enforce_nonce` handles near-term duplicate POSTs; an uncertain
network outcome is never retried automatically. A definite rate limit may be
retried once after a short `Retry-After`. Permission removal, a long rate
limit, or an uncertain response leaves the task available in Home Assistant
without repeated channel delivery. Revocation and policy changes suppress
unsent results. The status diagnostic reports only fixed error codes.

This candidate needs native qualification with a disposable server and bot:
allow-list success and rejection, DM separation, guild visibility, permission
removal, reconnect, restart, rate limiting and secret-free diagnostics. No
production Discord connection is implied by including this code.

Discord protocol references: [application commands](https://docs.discord.com/developers/docs/interactions/slash-commands),
[create message and nonce](https://docs.discord.com/developers/resources/message#create-message),
and [Gateway intents](https://docs.discord.com/developers/events/gateway#gateway-intents).
