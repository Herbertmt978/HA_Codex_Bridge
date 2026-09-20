# Evidence

20 September 2026. Published 1.0.6 is the baseline; 1.1.0 is an unpublished
candidate for issue #91. Production has not received the Host Access App.

## Local checks

- Frontend: lint, 364 unit tests and the generated panel build passed.
- Browser: 35 Playwright checks passed, including desktop/mobile host warnings,
  keyboard behaviour and accessibility. A low-contrast installation link was
  corrected during these checks.
- Linux worker: 333 Home Assistant tests, 1,816 Bridge tests and eight separate
  root restore checks passed. The Bridge run skipped 27 platform/root-specific
  cases; the root restore suite was run separately.
- Workflow validation: actionlint and zizmor passed. The runtime updater's
  generation, artifact and PR allowlists now include the companion's version
  and changelog projections, with a regression check.
- Hassfest, reverse-proxy transport checks, release projection synchronisation
  and Codex lock validation passed.
- The final amd64 candidate image built locally after all functional corrections.
  Host Access refuses to start in an ordinary, unprivileged Docker container.
- The final Linux suites include native-discovery, tool-contract, lifecycle and
  durable-revocation corrections. Ruff, release synchronisation and lock checks
  passed again after the native tests.

## Native DEV qualification

- HAOS-DEV has a new rollback snapshot. Its existing three HA backups remain.
- Candidate Integration 1.1.0 passes the HA configuration check and restarts.
- The separate companion installs through Supervisor with no public ports.
- Native testing found missing namespace capabilities and rejection of HAOS
  development version strings. Both are corrected and covered by checks.
- HA adds an App display name to Supervisor discovery data. The companion
  validator now removes that presentation field before validating the private
  contract. Native discovery pairs without granting access.
- An unchecked acknowledgement is rejected; explicit acknowledgement creates
  a DEV grant and permits selecting the host mode.
- A real Codex turn ran a bounded root command that created, read and removed
  a task-owned file, identified the DEV host and reached an HTTPS website.
- HA dispatched an acknowledged one-off scheduled task after Core restart and
  scheduler reconciliation. Its host command and run completed successfully.
- Stop and Revoke each interrupted a tracked sleeping command. Revocation
  rejected new chat work and blocked the saved unattended task.
- Restarting the companion rejected a request using the previous execution
  session and did not restore revoked consent. An ordinary workspace chat then
  completed without host tools.
- Native testing also caught a missing nested function-type field in the Codex
  tool definition. The corrected contract passed the actual provider turn.

The native fixture uses the built image's root filesystem and startup
configuration, loaded locally through Supervisor. Signed-image acceptance is
still required after publication. DEV-only fixture packaging is not committed.

## HA-MCP recommendation

The installation documentation and Settings MCP page link to the optional
community HA-MCP server. The guide explains the current HTTPS/streamable-HTTP
requirements, secret-URL handling, optional permissions and separation from root
host access. No HA-MCP installation or new connection is performed by this task.

Sources checked on 20 September 2026:

- https://github.com/homeassistant-ai/ha-mcp
- https://github.com/homeassistant-ai/ha-mcp/blob/master/docs/in-process-server.md
- https://developers.home-assistant.io/docs/apps/configuration/

Detailed local logs and temporary native fixtures are retained outside the
repository. Credentials and private endpoints are excluded from this record.
