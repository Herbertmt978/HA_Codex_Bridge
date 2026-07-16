import { GlobalWorkerOptions, getDocument } from "pdfjs-dist/legacy/build/pdf.min.mjs";

const PDF_PREVIEW_MAX_PAGES = 1000;
const PDF_PREVIEW_MAX_CANVAS_PIXELS = 12_000_000;
const PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS = 12_000_000;
const PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES = PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS * 4;
const PDF_PREVIEW_MIN_SCALE = 0.35;
const PDF_PREVIEW_MAX_SCALE = 3;

GlobalWorkerOptions.workerSrc = new URL("./codex-bridge-pdf-worker.js?v=6.1.200", import.meta.url).href;

function boundedScale(value) {
  return Math.min(PDF_PREVIEW_MAX_SCALE, Math.max(PDF_PREVIEW_MIN_SCALE, value));
}

export function loadPdfDocument(blob) {
  if (!(blob instanceof Blob)) throw new TypeError("PDF preview requires a Blob");
  const loading = blob.arrayBuffer().then((buffer) => getDocument({
    data: new Uint8Array(buffer),
    canvasMaxAreaInBytes: PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES,
    enableXfa: false,
    isEvalSupported: false,
    maxImageSize: PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS,
    stopAtErrors: true,
    useSystemFonts: true,
  }));
  return loading;
}

export async function resolvePdfDocument(blob) {
  const loadingTask = await loadPdfDocument(blob);
  const document = await loadingTask.promise;
  return { document, loadingTask };
}

export function visiblePdfPageCount(document) {
  const count = Number.isSafeInteger(document?.numPages) ? document.numPages : 0;
  return Math.min(Math.max(count, 0), PDF_PREVIEW_MAX_PAGES);
}

export async function renderPdfPage({
  document,
  pageNumber,
  canvas,
  availableWidth,
  zoom = 1,
  signal,
  onRenderTask,
}) {
  if (!document || !(canvas instanceof HTMLCanvasElement)) {
    throw new TypeError("PDF preview target is unavailable");
  }
  if (signal?.aborted) throw new DOMException("PDF render cancelled", "AbortError");
  const pageCount = visiblePdfPageCount(document);
  const boundedPage = Math.min(pageCount, Math.max(1, Number(pageNumber) || 1));
  const page = await document.getPage(boundedPage);
  let renderTask = null;
  const cancelRender = () => renderTask?.cancel();
  try {
    if (signal?.aborted) throw new DOMException("PDF render cancelled", "AbortError");
    const baseViewport = page.getViewport({ scale: 1 });
    const fitWidth = Math.max(240, Math.min(Number(availableWidth) || 760, 1200));
    let scale = boundedScale((fitWidth / Math.max(baseViewport.width, 1)) * boundedScale(Number(zoom) || 1));
    let viewport = page.getViewport({ scale });
    const requestedOutputScale = Math.min(globalThis.devicePixelRatio || 1, 2);
    const requestedPixels = viewport.width * viewport.height * requestedOutputScale * requestedOutputScale;
    const pixelScale = requestedPixels > PDF_PREVIEW_MAX_CANVAS_PIXELS
      ? Math.sqrt(PDF_PREVIEW_MAX_CANVAS_PIXELS / requestedPixels)
      : 1;
    const outputScale = requestedOutputScale * pixelScale;
    if (!Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) || viewport.width < 1 || viewport.height < 1) {
      throw new Error("PDF page dimensions are invalid");
    }
    scale = boundedScale(scale);
    viewport = page.getViewport({ scale });
    canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
    canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("PDF canvas is unavailable");
    const transform = outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0];
    renderTask = page.render({
      canvasContext: context,
      viewport,
      transform,
      background: "rgb(255,255,255)",
    });
    onRenderTask?.(renderTask);
    signal?.addEventListener("abort", cancelRender, { once: true });
    await renderTask.promise;
    return { pageNumber: boundedPage, pageCount, renderTask };
  } finally {
    signal?.removeEventListener("abort", cancelRender);
    onRenderTask?.(null);
    page.cleanup();
  }
}

export {
  PDF_PREVIEW_MAX_CANVAS_PIXELS,
  PDF_PREVIEW_MAX_DECODED_CANVAS_BYTES,
  PDF_PREVIEW_MAX_DECODED_IMAGE_PIXELS,
  PDF_PREVIEW_MAX_PAGES,
  PDF_PREVIEW_MAX_SCALE,
  PDF_PREVIEW_MIN_SCALE,
};
