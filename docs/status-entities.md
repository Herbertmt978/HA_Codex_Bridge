# Home Assistant status entities

HA-04 adds eight entities to an App-backed Codex Bridge Integration. They use
the existing private Bridge connection. The browser and Home Assistant do not
connect directly to Codex or an MCP server.

| Entity | Home Assistant type | Meaning |
| --- | --- | --- |
| Connection | Diagnostic binary sensor | On after a successful Bridge status and thread refresh; off if the App cannot be reached. |
| ChatGPT connected | Diagnostic binary sensor | On only while the Bridge reports a verified ChatGPT sign-in. |
| Task running | Binary sensor | On while at least one non-archived chat reports a running turn. Use this in HA automations. |
| Last task outcome | Sensor | Most recent completed, failed, cancelled or interrupted turn observed by this Integration since it started. Unavailable until an outcome is observed. |
| 5-hour usage | Diagnostic percentage sensor | Reported percentage used in an explicitly identified 300-minute window. |
| Weekly usage | Diagnostic percentage sensor | Reported percentage used in an explicitly identified 10,080-minute window. |
| 5-hour reset | Diagnostic timestamp sensor | Reported reset time for the 300-minute window, in UTC. |
| Weekly reset | Diagnostic timestamp sensor | Reported reset time for the 10,080-minute window, in UTC. |

Usage sensors become unavailable when the reported window is absent, unlimited,
older than 15 minutes, or older than a sign-in/account change. They do not
turn missing values into zeroes. The connection sensor turns off on an App
outage; the other sensors become unavailable. A Bridge using the legacy v0 API
does not expose these entities.

The Integration refreshes the entities together every minute and when relevant
events arrive through its existing single event broker. The last outcome is an
in-memory observation and resets to unavailable after an Integration reload or
sign-in change;
the running state is read afresh from the Bridge. Home Assistant gives each
entity a stable registry ID tied to the configuration entry. No prompts, chat
text, account identifiers, email addresses, tokens or raw Bridge responses are
stored in entity state or attributes.

Home Assistant's recorder may store the entity states and their history. Usage
percentages are rounded to one decimal place to avoid a record for every small
measurement change. Reset times and diagnostic states change only when their
reported values change. To exclude these from recorder, use Home Assistant's
normal recorder configuration for the relevant entity IDs.
