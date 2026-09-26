# Answer questions from Home Assistant notifications

Codex questions remain answerable in their chat. Question notifications are an
optional way to reach that question or reply from a registered Companion phone.
They are off by default and apply only to ordinary Codex questions. Notification
replies cannot approve commands, file changes, MCP requests or execution access.

Open the Codex Bridge Integration's configuration options. Enable question
notices, choose Home Assistant's persistent notification centre, registered
administrator Companion devices, or both. A phone is eligible only while its
current registration, device, notification service and active administrator
account can be verified. Removing a device or revoking that account removes its
reply authority.

Notices show generic text by default. They offer Open chat and, where supported,
a text Reply action. Enable question previews only if question text may be shown
on the selected phones, including their lock screens. Short single-choice
questions can then offer labelled choices. Questions with several fields,
multiple selections, long choices, sensitive credential requests or an
unsupported phone open the authenticated chat instead. Persistent notifications
always contain a generic message and an Open chat link.

A reply answers the existing question; it does not start another chat message.
The first answer accepted by the Bridge wins across chat and phones. Expired,
answered or revoked notification actions cannot answer another question.
Home Assistant reconciles pending questions after restart and clears the
corresponding notices when the question is no longer pending. A delivery failure
does not stop chat answering. Mobile notification delivery has no transaction
or acknowledgement: a saved delivery claim favours avoiding duplicate notices
after a crash, and cannot prove that a phone displayed one.

The [Companion action format](https://companion.home-assistant.io/docs/notifications/actionable-notifications/) requests device authentication where supported. [Home
Assistant's registration context](https://github.com/home-assistant/core/blob/2026.9.3/homeassistant/components/mobile_app/helpers.py) and a private random action correlation
authorise the reply; a device identifier supplied in an event is not independent
proof of the sending device. The Integration never copies webhook secrets into
its notification ledger or adds a public reply endpoint.

The source supports the documented Companion text/choice action formats.
Physical Android/iOS display, authentication, replies and clearing require
qualification on the deployed Home Assistant and Companion versions before
release. Automated service-call evidence alone is not physical phone evidence.
