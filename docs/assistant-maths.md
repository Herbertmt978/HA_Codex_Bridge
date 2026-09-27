# Assistant mathematical notation

Assistant Markdown recognises `\( ... \)` inline maths and standalone
`\[ ... \]` or `$$ ... $$` display maths. Display delimiters may share one
line or enclose several lines. `$ ... $` is supported conservatively: the
opening dollar must be followed by a non-space, non-digit character, and the
closing dollar must follow a non-space and must not precede a digit. Use
`\(42\)` for digit-led formulae. Ordinary `$5` and `$10.99` prices remain text.
Code fences, inline code, escaped delimiters and link destinations are not
reinterpreted as maths. Display maths requires its own line and does not span
blank lines or code fences.

KaTeX 0.18.9 renders only recognised expressions to native MathML. No stylesheet,
font, CDN, TeX package, filesystem or network loading is involved. MathML uses
the browser's mathematical layout and accessibility support. Display expressions
scroll within the response at narrow widths, with labelled keyboard focus and
arrow-key scrolling. Unsupported browser rendering
requires browser qualification before acceptance; feature detection alone does
not demonstrate legibility or assistive-technology support.

## Source and copy

Whole-message copy and quote retain the original response. Each successfully
rendered expression has a **Copy maths source** action in the message action
area. It copies the captured original delimiters and expression, including
original display line endings. Whitespace outside the delimiters is excluded.
Windows may normalise line endings during the native clipboard roundtrip;
the clipboard API receives the exact captured text. The rendered expression's
`.assistant-math-expression[data-math-source]` wrapper owns that same source;
the renderer removes MathML annotation text so it cannot duplicate visible text
in passage copying. Maths buttons stay outside selectable response content.

The passage owner must replace a fully selected mathematical wrapper with its
captured source. A partial mathematical selection must be refused with a
readable explanation and source-copy alternative. Generic hidden-content
exclusion remains in force; renderer text or annotations must never become an
invented source quote.

## Bounds and safety

Each response shares a budget of 64 expression attempts and 8,192 expression
characters. Each expression is limited to 2,048 characters, 32 levels of brace
or bracket nesting, 128 commands, 128 alignment/row separators and eight
environments. Array column specifications over 32 characters and more than 32
square-root/fraction/left commands are refused. These conservative limits can
reject legitimate large formulae; their original source remains readable.

KaTeX uses `trust: false`, `strict: "error"`, `throwOnError: true`,
`maxExpand: 200`, `maxSize: 10`, `globalGroup: false`, and a new macro object
for each expression. Author-defined macros, HTML, URL/resource/file commands and
author-selected colours are refused before rendering. Output exceeding 4,096
nodes, or containing links, images, executable elements or resource attributes,
falls back to original delimited text. Error messages are never inserted as HTML.

Malformed, unsupported, incomplete streamed or over-budget expressions remain
inert source text. An incomplete display opener does not swallow following prose
or a code fence. Completed streaming and historical messages use the same parser.

Primary references reviewed on 27 September 2026:

- [KaTeX security](https://katex.org/docs/security)
- [KaTeX options](https://katex.org/docs/options)
- [KaTeX API](https://katex.org/docs/api)
- [KaTeX release](https://github.com/KaTeX/KaTeX/releases/tag/v0.18.9)

The release owner adds the exact package/lock dependency and runs the normal
build. Feature checks are in `frontend/test/assistant-math.test.js`; served
Shadow DOM/browser, theme, narrow-screen, copy, keyboard/touch and accessibility
qualification remains required alongside the combined release gates.
