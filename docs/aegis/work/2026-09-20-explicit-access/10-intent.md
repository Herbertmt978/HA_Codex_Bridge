# Explicit access environments

Anthony has authorised implementation of issue #91 as the next optional
capability, controlled from Home Assistant. Before enabling it, users must see
an accurate warning explaining exactly what Codex can access.

## Scope and acceptance

The default workspace modes remain unchanged. An HA administrator explicitly
configures and enables broader access, and a chat or scheduled task explicitly
selects it. The server owns the effective grant and its current revision. A
client-side checkbox or a model instruction cannot confer access.

The warning must identify the execution machine and account, readable and
writable locations, command privileges, network access and sensitive data that
can be exposed. It must explain that returned file contents and command output
can be sent to the Codex model, that permitted network operations can transmit
data, and that stopping work does not undo completed changes. Scheduled tasks
need explicit permission for unattended execution and must fail closed when an
operation requires an unanswered approval.

Verify the permitted operations and nearby denials, stale or missing consent,
changed environment configuration, revocation, cancellation, bounded resource
use and restart recovery. Test compatibility with older Apps and Integrations,
keyboard and mobile access to the warning, and native HAOS-DEV behaviour before
release. Anthony explicitly limited installation and testing of the extra App
to HAOS-DEV. Do not install it or enable host access on production HA: this
feature is being developed for other users.

Selecting host full access in the Bridge UI must show the warning. An installed,
ready App leads directly to acknowledgement and enablement; a missing or
unavailable App also shows a link to installation instructions.

## Baseline and impact

Read: issue #91 and its acceptance criteria; AGENTS.md; CONTEXT.md; App config;
runtime_policy.py; feature_capabilities.py; thread routes; browser dynamic-tool
entry points; ADR 0006; panel-settings.md; home-operations and release rules.

This changes a security boundary, durable grants, run admission, scheduled
execution, capability negotiation and the administrator interface. The browser
continues to communicate only with HA. The bounded browser worker from #43
retains its existing restrictions. Existing installations gain no permissions
on upgrade. Credentials and control-plane state remain separate from model-
accessible data wherever the selected execution design can enforce that.

Anthony selected the Home Assistant OS machine itself. The implementation will
use a separately installed Host Access App, with host privileges enabled only
after explicit administrator action. The normal Bridge remains unchanged for
users who do not install it. A claim of access to HAOS must mean the host
namespace, not merely access inside the App container.

Root access intentionally includes HA configuration, private app data and any
credentials accessible on the host. Those credentials may confer further
permissions, including Proxmox access. The warning must disclose this; it must
not promise that private tokens remain protected from an authorised host-root
command. Seeing a Proxmox integration alone does not imply a VM shell.

## Working baseline

The task branch is Herb/explicit-access-environments, based on published 1.0.6.
Production remains on verified 1.0.6. No live broader permission is enabled.
DEV103 is currently shared with HA Growatt qualification; coordinate before
native changes. Temporary build output belongs on fixed D:, and local Docker
must not be started or restarted by Codex. Local HA backup retention is three,
including automatic, manual and update backups, after any temporary task excess
has been cleaned up.
