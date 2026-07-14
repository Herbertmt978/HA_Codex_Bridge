# Migrating from an external Bridge

This guide applies to any separately operated private Bridge. The Supervisor
App is the intended runtime for new work, but an external Bridge remains an
optional compatibility and recovery path while the App is evaluated. It is not
required for a new Home Assistant deployment.

The App starts with fresh Home Assistant chat history by design. Do not import
external Bridge conversations, project state, Codex login state, or credentials.

## Safe migration sequence

1. Keep the external Bridge stopped or private while evaluating the App; do not
   expose either runtime to a browser.
2. Copy only deliberately selected workspace files into a new reviewed App
   workspace below `/config/workspaces`. Do not copy Codex homes, tokens,
   cookies, Bridge state, or host configuration files.
3. Install the Integration and, when available, its matching App image as
   described in [installation](installation.md).
4. In the Home Assistant panel, select **Sign in with ChatGPT** and complete a
   new approved ChatGPT device-auth flow. No OpenAI API key is needed.
5. Start a new Project, confirm its workspace and effective model/reasoning
   settings, then test a small reversible task.

## Recovery during cutover

Do not run the App and external Bridge against the same mutable workspace.
Keep the external Bridge's workspace data and operating notes until the cutover
is proven in your environment. If the App is unavailable or reports
`sandbox_unavailable`, stop it and use the private external Bridge or restore a
cold backup.

Do not rely on selecting a prior App image through Supervisor: that rollback
path is not yet validated. It becomes a supported recovery option only after a
prior immutable tag and restore procedure are published and tested.

## Windows appendix

A Windows VM is one optional legacy way to run an external Bridge. Keep it
private and treat it like any other external Bridge in this guide. Do not create
or retain a Windows VM solely because the Supervisor App is being evaluated;
retire it only after your own migration and recovery criteria are met.
