# Council Quick Check: README brand portability

**Date:** 2026-07-15
**Mode:** quick (single-agent, no multi-perspective spawning)
**Target:** Render the repository header consistently on GitHub and in the HACS README viewer.

## Verdict: PASS

```json
{
  "verdict": "PASS",
  "confidence": "HIGH",
  "key_insight": "Standard Markdown image syntax with an absolute PNG source preserves the brand across GitHub and HACS renderers.",
  "findings": [],
  "recommendation": "Ship the portable image URL and verify it in both GitHub and the live HACS repository page."
}
```

## Analysis

The README already leads with a plain-language outcome, separates the HACS
Integration from the Supervisor App, puts the experimental-platform warning
next to installation, and provides visible security, recovery, and uninstall
guidance. The live HACS viewer exposed a renderer-specific defect rather than a
content problem: its iframe stripped the source from the raw HTML image element.

Using standard Markdown image syntax with the repository's own absolute raw PNG
keeps the same generated artwork, does not add a third-party brand dependency,
and renders on surfaces that sanitize raw HTML. The local source assets remain
the authority and can still be regenerated with `npm run brand:render`.

---
*Quick check -- for thorough multi-perspective review, run `$council validate` (default mode).*
