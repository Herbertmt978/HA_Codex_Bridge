# Individual subagents

The Activity panel can inspect genuine children reported by the managed Codex
runtime. **Refresh list** reads the Bridge's retained projection. It does not
discover agents from counts, prompts or prose, and creates no background work.
Expand a child to see its reported task and available public result.

**Verify status** reads that observed child's current native record. The Bridge
checks its actual parent and workspace against the authorised parent run, as
well as the account and runtime generation. **Stop this child** is available
only for a verified running child while its parent run still owns the runtime.
It refreshes the exact child again and requires the same active turn before
submitting a targeted interrupt. The parent, siblings and unrelated chats are
never fallback interrupt targets. An accepted request still needs a status
verification to confirm the resulting state. An uncertain outcome is retained
and is not automatically retried.

Completed, interrupted, failed and stopped children retain their reported
results. **Last known status** indicates an unverified observation, an earlier
run/account/generation, a reconnect or restart. Cached terminal observations
cannot be resurrected by replayed running events. Current verification can
establish a genuinely new active turn; an old stop click cannot interrupt it.
After a Bridge restart, retained children remain stale until the current
parent reports a matching child again. Reading chat history cannot recreate
identities which older releases deliberately omitted.

## Supported scope and limitation

Codex 0.157.1 reports child thread identities and parent relationships, and its
app-server supports targeted `turn/interrupt` with a thread and turn precondition.
These are separate from the aggregate working/completed/attention counts, which
remain unchanged. Older Apps without `subagents_v1` hide individual controls.

Direct follow-up is **unavailable** in this implementation. The pinned native
multi-agent v2 runtime explicitly rejects direct app-server input to child
threads, including `turn/steer` and `turn/start`. Transcript operation names such
as `followupTask` are not client RPC methods. The Bridge does not simulate
follow-up through a parent prompt, desktop orchestration or creation of another
child. An experimental direct-input flag is absent from the stable committed
protocol projection; its absence does not grant permission. See the pinned
[input guard](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/app-server/src/request_processors/thread_input.rs)
and [turn processor](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/app-server/src/request_processors/turn_processor.rs).
This limitation means the full requested follow-up outcome is not delivered.
CB-011 must remain open and unqualified until its agreed acceptance is met.

## Data and operational boundary

The private projection retains at most 256 child records overall, 64 per parent
and 256 stop receipts. Its payload is capped at 4 MiB and its SQLite database at
8 MiB. When overall retention fills, older terminal records are removed first;
active rows are not evicted to admit unbounded fan-out. Older accepted stop
receipts can expire; an old revision still cannot be replayed against a changed
child. Uncertain receipts are retained and filling that limit blocks new child
controls, without affecting the parent. A parent at its limit
retains its first 64 children. This is a bounded retained view, not a complete
historical audit. No periodic task, scheduler or automatic follow-up is added.

Public task, nickname and terminal message previews use the existing conservative
credential/control-character filter and a 2,000-byte bound. Recognised sensitive
text is omitted; this filter is not a guarantee of finding every secret.
Only public final-answer items can supply an inspected result. Hidden reasoning,
tool output, agent paths, native IDs and account ownership markers are excluded
from browser responses. Native identity and control receipts remain in private
Bridge state. The browser communicates only with administrator-authenticated
Home Assistant, which proxies capability-gated requests to the private Bridge.
Authorised chat/project deletion purges the matching private child records and
stop receipts. If that private history cannot be updated, deletion fails rather
than claiming that all retained data was removed.

No native installation or live isolation acceptance is established by source
review or schema-backed tests. The coordinator owns that separate DEV check.
