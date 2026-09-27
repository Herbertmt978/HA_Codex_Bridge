# Durable goals with manual continuation

A Bridge-managed goal keeps an ordinary chat's objective, completion criteria
and progress notes across turns and App restarts. It applies to deliberate new
prompts in that chat. It is separate from native Plan mode and Home Assistant
scheduled tasks.

Save an objective and one to sixteen completion criteria, one per line, using
the **Goal** disclosure above the conversation. New goals are paused. **Resume
for manual turns** makes the goal applicable to prompts you subsequently send;
it does not submit a prompt, run a model, create a timer or schedule further
work. Progress notes are edited explicitly. A finished response never marks a
goal complete. Review the criteria, tick the completion confirmation and choose
**Mark goal complete** yourself.

**Pause goal** and **Cancel goal** prevent future starts using that goal. A
bounded turn already started continues under the existing run owner; use the
chat's existing **Stop** control to stop it. These goal controls do not stop
sibling agents or cancel unrelated manual prompts. Cancellation is final for
that goal. Create a new goal deliberately if further work is needed. Editing
an objective or criteria requires pausing first. A goal grants no filesystem,
network, host, MCP or approval permission and cannot bypass a run budget.

Each new ordinary turn accepted while a goal is active records the exact
objective, criteria and progress snapshot as visible, user-provided context in
the transcript and in the native input. A later progress update does not rewrite
an accepted snapshot. Steer changes an already-started turn and does not attach
a new goal snapshot; use Queue to apply a goal to a new turn while a response is
running. Unattended schedules and Assist chats do not inherit manual goals.

If an accepted goal-associated turn is still queued or preparing when its goal
is paused, cancelled, completed or replaced, it fails visibly before sending
native turn input. Its original request and accepted context remain in the
conversation for review and deliberate resubmission. The Bridge does not remove
or replace accepted context to make it run. Pause followed by Resume creates a
fresh activation, so older queued work cannot revive. Other queued prompts
without a goal snapshot remain executable. A retry with the same request
identity returns its durable outcome and does not start another run.

The goal remains applicable after an App restart, but restarting and resuming a
goal create no new work. The existing queue owner may recover a previously
accepted, unexpired manual turn; it checks that turn's immutable goal snapshot
before dispatch. Account admission and all existing permission checks still
apply. Goals are local chat metadata, not bindings to isolated saved accounts.
They do not silently replay the earlier conversation to a different account.

## Runtime support and boundaries

The pinned Codex 0.157.1 protocol includes `thread/goal/get`, `thread/goal/set`,
`thread/goal/clear` and goal update/clear notifications. Its stable goals feature
is enabled by default. Activating a native goal can immediately launch idle
turns, and native idle continuation can create further turns outside the
Bridge's prompt admission and run accounting. The Bridge therefore explicitly
sets `features.goals=false` on its owned thread start, cold resume and fork
configuration. Child threads inherit the restricted thread configuration.
Assist already disables native goals.

This feature provides Bridge-managed durable goals with manual continuation.
It does not expose native active-goal controls or claim bounded native automatic
continuation. Native acceptance of the disabled configuration and child
inheritance must be verified against the pinned installed runtime before a
release is accepted. No runtime upgrade is required by this implementation.

## API and persistence

Capability `durable_goals_v1` gates both the Integration and Bridge controls.
Older Apps and ineligible chats show an unavailable state. The browser talks
only to Home Assistant; its administrator WebSocket actions `get_goal` and
`goal_action` proxy the private Bridge endpoints:

- `GET /threads/{local_chat_id}/goal` returns the current goal, revision and up
  to eight previous finished goals.
- `POST /threads/{local_chat_id}/goal/actions` accepts `create`, `edit`,
  `progress`, `pause`, `resume`, `complete` or `cancel`, an `expected_revision`
  and a `client_request_id`. Completion additionally requires
  `completion_confirmed=true`. The server validates fields per action and
  rejects stale revisions, unavailable chats and permission-setting fields.

The single goal owner uses the existing private durable outbox and thread
mutation lock. Canonical files are stored under `goals/` in Bridge state, paired
atomically with bounded goal status events. Goal text is limited to 32 KiB per
record, with at most sixteen criteria, eight previous finished goals and 64
recent action receipts per chat. Canonical reads are bounded at 512 KiB. Old
action receipts can expire; an unchanged replay then fails its stale revision
rather than reapplying it. Deleting a chat removes its goal file through the
existing deletion owner. Reading/opening a chat does not complete a goal or
resolve an interaction.

Refresh before resolving a conflicting edit. A failed or uncertain UI action
keeps entered text and offers an exact-identity retry or an explicit refresh of
the current server state. The interface uses ordinary labelled controls, visible
keyboard focus, existing panel colours and compact buttons.
