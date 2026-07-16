/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const PREVIEW_MAX_BYTES = 512 * 1024;
const PDF_PREVIEW_MAX_BYTES = 8 * 1024 * 1024;
const GENERATED_IMAGE_PREVIEW_MAX_BYTES = 8 * 1024 * 1024;
const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;

function createArtifact(overrides = {}) {
  return {
    artifact_id: "art_safe",
    filename: "note.txt",
    mime_type: "text/plain",
    size_bytes: 12,
    ...overrides,
  };
}

function createPanel(artifact) {
  const panel = document.createElement("codex-bridge-panel");
  document.body.append(panel);
  panel._selectedThreadId = "thread_safe";
  panel._selectedArtifactId = artifact.artifact_id;
  panel._artifacts = [artifact];
  return panel;
}

function previewUrlStubs() {
  const createObjectUrl = vi.fn(() => `blob:${window.location.origin}/artifact-preview`);
  const revokeObjectUrl = vi.fn();
  URL.createObjectURL = createObjectUrl;
  URL.revokeObjectURL = revokeObjectUrl;
  return { createObjectUrl };
}

describe("artifact previews", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    URL.createObjectURL = originalCreateObjectUrl;
    URL.revokeObjectURL = originalRevokeObjectUrl;
  });

  it("fetches and renders a text artifact within the preview cap", async () => {
    const panel = createPanel(createArtifact());
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("preview text", { status: 200, headers: { "Content-Type": "text/plain" } })
    );

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(panel._artifactPreview).toMatchObject({ kind: "text", text: "preview text" });
  });

  it("settles a failed preview locally and offers a working retry", async () => {
    const panel = createPanel(createArtifact());
    panel._setError("Bridge request failed", { source: "poll" });
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockRejectedValueOnce(new Error("temporary HA preview failure"))
      .mockResolvedValueOnce(
        new Response("preview recovered", { status: 200, headers: { "Content-Type": "text/plain" } })
      );

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(panel._artifactPreview).toMatchObject({ kind: "binary", retryable: true });
    expect(panel._error).toBe("Bridge request failed");
    expect(panel.shadowRoot.getElementById("error-strip").classList).toContain("visible");
    const retry = panel.shadowRoot.querySelector('[data-action="retry-artifact-preview"]');
    expect(retry).not.toBeNull();
    retry.click();
    await vi.waitFor(() => expect(panel._artifactPreview).toMatchObject({ kind: "text", text: "preview recovered" }));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(panel._error).toBe("Bridge request failed");
  });

  it.each([401, 403, 502, 503, 504])("surfaces preview HTTP %s as a global error", async (status) => {
    const panel = createPanel(createArtifact());
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("preview unavailable", { status }));

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(panel._error).toBe("Preview failed");
    expect(panel._artifactPreview).toBeNull();
    expect(panel.shadowRoot.getElementById("error-strip").classList).toContain("visible");
  });

  it("surfaces a browser preview network failure globally", async () => {
    const panel = createPanel(createArtifact());
    vi.spyOn(window, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(panel._error).toBe("Failed to fetch");
    expect(panel._artifactPreview).toBeNull();
  });

  it("fetches a capped image artifact and creates a local preview URL", async () => {
    const urls = previewUrlStubs();
    const panel = createPanel(createArtifact({ filename: "chart.png", mime_type: "image/png" }));
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("image", { status: 200, headers: { "Content-Type": "image/png" } })
    );

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();
    expect(panel._artifactPreview).toMatchObject({ kind: "image", url: `blob:${window.location.origin}/artifact-preview` });
  });

  it("validates a PDF and renders the safe canvas preview shell", async () => {
    const urls = previewUrlStubs();
    const artifact = createArtifact({ filename: "design.pdf", mime_type: "application/pdf", size_bytes: 32 });
    const panel = createPanel(artifact);
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("%PDF-1.7\n1 0 obj\n", { status: 200, headers: { "Content-Type": "application/octet-stream" } })
    );

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();
    expect(panel._artifactPreview).toMatchObject({
      kind: "pdf",
      contentType: "application/pdf",
      url: `blob:${window.location.origin}/artifact-preview`,
    });
    const preview = panel.shadowRoot.getElementById("artifact-preview");
    const shell = preview.querySelector(".pdf-preview-shell");
    expect(shell).not.toBeNull();
    expect(shell.querySelector("canvas.pdf-preview-canvas")).toBeInstanceOf(HTMLCanvasElement);
    expect(shell.querySelector('[data-action="pdf-previous"]')).not.toBeNull();
    expect(shell.querySelector('[data-action="pdf-next"]')).not.toBeNull();
    expect(shell.querySelector('[data-action="pdf-zoom-out"]')).not.toBeNull();
    expect(shell.querySelector('[data-action="pdf-zoom-in"]')).not.toBeNull();
    expect(shell.querySelector('[data-action="open-pdf-preview"]')).not.toBeNull();
    expect(shell.querySelector('[data-action="download-artifact"]')).not.toBeNull();
    expect(preview.querySelector("iframe, object, embed")).toBeNull();
  });

  it("latches malformed PDF failures until the user explicitly retries", async () => {
    const artifact = createArtifact({ filename: "malformed.pdf", mime_type: "application/pdf", size_bytes: 32 });
    const panel = createPanel(artifact);
    const preview = {
      artifactId: artifact.artifact_id,
      blob: new Blob(["%PDF-1.7\nmalformed"], { type: "application/pdf" }),
      filename: artifact.filename,
      kind: "pdf",
    };
    panel._artifactPreview = preview;
    panel._pdfPreviewArtifactId = artifact.artifact_id;
    panel._pdfPreviewError = "PDF preview unavailable.";

    await panel._ensurePdfPreview(preview);
    expect(panel._pdfPreviewLoadPromise).toBeNull();
    panel._renderArtifactPreview();
    const retry = panel.shadowRoot.querySelector('[data-action="retry-pdf-preview"]');
    expect(retry).not.toBeNull();
    expect(retry.hidden).toBe(false);

    const ensureSpy = vi.spyOn(panel, "_ensurePdfPreview").mockResolvedValue(undefined);
    retry.click();
    expect(panel._pdfPreviewError).toBe("");
    expect(ensureSpy).toHaveBeenCalledWith(preview);
  });

  it("keeps a spoofed PDF inert when its signature is invalid", async () => {
    previewUrlStubs();
    const artifact = createArtifact({ filename: "spoofed.pdf", mime_type: "application/pdf", size_bytes: 64 });
    const panel = createPanel(artifact);
    vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("<script>window.top.location='https://evil.example'</script>", {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      })
    );

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(panel._artifactPreview.kind).toBe("binary");
    expect(panel._artifactPreview.notice).toMatch(/valid PDF header/i);
    expect(panel.shadowRoot.getElementById("artifact-preview").querySelector("iframe, object, embed")).toBeNull();
  });

  it("enforces the PDF preview cap against the fetched bytes as well as metadata", async () => {
    previewUrlStubs();
    const artifact = createArtifact({ filename: "oversized.pdf", mime_type: "application/pdf", size_bytes: 32 });
    const panel = createPanel(artifact);
    vi.spyOn(window, "fetch").mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array(PDF_PREVIEW_MAX_BYTES + 1)], { type: "application/pdf" }),
    });

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(panel._artifactPreview.kind).toBe("binary");
    expect(panel._artifactPreview.notice).toContain("8 MB");
    expect(panel.shadowRoot.getElementById("artifact-preview").querySelector("iframe")).toBeNull();
  });

  it("rejects and cancels a streamed preview response that exceeds the PDF cap", async () => {
    const artifact = createArtifact({ filename: "streamed-large.pdf", mime_type: "application/pdf", size_bytes: 32 });
    const panel = createPanel(artifact);
    const cancel = vi.fn().mockResolvedValue(undefined);
    const releaseLock = vi.fn();
    const read = vi.fn()
      .mockResolvedValueOnce({ done: false, value: new Uint8Array(PDF_PREVIEW_MAX_BYTES) })
      .mockResolvedValueOnce({ done: false, value: new Uint8Array([0]) });
    vi.spyOn(window, "fetch").mockResolvedValue({
      ok: true,
      headers: new Headers({ "Content-Type": "application/pdf" }),
      body: { getReader: () => ({ read, cancel, releaseLock }) },
    });

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(cancel).toHaveBeenCalledOnce();
    expect(releaseLock).toHaveBeenCalledOnce();
    expect(panel._artifactPreview.kind).toBe("binary");
    expect(panel._artifactPreview.notice).toContain("8 MB");
    expect(panel.shadowRoot.getElementById("artifact-preview").querySelector("iframe, object, embed")).toBeNull();
  });

  it("does not send a Range header for a zero-byte artifact", async () => {
    const artifact = createArtifact({ artifact_id: "empty", filename: "empty.txt", size_bytes: 0 });
    const panel = createPanel(artifact);
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("", { status: 200, headers: { "Content-Type": "text/plain" } })
    );

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/codex_bridge/threads/thread_safe/artifacts/empty",
      { headers: {} }
    );
  });

  it("invalidates a stale preview when switching artifacts and revokes the active blob on disconnect", async () => {
    const urls = previewUrlStubs();
    const first = createArtifact({ artifact_id: "first", filename: "first.png", mime_type: "image/png" });
    const second = createArtifact({ artifact_id: "second", filename: "second.png", mime_type: "image/png" });
    const panel = createPanel(first);
    panel._artifacts = [first, second];
    let resolveFirst;
    const firstResponse = new Promise((resolve) => { resolveFirst = resolve; });
    const fetchSpy = vi.spyOn(window, "fetch").mockImplementation((url) => (
      String(url).endsWith("/first")
        ? firstResponse
        : Promise.resolve(new Response("second", { status: 200, headers: { "Content-Type": "image/png" } }))
    ));

    const staleLoad = panel._loadArtifactPreview(first.artifact_id);
    await Promise.resolve();
    panel._selectedArtifactId = second.artifact_id;
    panel._clearArtifactPreview();
    const activeLoad = panel._loadArtifactPreview(second.artifact_id);
    resolveFirst(new Response("first", { status: 200, headers: { "Content-Type": "image/png" } }));
    await staleLoad;
    await activeLoad;

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(panel._artifactPreview.artifactId).toBe(second.artifact_id);
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();
    panel.remove();
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(`blob:${window.location.origin}/artifact-preview`);
  });

  it("revokes the previous blob URL when switching from one preview to another", async () => {
    const urls = previewUrlStubs();
    const first = createArtifact({ artifact_id: "switch-first", filename: "first.png", mime_type: "image/png" });
    const second = createArtifact({ artifact_id: "switch-second", filename: "second.png", mime_type: "image/png" });
    const panel = createPanel(first);
    panel._artifacts = [first, second];
    vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response("first", { status: 200, headers: { "Content-Type": "image/png" } }))
      .mockResolvedValueOnce(new Response("second", { status: 200, headers: { "Content-Type": "image/png" } }));

    await panel._loadArtifactPreview(first.artifact_id);
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();

    await panel._selectArtifact(second.artifact_id);

    expect(urls.createObjectUrl).toHaveBeenCalledTimes(2);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(`blob:${window.location.origin}/artifact-preview`);
    panel.remove();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it("invalidates a pending preview load when disconnected", async () => {
    const urls = previewUrlStubs();
    const artifact = createArtifact({ filename: "pending.png", mime_type: "image/png" });
    const panel = createPanel(artifact);
    let resolveResponse;
    const pending = new Promise((resolve) => { resolveResponse = resolve; });
    vi.spyOn(window, "fetch").mockReturnValue(pending);
    const load = panel._loadArtifactPreview(artifact.artifact_id);
    await Promise.resolve();
    panel.remove();
    resolveResponse(new Response("pending", { status: 200, headers: { "Content-Type": "image/png" } }));
    await load;

    expect(urls.createObjectUrl).not.toHaveBeenCalled();
    expect(panel._artifactPreview).toBeNull();
  });

  it("does not preview an oversized artifact but still downloads it in full on request", async () => {
    previewUrlStubs();
    const artifact = createArtifact({ size_bytes: PREVIEW_MAX_BYTES + 1 });
    const panel = createPanel(artifact);
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("download", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="note.txt"' },
      })
    );
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await panel._loadArtifactPreview(artifact.artifact_id);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(panel._artifactPreview.notice).toContain("512 KB");

    await panel._downloadArtifact(artifact.artifact_id);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/codex_bridge/threads/thread_safe/artifacts/art_safe",
      { headers: {} }
    );
    expect(clickSpy).toHaveBeenCalledOnce();
  });

  it("uses the separate bounded preview limit for generated images without weakening generic artifact limits", async () => {
    const urls = previewUrlStubs();
    const generated = createArtifact({
      artifact_id: "generated-large",
      filename: "generated.png",
      mime_type: "image/png",
      size_bytes: PREVIEW_MAX_BYTES + 1,
      source: "generated_image",
    });
    const panel = createPanel(generated);
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("image", { status: 200, headers: { "Content-Type": "image/png" } })
    );

    await panel._loadArtifactPreview(generated.artifact_id);

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(urls.createObjectUrl).toHaveBeenCalledOnce();
    expect(panel._artifactPreview.kind).toBe("image");

    generated.size_bytes = GENERATED_IMAGE_PREVIEW_MAX_BYTES + 1;
    await panel._loadArtifactPreview(generated.artifact_id);
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(panel._artifactPreview.notice).toContain("8 MB");
  });

  it("skips unknown-size artifacts for auto-preview and does not fetch them when selected", async () => {
    const unknown = createArtifact({ artifact_id: "art_unknown", size_bytes: undefined });
    const previewable = createArtifact({ artifact_id: "art_previewable", filename: "summary.txt" });
    const panel = createPanel(unknown);
    panel._selectedArtifactId = null;
    panel._artifacts = [unknown, previewable];
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("summary", { status: 200, headers: { "Content-Type": "text/plain" } })
    );

    panel._syncSelectedArtifact();

    expect(panel._selectedArtifactId).toBe(previewable.artifact_id);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/codex_bridge/threads/thread_safe/artifacts/art_previewable",
      { headers: { Range: "bytes=0-11" } }
    );

    panel._selectedArtifactId = unknown.artifact_id;
    await panel._loadArtifactPreview(unknown.artifact_id);

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(panel._artifactPreview.notice).toContain("file size is unknown");
  });

  it("auto-selects a normal-size generated image even when it exceeds the generic cap", () => {
    const generated = createArtifact({
      artifact_id: "generated-auto",
      filename: "generated.png",
      mime_type: "image/png",
      size_bytes: PREVIEW_MAX_BYTES + 1,
      source: "generated_image",
    });
    const panel = createPanel(generated);
    panel._selectedArtifactId = null;
    const loadPreview = vi.spyOn(panel, "_loadArtifactPreview").mockResolvedValue();

    panel._syncSelectedArtifact();

    expect(panel._selectedArtifactId).toBe(generated.artifact_id);
    expect(loadPreview).toHaveBeenCalledWith(generated.artifact_id);
  });

  it("renders a generated image as a safe inline result and opens its authenticated preview", () => {
    const artifact = createArtifact({
      artifact_id: "generated-safe",
      filename: "C:\\private\\<img src=x onerror=alert(1)>.png",
      relative_path: "/private/should-not-appear.png",
      mime_type: "image/png",
      source: "generated_image",
    });
    const panel = createPanel(artifact);
    const selectArtifact = vi.spyOn(panel, "_selectArtifact").mockResolvedValue();
    const card = panel._renderEvent({
      event_type: "artifact.added",
      sequence: 7,
      payload: { artifact_id: artifact.artifact_id },
    });
    panel.shadowRoot.getElementById("message-list").append(card);

    expect(card.classList.contains("generated-image-message")).toBe(true);
    expect(card.textContent).toContain("Generated image");
    expect(card.textContent).not.toContain("C:\\private");
    expect(card.textContent).not.toContain("/private/should-not-appear.png");
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector("script")).toBeNull();
    const open = card.querySelector('[data-action="select-artifact"]');
    expect(open.getAttribute("aria-label")).toContain("Open generated image");

    open.click();
    expect(selectArtifact).toHaveBeenCalledWith(artifact.artifact_id);
  });

  it("keeps unassociated generated-image events chronological without inventing a preview link", () => {
    const panel = createPanel(createArtifact());
    const card = panel._renderEvent({
      event_type: "artifact.added",
      sequence: 8,
      payload: { source: "generated_image", artifact_id: "not-yet-listed", mime_type: "image/png" },
    });

    expect(card.textContent).toContain("Generated image");
    expect(card.textContent).toContain("Available in Files");
    expect(card.querySelector('[data-action="select-artifact"]')).toBeNull();
  });

  it("shows a safe retry message when a generated image is rejected", () => {
    const panel = createPanel(createArtifact());
    const card = panel._renderEvent({
      event_type: "item.completed",
      sequence: 9,
      payload: {
        item_type: "imageGeneration",
        status: "failed",
        error: "image_result_rejected",
        result: "private-provider-output",
        savedPath: "C:\\private\\image.png",
      },
    });

    expect(card.textContent).toContain("Image generation failed");
    expect(card.textContent).toContain("Retry the prompt");
    expect(card.textContent).not.toContain("private-provider-output");
    expect(card.textContent).not.toContain("C:\\private");
  });

  it("uses an already authenticated blob preview as an inline generated-image thumbnail without refetching", () => {
    const artifact = createArtifact({ artifact_id: "generated-thumb", filename: "generated.png", mime_type: "image/png", source: "generated_image" });
    const panel = createPanel(artifact);
    panel._artifactPreview = {
      artifactId: artifact.artifact_id,
      filename: artifact.filename,
      contentType: artifact.mime_type,
      kind: "image",
      url: `blob:${window.location.origin}/generated-thumb`,
    };
    const fetchSpy = vi.spyOn(window, "fetch");

    const card = panel._renderEvent({ event_type: "artifact.added", sequence: 9, payload: { artifact_id: artifact.artifact_id } });

    expect(card.querySelector(".generated-image-thumbnail img").getAttribute("src")).toBe(`blob:${window.location.origin}/generated-thumb`);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
