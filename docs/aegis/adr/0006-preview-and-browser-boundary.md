# ADR 0006: Artifact preview and browser-automation boundary

**Status:** Accepted for document preview; browser automation remains gated by
the conditions below.

## Decision

The Home Assistant panel may preview an artifact only after retrieving it from
the existing administrator-authenticated, same-origin artifact route. Text and
allowlisted raster images retain their existing bounded preview behavior. A PDF
may be rendered only when its declared artifact size and fetched size are within
the dedicated 8 MB limit and its bytes begin with the PDF signature. Rendering
uses the bundled local PDF.js canvas renderer with scripting, eval, and XFA
support disabled. It does not use an iframe or a native browser PDF embed, and
it never accepts a remote URL. HTML, SVG, XML, invalid PDFs, unknown-size files,
and oversized files remain on the safe open/download fallback.

Agent browser automation is a different capability. It must not be implemented
as an iframe address bar, a panel-to-Chrome connection, an exposed CDP port, or
a general local MCP endpoint. A future implementation must use a fixed,
App-owned helper and isolated browser worker; expose only bounded high-level
actions; keep browser profiles ephemeral; prevent access to Home Assistant,
Supervisor, App-private state, LAN/private addresses, credentials, arbitrary
headers, raw evaluation, and raw CDP; and publish screenshots or printed PDFs
through the existing private artifact pipeline. It must fail closed if the
Chromium sandbox or enforced egress boundary is unavailable.

## Rationale

Document preview consumes bytes the administrator already selected through
Home Assistant and adds no remote network surface. Canvas rendering avoids
delegating PDF execution to a browser-native document/plugin context. Browser
automation performs new outbound navigation against untrusted pages, so it needs process,
filesystem, and connection-time network isolation of its own. Treating those
features as equivalent would either weaken the existing browser-to-HA trust
boundary or give a model-controlled page a path to local services.

## Consequences

- The panel can provide a useful Codex-style PDF viewer without exposing the
  App or Bridge or delegating to a native browser PDF viewer.
- Invalid or unsupported content keeps the safe download fallback.
- Native web search remains provider-side and is not a browser-network
  exemption.
- Chrome/Playwright UI design flows require a separately reviewed App release,
  immutable browser assets, HAOS sandbox acceptance, and an architecture-aware
  release track. The current App is `amd64` only and does not bundle Chromium.
- Issue #43 tracks the secure App-owned browser-worker follow-up; it does not
  authorize interactive Chromium before these conditions are met.
