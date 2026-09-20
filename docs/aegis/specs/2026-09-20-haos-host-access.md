# Optional Home Assistant OS host access

Status: authorised implementation design for issue #91, 20 September 2026.

## Decision

Add a separately installed **Codex Host Access** Supervisor App. The existing
Codex Bridge App retains its current permissions and workspace sandbox. Both
roles use the same signed, immutable amd64 image, with a fixed startup role in
each App's configuration. Host Access requires an explicit Supervisor privilege
change; the normal App cannot enable it by changing a chat setting.

Host Access receives authenticated private requests from the Bridge. Supervisor
discovery supplies its private endpoint and generated credential to the HA
Integration, which pairs it with the Bridge over the existing authenticated
channel. The panel receives neither endpoint credentials nor a direct worker
connection. No new host port or browser-facing listener is published.

After startup proves it can enter the HAOS host namespaces, the companion
advertises a host identity and a versioned disclosure. A failed proof exposes
no executable capability. The HA Settings page shows the complete disclosure,
requires an unchecked acknowledgement and an explicit enable action, and binds
the saved grant to the current companion identity and disclosure version.
Replacement, incompatible version or lost pairing invalidates the grant.

Chats and schedules choose **Full access · Home Assistant OS** separately from
the existing workspace modes. A fresh provider session receives the `ha_host`
tool only when that particular run has a valid grant. An existing session must
not gain a new tool silently, and changing away from host access detaches its
old provider session. Every callback must match the current run, turn, provider
generation and grant, with at-most-once call handling. Root commands run through
the companion; the ordinary Codex shell stays workspace-confined.

## What users must be told

The warning names the detected Home Assistant OS machine and states:

- Codex can run commands as root on this HAOS machine, install or run software,
  manage containers and services, and restart or stop Home Assistant.
- It can read, change or delete host files, including HA configuration, app data,
  backups and files on storage mounted to this machine. This includes secrets,
  integration tokens and saved sign-in credentials accessible to root.
- It can use the host's internet and local-network connectivity. Other systems,
  including Proxmox or another VM, may be reachable with permissions from stored
  credentials. This does not independently grant an account on those systems.
- File contents and command output returned to Codex can be sent to the model
  provider. Network commands can send data to other services.
- Incorrect instructions or malicious content encountered during work could
  cause data loss, expose credentials or interrupt household automations.
- Stop/revoke blocks further Bridge requests and attempts to stop tracked work.
  It cannot undo completed changes. Host-root commands can alter the controls,
  start persistent work or affect HA itself; local isolation cannot prevent
  this while also granting host-root access.

Use **Enable host access** as the action label, not a generic Continue button.
Keep Cancel available and put the explanation in the visible page/dialog body,
not solely in a tooltip. Use a separate explicit acknowledgement when a schedule
will run with these rights while the user is absent. Persist the chosen access
mode visibly in the chat and scheduled-task details.

## Execution and recovery

The companion runs with explicitly declared host PID, full hardware access,
and the SYS_ADMIN, SYS_PTRACE and DAC_READ_SEARCH capabilities needed to enter
the host environment. Protection mode and AppArmor are disabled for this App
only. A fixed role
entrypoint bypasses S6, which does not support host PID. A short-lived child joins
the HAOS mount/network/UTS/IPC namespaces and changes root before running a shell.
The web server stays on the private App network. No Docker socket or host port
is exposed. Each command has bounded input, output and duration, and a tracked
process group. These are operational controls, not containment against an
authorised root command, which can undo local controls.

Revoke prevents new admissions before cancellation begins. The worker rejects
expired/replayed requests, and the broker rejects stale or unsolicited runtime
callbacks. Each worker start has a fresh execution session; old requests cannot
execute after restart. Shutdown attempts to terminate only tracked command
groups, and interrupted Bridge runs cannot regain authority on restart. No
arbitrary container, host process or existing HA service is swept by cleanup.

Anthony requires installation and native testing on HAOS-DEV only. Production
must not receive the companion or an enabled host grant. The UI must explain
the privileges when the option is chosen. If the App is installed and ready,
show the warning and enable action directly. Show the installation link only
when the App is missing or unavailable.

Scheduled tasks are admitted only with a current host grant and their own saved
unattended acknowledgement. They never wait invisibly for interactive input.
A disabled/replaced worker, revoked grant or mismatched consent produces a
readable terminal failure rather than a fallback to another access mode.

## Alternatives

A dedicated worker VM would provide a stronger boundary around the household
system, but Anthony explicitly selected HAOS as the target for this feature.
Adding root privileges to the existing App would enlarge every installation's
permission surface; the separate companion keeps that change opt-in. Merely
setting Codex's sandbox to an unrestricted mode inside the App would not provide
honest host access and would expose its credentials without a clear boundary.

## Required evidence

Test authentication, host identity and capability negotiation; unchanged normal
App privileges; missing/stale/revoked grant denial; selected-mode run admission;
thread/turn/generation/call correlation; schedule acknowledgement and unattended
failure; bounded output/time; cancellation; restart cleanup; UI keyboard/mobile
behaviour; and an actual isolated HAOS-DEV host command that reads host identity,
creates and removes a task-owned probe file, and leaves original services intact.
Never use production household controls as a test fixture. Complete normal local,
PR, signed-image and target acceptance checks before release claims.
