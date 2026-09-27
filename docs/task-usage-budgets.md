# Task usage and elapsed-time limits

Open **Usage history** beside the chat controls to inspect retained runs for the
selected chat or project. Refresh explicitly to read newer observations. History
is private to the Bridge and reaches the panel through Home Assistant's
administrator API; it is not browser-local storage and does not query a provider.

Each row shows its execution account, time, reported tokens and outcome. Saved
account labels are captured at admission; an unsaved account gets a stable local
account number. Switching, renaming or removing a saved account does not reassign
earlier runs. Unverified attribution remains **Account not reported**. A task is
never bound to a separately selected saved-account runtime by this feature.

The meter uses native cumulative thread snapshots and their actual thread/turn
identities. It does not sum last-response readings, duplicate notifications or
child counts. An established fresh thread starts at zero only when creation and
account ownership are verified. A resumed thread without a baseline remains
unmeasured until a later growth observation; coverage remains partial. Native
history replay cannot add an old turn's usage to a new run. Missing, invalid,
decreasing or detectable synthetic context-fill counts break continuity.

**Usage not reported** means unknown, never zero. A reported zero requires a
verified zero reading. Partial coverage identifies gaps, resets and resumed
work; it is not an exact lifetime total. The runtime can estimate or synthesise
counts, and does not provide a billing-completeness marker. **Reported tokens**
therefore mean runtime observations, not provider invoices. Context occupancy,
subscription allowance percentages and consumed tokens are separate measures.
This feature supplies no currency conversion, price estimate or token limit.
Unknown token-budget fields are rejected rather than silently ignored.

History retains at most 1,024 runs, with active rows protected from eviction, at
most 2,048 native meter scopes and account identities, and a 4 MiB private ledger.
The panel reports when earlier runs were evicted. Scope watermarks survive run
eviction when capacity permits; an evicted watermark makes resumed usage partial.
Deleting a chat removes it from visible history. Child work is not independently
aggregated into the parent's reported counter: the pinned protocol does not prove
that those meters are disjoint. No pre-feature history or billing is invented.

## Optional elapsed-time limit

Choose an **Elapsed-time limit** before sending a new turn, or keep **No extra
limit**. The selected value is visible and applies to that one submitted turn.
It starts when the broker begins native execution, including native preparation;
time waiting in the queue does not consume it. The Bridge rejects values above
its existing runtime ceiling. A new elapsed-time limit cannot be attached while
steering a running turn; use an explicitly queued next turn instead. Retries of
the same submission retain its original limit and cannot restart expired work.

The existing runtime watcher requests an interrupt for the exact current native
turn when the elapsed-time limit is reached. Notification/request latency and
in-flight work can overshoot it; this is interrupt-trigger timing, not a provider
hard cap. Token telemetry is not needed to enforce this elapsed-time trigger.
Partial transcript output, files and results remain available. An elapsed-time
stop does not imply goal completion or change manually recorded progress.

Explicit queued prompts are independent user work. A budget stop does not cancel
them or unrelated chats, and does not automatically retry or continue the stopped
turn. It does not bulk-stop child agents. If the native turn has not confirmed its
stop within the existing grace period, history shows **Stop unconfirmed** and
new admission is blocked until authoritative recovery. The Bridge retains the
queued records and cancellation ownership; it does not kill a shared runtime to
enforce an early per-turn limit. The existing global safety ceiling remains owned
by the existing runtime lifecycle. A restart records the interrupted outcome and
revalidates undispatched queued work with its original authority.

Capabilities `usage_history_v1` and `elapsed_time_limit_v1` gate the private API,
Home Assistant proxy and panel. Older Apps do not receive unsupported requests.
Usage history is a read-only `GET /usage` projection; the prompt request carries
the optional strict integer `max_duration_seconds`. No scheduler, provider
poller, account cycling, permission grant or recurring work is introduced.
