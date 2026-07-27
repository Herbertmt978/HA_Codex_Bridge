# Chromium package refresh

The Alpine 3.24 repository replaced Chromium `150.0.7871.124-r0` with
`150.0.7871.128-r0`. The exact image pin, browser-worker attestation contract,
acceptance record, and focused tests move together so the App build remains
reproducible and fails closed on a version mismatch.

Validation for this change:

- `python -m pytest -q bridge_service/tests/test_browser_worker_app.py bridge_service/tests/test_browser_worker_client.py`
- `python scripts/sync_app_release.py --check`
- the pull request's Linux App-image build

## GitHub Actions refresh

The repository's pinned checkout, Python setup, uv setup, and container-login
actions move to their reviewed releases. Because setup-uv v9 no longer prunes
its cache by default, every setup-uv step now sets `prune-cache: true` so this
refresh preserves the existing cache lifecycle and storage behavior.

Validation for this change:

- `python -m pytest -q bridge_service/tests/test_release_workflows.py`
- `python -m ruff check bridge_service/tests/test_release_workflows.py`
- `python scripts/sync_app_release.py --check`
- the pull request's workflow-policy and App-image checks

## FastAPI runtime refresh

The bridge's FastAPI floor moves from `0.139.0` to `0.140.7`. The deployed
Home Assistant App requirements are regenerated from the bridge project so the
runtime image receives the same reviewed version instead of leaving the update
only in development metadata.

Validation for this change:

- `python -m pytest -q bridge_service/tests`
- `python -m ruff check bridge_service`
- `python scripts/stage_app_context.py --arch amd64`
- the pull request's Integration and Bridge tests and App-image build
