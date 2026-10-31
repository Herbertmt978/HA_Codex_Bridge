# Saved-account allowance sensors

On App versions that advertise saved-account telemetry, Home Assistant creates
a service device for each account in the account switcher, named with the
account's safe display label and shown under the Codex Bridge connection
device, and adds diagnostic sensors to it. Renaming the
account in Codex Bridge renames the device unless you have named it in Home
Assistant. Each account has five-hour and weekly
used and remaining percentages, reset timestamps, available reset credits, and
the next known reset-credit expiry. A reset credit is only reported; these
sensors never use one.

The Bridge refreshes saved-account snapshots in its existing background poller.
Home Assistant reads that cached projection once a minute and never switches
accounts or starts a provider sign-in to update a sensor. Reset timestamps use
Home Assistant's timestamp format and can be used directly in dashboard cards
or time-based automations. For example, a numeric sensor can trigger when its
state changes, while a timestamp sensor can be used with a time trigger.

Missing or disabled windows, values outside the reported bounds, stale
snapshots, accounts without reported usage and accounts requiring
reauthentication are unknown. If the App cannot answer, every account sensor is
unavailable until it can. An incomplete credit inventory may show the next
**known** expiry, with `expiry_complete: false`; it does not establish the
earliest expiry of every available credit. Zero available credits is a reported
value; an absent credit count is unknown. They are not treated as zero.
The allowance status sensor is an enum sensor with the states `available`,
`stale`, `usage_unavailable` and `reauthentication_required`. Its
`telemetry_status`, `last_updated`, `data_age_seconds` and `last_refresh`
attributes describe the retained data. The reset-credit sensor also reports
whether the expiry inventory is complete. The existing Integration-wide sensors
and their five-hour visibility choice remain unchanged.

When the App reports that a saved account needs a fresh sign-in, Home Assistant
raises a Repairs issue named after the account. To fix it, open the account
menu in Codex Bridge, choose **Add another account**, sign in to that account
and save it again. The issue closes on the next fresh reading that reports the
account as signed in, or when the account is removed. An App outage or restart,
or a stale or unreported reading, leaves it open, and keeps it ignored if you
chose to ignore it. Removing the Integration deletes it.

Account entities keep a stable Home Assistant registry identity when an
account is renamed, reordered or selected as active. A complete profile
inventory removes the device and entities of an account that was removed. An unavailable or
incomplete inventory preserves existing registry entries until the Bridge can
confirm the account list again. Older App versions without the telemetry
capability do not create per-account entities.

Five-hour sensors follow the existing reversible visibility policy. A confirmed
weekly-only allowance hides them; stale or missing telemetry does not change
visibility. Home Assistant names, disabled settings and manual hide/unhide
choices survive account switching and reload. The same account retains its
entity IDs; adding a new account creates new diagnostics on the next refresh.

For example, select an account's **weekly reset** sensor as the entity in a
Home Assistant time trigger to run at the reported reset timestamp. Only use
an available timestamp, and check the account's allowance status when deciding
whether a dashboard or automation should act on a cached reading. Reset credit
expiry is informational and never redeems a credit.
