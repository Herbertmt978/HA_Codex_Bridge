# Backup and recovery

## What to protect

The App stores workspaces below `/config/workspaces` and private Bridge and
ChatGPT device-login state in its App-private data volume. Backups can contain
source code, workspace secrets, and login-related private state; treat them as
sensitive material.

## Current recovery plan

Prepare a cold Home Assistant backup and, where one is already operated, retain
a private external Bridge. This cold restore runbook has not yet completed
release acceptance, so test it on the intended Home Assistant installation
before relying on it. App-image rollback is also not validated. Do not assume
Supervisor can select an arbitrary earlier App image until a prior immutable
App tag and its restore procedure are published and tested.

## Create a cold backup

1. Finish or cancel active Codex work and note the App and Integration versions.
2. Stop the Codex Bridge App.
3. Create a Home Assistant backup that includes the App and its data, using the
   supported Home Assistant backup process for the installation.
4. Confirm completion and keep a second copy in a location you control.
5. Start the App and confirm readiness before resuming work.

Make a cold backup before an App change, a workspace-layout change, or a host
migration.

## Restore safely

1. Stop the App on the target Home Assistant installation.
2. Restore the selected cold backup through Home Assistant's supported restore
   workflow.
3. Start the App and check readiness. If it reports `sandbox_unavailable`, do
   not loosen permissions; retain redacted diagnostics and stop the rollout.
4. Open the administrator panel and verify the ChatGPT session. Be prepared to
   select **Sign in with ChatGPT** again rather than copying credentials.
5. Inspect the restored workspace before asking Codex to change it.

Removing the Integration or App intentionally does not remove workspace files.
Review and back up `/config/workspaces` before cleanup. For switching from an
existing private Bridge, see [external-Bridge migration](migration-from-windows.md).
