import { describe, expect, it } from "vitest";

import { fileTypeIconMarkup, fileTypeKind } from "../src/file-type-icons.js";

describe("file type icons", () => {
  it.each([
    ["report.docx", "word", "#185abd"],
    ["budget.xlsx", "excel", "#107c41"],
    ["slides.pptx", "powerpoint", "#c43e1c"],
    ["paper.pdf", "pdf", "#c43835"],
    ["notes.txt", "text", "#657486"],
    ["chart.png", "image", "#397f76"],
    ["source.py", "code", "#596f9f"],
    ["audio.mp3", "audio", "#725ca5"],
    ["video.mp4", "video", "#397f8d"],
    ["bundle.zip", "archive", "#856944"],
    ["unknown.bin", "file", "#657486"],
  ])("has a bundled icon for %s", (filename, kind, colour) => {
    expect(fileTypeKind(filename)).toBe(kind);
    expect(fileTypeIconMarkup(filename)).toContain(colour);
  });

  it("does not interpolate an untrusted filename into SVG markup", () => {
    const markup = fileTypeIconMarkup('file.<script>alert(1)</script>');
    expect(markup).not.toContain("<script>");
    expect(markup).toContain("<svg");
  });
});
