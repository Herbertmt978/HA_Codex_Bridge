# Chromium package refresh

The Alpine 3.24 repository replaced Chromium `150.0.7871.124-r0` with
`150.0.7871.128-r0`. The exact image pin, browser-worker attestation contract,
acceptance record, and focused tests move together so the App build remains
reproducible and fails closed on a version mismatch.

Validation for this change:

- `python -m pytest -q bridge_service/tests/test_browser_worker_app.py bridge_service/tests/test_browser_worker_client.py`
- `python scripts/sync_app_release.py --check`
- the pull request's Linux App-image build
