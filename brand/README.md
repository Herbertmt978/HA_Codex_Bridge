# Codex Bridge brand assets

The SVG files are the editable sources for the Codex Bridge identity. The PNG
exports are generated snapshots for Home Assistant, HACS, README rendering,
and the GitHub social-preview setting.

Regenerate every PNG copy from the repository root:

```console
npm run brand:render
```

The renderer uses the maintainer machine's browser and available fonts. Review
the PNG diff visually after regeneration; cross-platform exports are not
expected to be byte-identical.

GitHub does not read the social-preview asset from repository metadata. After
regenerating `brand/social-preview.png`, upload it manually in the repository's
**Settings -> General -> Social preview** control and verify the public card.

The mark represents a protected code portal: two bridge pylons form a subtle
home-shaped negative space, while the three lower strokes carry a command path
through the private boundary. It is intentionally distinct from the OpenAI and
Home Assistant product marks.
