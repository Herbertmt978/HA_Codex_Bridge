# Saved-account allowance sensors

On App versions that advertise saved-account telemetry, Home Assistant creates
diagnostic sensors for each account in the account switcher. Their names begin
with the account's safe display label. Each account has five-hour and weekly
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
snapshots, unavailable Bridge responses and accounts requiring reauthentication
remain unknown or unavailable. An incomplete credit inventory may show the
next **known** expiry, with `expiry_complete: false`; it does not establish the
earliest expiry of every available credit. Zero available credits is a reported
value; an absent credit count remains unavailable. They are not treated as zero.
The allowance status sensor reports freshness and source failures. Its
`telemetry_status`, `last_updated`, `data_age_seconds`, `last_refresh` and
`source_available` attributes describe retained data even during a source
outage. Home Assistant omits extra attributes from unavailable numeric and
timestamp entities. The reset-credit sensor also reports whether the expiry
inventory is complete while it is available. The existing Integration-
wide sensors and their five-hour visibility choice remain unchanged.

Account entities keep a stable Home Assistant registry identity when an
account is renamed, reordered or selected as active. A complete profile
inventory removes entities for accounts that were removed. An unavailable or
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
