/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderAssistantMarkdown } from "../src/markdown.js";
import { assistantMathLimits, mathSourceActions } from "../src/assistant-math.js";

describe("bounded assistant maths", () => {
  let host;
  let sources;
  beforeEach(() => {
    host = document.createElement("div");
    document.body.replaceChildren(host);
    sources = [];
  });
  const render = (source, options = {}) => {
    const fragment = renderAssistantMarkdown(document, source, { onMathSource: (value) => sources.push(value), ...options });
    host.append(fragment);
    return fragment;
  };

  it("renders accessible inline and standalone display fractions/matrices without hidden annotations", () => {
    const source = String.raw`Inline \(E=mc^2\) and $x+1$.

\[
\frac{a}{b}+\begin{pmatrix}1&2\\3&4\end{pmatrix}
\]

Afterwards.`;
    const fragment = render(source);
    expect(host.querySelectorAll("math")).toHaveLength(3);
    expect(host.querySelector("mfrac")).not.toBeNull();
    expect(host.querySelector("mtable")).not.toBeNull();
    expect(host.querySelector(".assistant-math-display math")?.getAttribute("display")).toBe("block");
    expect(host.querySelector("annotation, [aria-hidden], button")).toBeNull();
    expect(fragment.plainText).toContain(String.raw`\(E=mc^2\)`);
    expect(fragment.plainText).toContain(String.raw`\frac{a}{b}`);
    expect(host.textContent).toContain("Afterwards.");
    expect(sources).toHaveLength(3);
  });

  it("copies exact captured delimited source including CRLF through external keyboard-accessible buttons", () => {
    const source = "$$\r\n\\frac{x}{y}\r\n$$";
    render(source);
    expect(sources).toEqual([source]);
    expect(host.querySelector("[data-math-source]")?.dataset.mathSource).toBe(source);
    const copy = vi.fn();
    const actions = mathSourceActions(document, sources, copy);
    document.body.append(actions);
    const button = actions.querySelector("button");
    expect(button.type).toBe("button");
    expect(button.textContent).toBe("Copy maths source");
    button.click();
    expect(copy).toHaveBeenCalledWith(source);
    expect(host.contains(button)).toBe(false);
  });

  it("captures only the exact delimiter-to-delimiter display source and keeps it keyboard reachable", () => {
    render("  \\[\r\nx+1\r\n\\]  ");
    expect(sources).toEqual(["\\[\r\nx+1\r\n\\]"]);
    expect(host.querySelector(".assistant-math-display").tabIndex).toBe(0);
  });

  it("preserves fences, inline code, escaped delimiters and ordinary currencies", () => {
    const callback = vi.fn((_document, code) => {
      const pre = document.createElement("pre"); pre.textContent = code; return pre;
    });
    render([
      String.raw`Price $5 and $10.99, or US$25; \$x\$ is escaped.`,
      "Code: `$x$` and \\\\(x\\\\).", "",
      "~~~tex", String.raw`\(x\) $$y$$`, "~~~",
    ].join("\n"), { createCodeBlock: callback });
    expect(host.querySelector("math")).toBeNull();
    expect(callback).toHaveBeenCalledWith(document, "\\(x\\) $$y$$\n", "tex");
    expect(host.textContent).toContain("Price $5 and $10.99, or US$25");
  });

  it("renders maths in headings, list items and table cells with one shared response budget", () => {
    render("## $x$\n\n- $y$\n\n| Value | Formula |\n| --- | --- |\n| $z$ | \\(a_b\\) |");
    expect(host.querySelectorAll("math")).toHaveLength(4);
    expect(host.querySelector("h2 math")).not.toBeNull();
    expect(host.querySelector("li math")).not.toBeNull();
    expect(host.querySelector("td msub")).not.toBeNull();
  });

  it("retains escaped pipes in table maths source while prose pipes remain readable", () => {
    render(String.raw`| Formula | Prose |
| --- | --- |
| \(a\|b\) | left\|right |`);
    expect(sources).toEqual([String.raw`\(a\|b\)`]);
    expect(host.querySelectorAll("td")[1].textContent).toBe("left|right");
  });

  it.each([
    String.raw`\(\frac{a}{\)`,
    String.raw`\(\unsupported{x}\)`,
    String.raw`\(unfinished *literal*`,
    String.raw`\[unfinished`,
    String.raw`\[
\frac{a}{b} *literal*
unfinished`,
    "$$ unfinished",
  ])("keeps malformed/unsupported/streaming input readable as inert source: %s", (source) => {
    const fragment = render(source);
    expect(host.querySelector("math, em, script")).toBeNull();
    expect(host.textContent).toBe(source);
    expect(fragment.plainText).toBe(source);
    expect(sources).toEqual([]);
  });

  it.each([
    String.raw`\href{javascript:alert(1)}{x}`,
    String.raw`\href{https://example.org}{x}`,
    String.raw`\url{file:///private/key}`,
    String.raw`\includegraphics{https://tracker.example/pixel}`,
    String.raw`\htmlStyle{background:url(https://tracker.example)}{x}`,
    String.raw`\htmlClass{fake}{x}`,
    String.raw`\htmlData{evil=x}{x}`,
    String.raw`\input{/private/key}`,
    String.raw`\require{package}`,
    String.raw`\gdef\foo{\foo}\foo`,
    String.raw`\newcommand{\foo}{x}\foo`,
    String.raw`\textcolor{white}{hidden}`,
    '<img src=x onerror="alert(1)">',
  ])("refuses unsafe resources/HTML/macros and preserves source: %s", (expression) => {
    const source = `\\(${expression}\\)`;
    render(source);
    expect(host.querySelector("a, img, script, iframe, [src], [href], [onerror]")).toBeNull();
    expect(host.textContent).toBe(source);
    expect(sources).toEqual([]);
  });

  it("does not share macros or corrupt the safe equation following a denied expression", () => {
    render(String.raw`\(\gdef\x{evil}\) and \(x+1\)`);
    expect(host.querySelectorAll("math")).toHaveLength(1);
    expect(sources).toEqual([String.raw`\(x+1\)`]);
  });

  it("bounds expression count, source size, depth and aggregate work with readable fallback", () => {
    render(Array.from({ length: 70 }, () => "\\(x\\)").join(" "));
    expect(host.querySelectorAll("math")).toHaveLength(assistantMathLimits.expressions);
    expect(host.querySelectorAll(".assistant-math-fallback")).toHaveLength(6);
    host.replaceChildren(); sources = [];
    const huge = `\\(${"x".repeat(assistantMathLimits.expressionChars + 1)}\\)`;
    render(huge);
    expect(host.textContent).toBe(huge);
    expect(sources).toEqual([]);
    host.replaceChildren();
    const deep = `\\(${"{".repeat(40)}x${"}".repeat(40)}\\)`;
    render(deep);
    expect(host.textContent).toBe(deep);
    expect(host.querySelector("math")).toBeNull();
    host.replaceChildren();
    render(Array.from({ length: 10 }, () => `\\(\\text{${"a".repeat(1024)}}\\)`).join(" "));
    expect(host.querySelectorAll("math").length).toBeLessThan(10);
    expect(host.querySelector(".assistant-math-fallback")).not.toBeNull();
  });

  it("caps user-specified visual dimensions", () => {
    render(String.raw`\(\rule{500em}{500em}\)`);
    expect(host.querySelector("math")).not.toBeNull();
    expect(host.querySelector("mspace")?.getAttribute("width")).toBe("10em");
    expect(host.querySelector("mspace")?.getAttribute("height")).toBe("10em");
  });

  it("keeps incomplete display input from swallowing a following code fence or prose", () => {
    render("$$ unfinished\n\nOrdinary **prose**\n\n```tex\n$$ x $$\n```");
    expect(host.querySelector("strong")?.textContent).toBe("prose");
    expect(host.querySelector("pre code")?.textContent).toBe("$$ x $$\n");
    expect(host.querySelector("math")).toBeNull();
  });

  it("renders completed streamed source exactly as history", () => {
    const complete = String.raw`Answer \(\frac{a}{b}\) then $$ is ordinary here.`;
    for (let end = 0; end <= complete.length; end += 1) {
      host.replaceChildren(); sources = [];
      render(complete.slice(0, end));
      expect(host.querySelector("script, img, a")).toBeNull();
    }
    const streamed = host.innerHTML;
    host.replaceChildren(); render(complete);
    expect(host.innerHTML).toBe(streamed);
  });

  it("bounds pathological unmatched-dollar parsing without dropping source", () => {
    const source = "$a ".repeat(40_000);
    const start = performance.now();
    render(source);
    expect(performance.now() - start).toBeLessThan(1500);
    expect(host.textContent).toBe(source);
  });
});
