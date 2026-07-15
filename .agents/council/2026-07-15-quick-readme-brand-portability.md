# Council Quick Check: README brand portability

**Date:** 2026-07-15
**Mode:** quick (single-agent, no multi-perspective spawning)
**Target:** Render the repository header consistently on GitHub and in the HACS README viewer.

## Verdict: PASS

```json
{
  "verdict": "PASS",
  "confidence": "HIGH",
  "key_insight": "A text-first header and absolute badge destinations preserve a polished README across GitHub and HACS without depending on HACS to embed repository-hosted artwork.",
  "findings": [],
  "recommendation": "Keep the generated brand assets for the Integration, App, social preview, and repository media; omit the decorative README hero and verify the text-first header in both GitHub and the live HACS repository page."
}
```

## Analysis

The README already leads with a plain-language outcome, separates the HACS
Integration from the Supervisor App, puts the experimental-platform warning
next to installation, and provides visible security, recovery, and uninstall
guidance. The live HACS viewer exposed a renderer-specific portability defect.
It failed to display the repository-hosted hero in both raw HTML and standard
Markdown, even though the PNG itself loaded directly. It also mishandled the
two badge links whose destinations were relative, while the four badges with
absolute destinations rendered normally.

The robust header therefore uses a centered text identity, retains the useful
badge row with absolute destinations, and keeps remote artwork out of the
HACS-rendered README. The generated local assets remain authoritative for the
Integration, App, repository media, and social preview and can still be
regenerated with `npm run brand:render`.

---
*Quick check -- for thorough multi-perspective review, run `$council validate` (default mode).*
