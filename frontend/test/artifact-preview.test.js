/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const PREVIEW_MAX_BYTES = 512 * 1024;
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
      { headers: {} }
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
