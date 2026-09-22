# Implementation notes

Extending the existing relay avoided a second store and kept upstream secrets
out of native configuration inspection. Capability negotiation preserves older
App compatibility and existing OAuth behaviour.

Secret entry uses HA HTTP views because WebSocket debug logging can retain
payloads. Browser tests caught a low-contrast error message and a test-harness
fetch override; both were corrected. Native qualification confirmed that server
discovery must be requested before testing rejection, and exposed noisy expected
cancellation logging that was corrected with a regression test.

The result is development code, not a released update. Write-only controls do
not encrypt App storage or erase old backups, and removing a credential cannot
revoke it with its provider. The UI and guide explain those limits.
