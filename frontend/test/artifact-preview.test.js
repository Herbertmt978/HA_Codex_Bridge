/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/codex-bridge-panel.js";

const PREVIEW_MAX_BYTES = 512 * 1024;
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
});
