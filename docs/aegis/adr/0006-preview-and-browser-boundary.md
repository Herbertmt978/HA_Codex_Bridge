# ADR 0006: Artifact preview and browser-automation boundary

**Status:** Accepted for document preview. The browser-worker protocol,
client-owned tool boundary, and inert pinned package scaffold are accepted;
runtime attestation and capability advertisement remain gated by the
conditions below.

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

Codex `0.144.5` provides the required private extension point through its
experimental app-server `dynamicTools` field and the correlated
`item/tool/call` client callback. The Bridge may opt into that API only when a
browser worker was explicitly requested and its complete runtime proof is
healthy. New Codex threads then receive one non-reserved `ha_browser`
namespace containing fixed high-level functions. The runtime broker must
correlate every callback to the active app-server generation, Codex thread,
turn, Bridge run, and ephemeral browser-session owner before dispatch. It must
return a bounded safe failure for every stale, unsolicited, malformed, or
cross-owner callback.

The fixed contract permits only public HTTP(S) navigation, bounded plain-text
inspection, click, type, select, wait, screenshot, PDF, and close. It has no
fields for scripts, evaluation, headers, cookies, credentials, downloads,
paths, CDP, WebDriver, or process control. Screenshots and PDFs are validated
and stored through the private artifact boundary first. A screenshot may also
be returned to Codex as a bounded inline data URL over the private app-server
pipe; a remote image URL is never valid. The Home Assistant browser still sees
only same-origin authenticated artifact routes.

`dynamicTools` is registered at `thread/start`. A Codex thread created before
the worker was proven does not silently gain the tool on resume; the product
must create a fresh Codex session (or use a future upstream supported update
method) instead of injecting an unproven field into old runtime state.

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
- The typed contract and fake-worker broker can land without advertising a
  browser capability. Chrome/Playwright UI design flows still require a
  separately reviewed App release, immutable browser assets, HAOS sandbox
  acceptance, connection-time destination enforcement, and an
  architecture-aware release track. The current accepted `0.8.3` App does not
  bundle Chromium. This development slice pins Chromium in the proposed
  `amd64` image, but deliberately creates no readiness attestation and exposes
  no capability because packaging is not isolation proof.
- Issue #43 tracks the secure App-owned browser-worker follow-up; it does not
  authorize interactive Chromium before these conditions are met.
