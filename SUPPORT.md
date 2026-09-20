# Support

## Where to ask

Use [GitHub issues](https://github.com/Herbertmt978/HA_Codex_Bridge/issues) for
bugs, feature requests and setup questions. Check the
[installation guide](docs/installation.md) and the quick checks below first.
For a suspected vulnerability, use [private security reporting](SECURITY.md).

## Include this information

Tell us your Home Assistant version and installation type, the App and HACS
Integration versions, and whether you use the Supervisor App or an external
Bridge. Describe the steps, what you expected and what happened. Include a
screenshot or short redacted log excerpt when useful.

Do not include tokens, device codes, cookies, credentials, private workspace
contents or complete private paths.

## Fast checks

| Problem | What to check |
| --- | --- |
| No update appears | Check the GitHub release has been published. Refresh HACS and the App store separately. The panel needs a HACS update; Codex needs an App update. See [update steps](docs/installation.md#update-an-existing-installation). |
| The old UI is still visible | Restart Home Assistant after updating the Integration, then reload open panel tabs. A page refresh alone does not install an update. |
| Sign-in expired | Start **Sign in with ChatGPT** again in the panel. Home Assistant login does not renew the separate ChatGPT session. |
| Astra or another model is missing | Update the App, confirm ChatGPT is connected and let the catalogue refresh. Available models and reasoning levels depend on the installed runtime and account. |
| Plugins are unavailable | Update both components and retry after Codex is ready. The larger catalogue fix is included from `1.0.3`. If it persists, provide the versions and redacted error. |
| Menus close or hover repeatedly flashes | Update the HACS Integration to `1.0.4` or newer and reload the panel. `1.0.5` also prevents Enter in a schedule dropdown from submitting the form. |
| A scheduled task did not run | Check **Runs**, the task's paused state, Home Assistant and App availability, and ChatGPT login. Overlaps, missed windows, capacity limits or an approval request can prevent a run. |
| A schedule runs at the wrong time | Check Home Assistant's time zone and the form preview. Fixed intervals measure elapsed time; daily and weekly schedules follow local time. |
| App reports `sandbox_unavailable` | Keep the failure intact and collect redacted logs. Do not weaken the sandbox or add broad mounts. |
| Browser automation is unavailable | This is expected. The App-owned browser worker is disabled pending the isolation work in [issue #43](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/43). |

Use Home Assistant's URL, not a direct App or Bridge address. Initial ChatGPT
sign-in requires browser access to the approved ChatGPT page. For current facts,
check run activity for an actual web search; a plausible answer alone does not
prove it used live information.

See [App documentation](codex_bridge_app/DOCS.md), [Scheduled tasks](docs/scheduled-tasks.md)
and [Backup and recovery](docs/backup-restore.md) for more detail.
