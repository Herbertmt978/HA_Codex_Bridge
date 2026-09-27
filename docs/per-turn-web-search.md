# Per-turn web search

The composer shows the effective search mode for the next prompt. **Use configured
setting** follows the Integration's saved preference; **Live** and **Off** override
that preference for one accepted prompt. Off sends `disabled` to native thread
configuration. Acceptance consumes the override. An uncertain response keeps the
reviewed mode and request identity for a safe retry, even if the configured setting
changes while waiting. A late acknowledgement consumes only the original owner's
captured choice and revision; a newer selection remains available for the next
prompt, including re-selecting the same mode.

The control is disabled with an explanation when the App/account does not
advertise native web search. Unsupported clients omit the setting; explicit
unsupported API requests are rejected before dispatch. Search never grants shell,
browser or Host Access internet permissions.

Native steering can add input to an active turn but cannot change its search
configuration. A changed effective mode is therefore refused before acceptance,
including older automatic follow-ups. Choose **Queue** to apply the new mode after
the active response, or wait for that response to finish and send a new turn.
The refused draft and choice remain editable. Matching-mode steering continues
normally. Queued prompts retain their captured mode through editing and retries;
the native thread is cold-resumed with that mode before the queued turn starts.

This is a source contract. Combined release and native Home Assistant acceptance
are recorded separately.
