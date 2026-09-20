# Checkpoint

## Current scope

Implement issue #91 as optional, separately installed HAOS root access. Anthony
requires DEV-only installation/testing here; production must never receive the
companion or an enabled grant as part of this task. An installed, ready App shows
the warning and acknowledgement directly. A missing/unavailable App shows setup
instructions. Scheduled work requires its own unattended acknowledgement.

Latest request: recommend HA-MCP and link its setup instructions from installation
docs and Settings. Implemented, with Bridge's existing HTTPS restrictions stated.
Do not install or enable a new MCP connection merely to document the option.

## State

Branch: Herb/explicit-access-environments, based on published 1.0.6.
App/Integration/panel 1.1.0 and Bridge 0.8.0 remain unpublished candidates.
Implementation, docs and unit/browser checks are present. See 90-evidence.md for
results and remaining native acceptance work. All feature files are uncommitted.

Native DEV installation found and corrected the companion's Linux capabilities,
HAOS development version validation, and HA's added discovery display-name field.
Discovery now pairs automatically without enabling access. Explicit acknowledgement
was tested. A missing nested function type in the Codex tool definition was
found through the native provider test and corrected. The DEV diagnostic has
been removed. Actual root file/network commands, HA-scheduled execution, Stop,
Revoke, post-revocation denial, worker restart and an ordinary workspace chat
after revocation now pass. No host grant remains enabled.

## Resources and cleanup

This task owns final graceful shutdown of HAOS-DEV VM103 and Linux worker CT105,
both initially stopped. DEV has a rollback snapshot and retains three HA backups.
The original normal Bridge App 1.0.6 is stopped with auto-update paused while a
private 1.1.0 candidate uses its DEV data. The companion is installed on DEV only.
Restore normal DEV service and settings, remove all host test records/grants,
uninstall the companion, remove the owned candidate/profile/file server and
return both guests to stopped after testing. Preserve unrelated services.

The canonical home map has concurrent updates: re-read its latest version before
recording this work. No GitHub action has yet published this feature. Finish local
checks and native acceptance before pushing; obey the standing posting intervals.
