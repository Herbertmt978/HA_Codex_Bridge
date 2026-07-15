# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub private vulnerability
reporting](https://github.com/Herbertmt978/HA_Codex_Bridge/security/advisories/new).
If that private form is unavailable, contact the maintainer through the
repository's published GitHub profile and request a private reporting channel.

Do not open a public issue for an unpatched vulnerability or include device
codes, bearer tokens, cookies, ChatGPT credentials, API keys, private workspace
contents, or full authorization headers.

Include a concise impact statement, affected component/version, reproducible
steps using synthetic data, expected and observed behavior, and a suggested
mitigation where safe. We will acknowledge reports when a private channel is
available and coordinate a fix and disclosure timeline with the reporter.

## Security boundaries

The browser talks to Home Assistant, and Home Assistant talks privately to the
Bridge. Do not expose the App or Bridge as a browser endpoint. Use Home
Assistant's supported LAN, VPN, Nabu Casa, Cloudflare, or HTTPS reverse-proxy
access path, terminating that route at Home Assistant rather than proxying it
through to the App or Bridge.

The App uses ChatGPT device login and does not use an OpenAI API key. In App
mode, keep workspaces under `/config/workspaces`; do not mount Home Assistant
configuration, host filesystems, or broad shares. The App fails closed when its
tool-sandbox attestation is unavailable. Do not weaken AppArmor, container
permissions, or network restrictions to bypass it.

The release being shipped (Integration `0.6.6`, App `0.6.6`, Bridge `0.5.5`,
Codex `0.144.4`) is experimental and `amd64` only; it is pending publication,
signing, and target-Home-Assistant acceptance. The signed, live-accepted
`0.6.5` matrix remains historical evidence and does not accept `0.6.6`.
Arbitrary prior-image selection is not a validated Supervisor rollback
mechanism. Until an update and restore canary is complete, recover with a cold
backup or an existing private external Bridge.

## Scope notes

The project can investigate vulnerabilities in the Integration, Bridge, App
source, image build inputs, and documented deployment boundary. Report issues
in Home Assistant, Nabu Casa, Cloudflare, ChatGPT, OpenAI, or other upstream
products to their respective security processes as well.
