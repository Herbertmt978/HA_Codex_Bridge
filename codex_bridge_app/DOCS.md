# Codex Bridge App documentation

## Runtime boundary

The App hosts the local Bridge process. Home Assistant is the only client-facing
boundary: the Integration authenticates users, authorizes workspace operations,
and proxies requests to the Bridge over the Supervisor-managed App network.

The App deliberately does not request host networking, ingress, Docker access,
devices, Home Assistant configuration, `/share`, or broad Supervisor roles.

## Storage and backups

Bridge state and ChatGPT device-login credentials live in the App-private
`/data` volume. User-visible workspaces live only under `/config/workspaces` in
the dedicated `addon_config:rw` mapping. Home Assistant stops the App for a
consistent cold backup; the App never requests access to Home Assistant's main
configuration or another App's data.

## Updates

The App image is immutable and is updated by the Home Assistant Supervisor when a
new repository version is published. Review the release notes before upgrading.

## Support and licence

Report problems through the repository's GitHub issue tracker. Codex Bridge is
open source under the MIT License; see the repository root `LICENSE` file.
