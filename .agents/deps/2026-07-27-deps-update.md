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

## Compatible Node tooling refresh

The panel toolchain now uses `@playwright/test` 1.62.0, ESLint 10.8.0, and
`globals` 17.8.0. These releases remain compatible with the repository's
existing Node support range. jsdom stays on 29.1.1 because jsdom 30 raises its
minimum runtime to Node 22.22.2; Dependabot is configured to keep reporting
compatible jsdom updates while ignoring that incompatible major. The lockfile
also refreshes ESLint's dev-only transitive `brace-expansion` dependency from
5.0.7 to 5.0.8, resolving GHSA-mh99-v99m-4gvg without adding a direct
dependency.

Validation for this change:

- `npm ci`
- `npm run lint`
- `npm run test:unit`
- `npm run check:generated`
- `npm audit`
- `python -m pytest -q bridge_service/tests/test_release_workflows.py`
- clean install, lint, and generated-asset checks at the Node 20.19.0 and
  22.13.0 floors; both unit runs retain the same three artifact-download
  failures reproduced on unchanged `main`
- Node 20.19.0 also retains `main`'s existing `pdfjs-dist` engine warning;
  this focused update does not change the repository's support metadata
