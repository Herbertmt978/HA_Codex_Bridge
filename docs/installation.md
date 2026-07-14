# Installation

## Status before you start

The Supervisor App is experimental and `amd64` only. Its source is in this
repository, but a public App image is not available yet. A private immutable
image running Codex `0.144.4` passed sandbox self-test and authenticated
readiness on an amd64 Home Assistant OS development VM on 14 July 2026. This
does not validate public distribution, remote access, updates, or App-image
rollback.

Codex Bridge has two separate surfaces:

1. The **Integration** is installed in Home Assistant and owns the
   administrator panel.
2. The private **App** runs the Bridge and Codex through Supervisor.

The Integration can be installed as a HACS custom repository. This does not
imply a HACS or Home Assistant listing, review, endorsement, or support. The
future public App repository is <https://github.com/Herbertmt978/ha-codex-bridge>.

## Prerequisites

- Home Assistant Operating System on `amd64`, with administrator access. Home
  Assistant Container does not provide Apps and cannot use this Supervisor App.
- A ChatGPT account that can use Codex. Device login does not use an OpenAI API
  key.
- A small, non-sensitive project directory you are comfortable letting Codex
  read and change.
- A recovery plan: make a cold backup and, if you already operate one, keep a
  private external Bridge available during evaluation. A Windows VM is optional
  legacy external-Bridge infrastructure, not a requirement.

## Install the Integration

1. In HACS, add this repository as a custom repository with category
   **Integration**.
2. Install **Codex Bridge** and restart Home Assistant.
3. Open **Settings -> Devices & services**, select **Add integration**, and add
   **Codex Bridge**.

The HACS link in the [repository README](../README.md) installs only the
Integration. It neither installs nor publishes an App image.

## Install the App

When a matching versioned App image is published, open **Settings -> Apps ->
App store**, select the three-dot menu, then **Repositories**. Add
<https://github.com/Herbertmt978/ha-codex-bridge>, install **Codex Bridge**, and
start it. Use matching Integration and App release notes.

Until then, [`codex_bridge_app`](../codex_bridge_app) is a controlled
source/evaluation workflow for contributors with a Home Assistant development
environment. It is not a general copy-and-paste installation method. The App
has no supported ingress route, direct port, or browser-visible Bridge URL;
Supervisor discovery supplies the private connection.

## First run

1. Confirm the App reports ready. If it reports `sandbox_unavailable`, stop:
   do not weaken its sandbox or broaden mounts.
2. Open the Codex Bridge panel as a Home Assistant administrator.
3. Select **Sign in with ChatGPT**, then complete the displayed approved
   ChatGPT device-auth page in a browser signed in to the intended account.
4. Wait for the connected state. Home Assistant and ChatGPT login are separate
   sessions. **Cancel** stops only an active sign-in; **Sign out** removes an
   established session.
5. Create a Project and grant a small workspace below `/config/workspaces`.

After connection, normal panel use can remain on Home Assistant. Initial
sign-in and re-authentication require browser access to the approved ChatGPT
device-auth page.

## After installation

- Read [remote access](remote-access.md) before exposing Home Assistant
  remotely.
- Make a cold backup before an App change; see
  [backup and recovery](backup-restore.md).
- Never paste device codes, cookies, bearer tokens, or API keys into App
  settings.
- See [SUPPORT.md](../SUPPORT.md) and [SECURITY.md](../SECURITY.md).
