/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getDocumentMock } = vi.hoisted(() => ({ getDocumentMock: vi.fn() }));

vi.mock("pdfjs-dist/legacy/build/pdf.min.mjs", () => ({
  GlobalWorkerOptions: {},
  getDocument: getDocumentMock,
}));

import {
  PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES,
  PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS,
  loadPdfDocument,
  renderPdfPage,
} from "../src/pdf-preview.js";

describe("bounded PDF preview runtime", () => {
  beforeEach(() => {
    getDocumentMock.mockReset();
  });

  it("caps hostile compressed images before PDF.js allocates decoded canvases", async () => {
    const loadingTask = { promise: Promise.resolve({ numPages: 1 }) };
    getDocumentMock.mockReturnValue(loadingTask);

    await expect(loadPdfDocument(new Blob(["%PDF-1.7"]))).resolves.toBe(loadingTask);

    expect(getDocumentMock).toHaveBeenCalledWith(expect.objectContaining({
      canvasMaxAreaInBytes: PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES,
      enableXfa: false,
      isEvalSupported: false,
      maxImageSize: PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS,
      stopAtErrors: true,
    }));
    expect(PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS).toBe(12_000_000);
    expect(PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES).toBe(48_000_000);
  });

  it("cancels the retained PDF.js render task when its preview is disposed", async () => {
    let rejectRender;
    const renderTask = {
      cancel: vi.fn(() => rejectRender(new DOMException("cancelled", "AbortError"))),
      promise: new Promise((resolve, reject) => { rejectRender = reject; }),
    };
    const page = {
      cleanup: vi.fn(),
      getViewport: vi.fn(({ scale }) => ({ width: 600 * scale, height: 800 * scale })),
      render: vi.fn(() => renderTask),
    };
    const pdfDocument = { getPage: vi.fn().mockResolvedValue(page), numPages: 1 };
    const canvas = document.createElement("canvas");
    vi.spyOn(canvas, "getContext").mockReturnValue({});
    const controller = new AbortController();
    const retainedTasks = [];

    const rendering = renderPdfPage({
      document: pdfDocument,
      pageNumber: 1,
      canvas,
      availableWidth: 600,
      signal: controller.signal,
      onRenderTask: (task) => retainedTasks.push(task),
    });
    await vi.waitFor(() => expect(page.render).toHaveBeenCalledOnce());
    controller.abort();

    await expect(rendering).rejects.toThrow();
    expect(renderTask.cancel).toHaveBeenCalledOnce();
    expect(retainedTasks).toEqual([renderTask, null]);
    expect(page.cleanup).toHaveBeenCalledOnce();
  });
});
