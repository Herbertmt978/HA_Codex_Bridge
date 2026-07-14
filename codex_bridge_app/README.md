# Codex Bridge App

The Codex Bridge App runs the private Bridge service alongside Home Assistant.
The companion `codex_bridge` Integration exposes the panel and keeps all browser
traffic inside the Home Assistant connection.

## Install

1. Add this repository to Home Assistant App repositories.
2. Install **Codex Bridge** from the App store.
3. Start the App and configure the `codex_bridge` Integration when discovery is offered.

The App uses the proven `amd64` Home Assistant target for this release. It has no
published port or ingress surface; access is provided by the Integration through
Home Assistant, Nabu Casa, or another reverse proxy in front of Home Assistant.

The only explicit writable host mapping is this App's dedicated
`addon_config` directory, mounted at `/config`. Workspaces created or imported
through the Integration live under `/config/workspaces`; the App cannot read
Home Assistant's main configuration or other Apps' data.

## Authentication

Sign in from the Integration panel using the Codex device-login flow. Credentials
remain in the App-private `/data` volume and are never entered into Home
Assistant App options or a browser URL.
