# Home Assistant task actions

Codex Bridge provides four native Home Assistant actions on a paired App that
advertises `task_actions_v1`:

| Action | Purpose | Response |
| --- | --- | --- |
| `codex_bridge.start_task` | Start a new chat in an existing project | Task, chat and run IDs with current status |
| `codex_bridge.continue_task` | Start an unattended turn in an idle chat | Task, chat and run IDs with current status |
| `codex_bridge.cancel_task` | Cancel the exact run belonging to a task ID | Updated task reference |
| `codex_bridge.get_task` | Read the durable status of a task ID | Current or terminal task reference |

These actions use the same private Integration-to-App connection as the panel.
They do not expose the Bridge to the browser or network. They require an HA
administrator context. HA scripts and automations normally have no user context;
enable **Allow unattended task actions from automations** in the Integration
options before using them there. This option is off by default. Treat scripts
that use it as trusted administrative automation. Non-admin user contexts are
rejected, even when the option is enabled.

`start_task` takes `project_id`, `title`, `prompt`, optional `mode` (`observe`,
`edit`, or `full-auto`), and optional `model_override` and
`thinking_override`. Use the project ID from the Bridge project you intend to
work in. `continue_task` takes `thread_id` and `prompt`; it uses that chat's
existing mode and model. Both actions accept an optional 32-character lowercase
hexadecimal `task_id`. Reuse the same ID only when retrying the *same* request.
If omitted, the Integration generates a new ID for each action, including
multiple actions in the same script run. Supply the original ID explicitly
when retrying a request. A changed request under an existing ID is rejected
rather than run again.

For example, an HA script may call:

```yaml
sequence:
  - action: codex_bridge.start_task
    data:
      project_id: prj_example
      title: Check the dashboard
      prompt: Review the dashboard and report actionable problems.
      mode: observe
    response_variable: codex_task
  - action: codex_bridge.get_task
    data:
      task_id: "{{ codex_task.task_id }}"
    response_variable: codex_status
```

The start response means the turn was accepted, not that it completed. The
returned `task_id`, `thread_id` and `run_id` are opaque references; `status` is
one of `queued`, `starting`, `running`, `cancelling`, `completed`, `failed`, `cancelled`, or
`interrupted`. Use `get_task` to recover state after an HA restart or a missed
event. `cancel_task` is bound to that task's run; calling it after a later turn
starts does not cancel the later turn.

The Integration publishes these HA bus events:

| Event | Data |
| --- | --- |
| `codex_bridge_task_accepted` | `task_id`, `thread_id`, `run_id`, `status` |
| `codex_bridge_task_interaction_needed` | `task_id`, `thread_id`, `run_id`, `kind` (`question` or `approval`) |
| `codex_bridge_task_result` | `task_id`, `thread_id`, `run_id`, terminal `status` |

Events never include prompt text, result text, tool output, credentials or
workspace paths. An unattended task cannot approve an interactive request; the
Bridge declines it and reports the lifecycle event. The Integration records a
receipt before publishing an HA bus event, so a restart does not cause the
same event to trigger an automation twice. A stop between receipt and publish
can omit a bus event; `get_task` and the Bridge chat remain the durable source
of status. Event-history expiry can also require querying `get_task`.

Task actions use the existing workspace boundary and run admission checks.
They cannot enable Home Assistant OS host access, including when continuing an
existing host-access chat. An archived target, busy chat, unavailable provider
or invalid model/effort fails without starting a second run. Configure the
project and any permitted workspace access in the Bridge first.
