# SHARE-01: Public read-only chat snapshots

Status: Planned. Version and date: unassigned.

Tracking issue: [#95](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/95).
This continues the existing public-snapshot proposal.

## Problem and outcome

Current Share links require Home Assistant administrator sign-in. Offer an
optional, fixed public snapshot without exposing the live chat or HA instance.

## Scope

- Choose a hosting model, costs, retention, expiry and abuse controls first.
- Preview the selected content and obtain explicit publication confirmation.
- Exclude credentials, private metadata, local paths, tool output and attachments
  unless a separate reviewed flow permits the relevant content.
- Support revocation and deletion, explaining that readers may retain copies.

## Acceptance criteria

- Later chat activity cannot appear in an existing snapshot.
- Anonymous readers cannot reach HA, the App, private APIs or unselected content.
- Tests cover sanitisation, malicious content, expiry, deletion and access checks.
- Public sharing is disabled by default and no content is published by upgrading.

## Dependencies and boundary

Requires the hosting/privacy design already requested in issue #95. Automatic
redaction alone is not a publication decision. The existing authenticated Share
link remains available independently of this future service.
