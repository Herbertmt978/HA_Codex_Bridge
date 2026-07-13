/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createPreviewElement,
  createSafeLink,
  previewDescriptor,
  sanitizeBlobUrl,
  sanitizeFilename,
  sanitizeId,
  sanitizeUrl,
  setSafeAttribute,
} from "../src/safe-dom.js";
import { hostileCorpus, makeArtifact } from "./helpers.js";

import "../src/codex-bridge-panel.js";

describe("safe DOM and hostile content", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    window.requestAnimationFrame = (callback) => {
      callback();
      return 1;
    };
  });
  it("rejects scriptable and remote URLs by default", () => {
    expect(sanitizeUrl("java" + "script:alert(1)")).toBeNull();
    expect(sanitizeUrl("data:text/html,<script>x</script>")).toBeNull();
    expect(sanitizeUrl("https://evil.example/collect")).toBeNull();
    expect(sanitizeUrl("/api/codex_bridge/status", { base: "https://ha.example" })).toBe("https://ha.example/api/codex_bridge/status");
    expect(sanitizeUrl("https://evil.example/collect", { base: "https://ha.example", allowRemote: true })).toBe("https://evil.example/collect");
  });

  it("allows same-origin links but gives remote links explicit opener protection", () => {
    const same = createSafeLink(document, "/lovelace", "Home");
    expect(same?.href).toBe(`${window.location.origin}/lovelace`);
    expect(same?.target).toBe("");
    expect(createSafeLink(document, "https://evil.example", "Evil", { allowRemote: true })?.rel).toContain("noopener");
  });

  it("blocks event-handler and srcdoc attributes", () => {
    const node = document.createElement("div");
    expect(setSafeAttribute(node, "onclick", "alert(1)")).toBe(false);
    expect(setSafeAttribute(node, "srcdoc", "<script>x</script>")).toBe(false);
    expect(setSafeAttribute(node, "data-value", "<img onerror=x>")).toBe(true);
    expect(node.getAttribute("data-value")).toContain("<img");
  });

  it("renders hostile text as one inert text node", () => {
    const descriptor = previewDescriptor(makeArtifact({ filename: "note.html", mime_type: "text/html" }), new Blob([hostileCorpus.join("\n")], { type: "text/html" }));
    expect(descriptor.kind).toBe("binary");
    const text = document.createElement("div");
    text.textContent = hostileCorpus.join("\n");
    expect(text.querySelector("script,svg,img,iframe")).toBeNull();
    expect(text.textContent).toContain("<script>");
  });

  it("only previews raster blobs and allowlisted text; PDF/SVG/HTML are binary", () => {
    expect(previewDescriptor(makeArtifact({ filename: "x.png" }), new Blob([], { type: "image/png" })).kind).toBe("image");
    expect(previewDescriptor(makeArtifact({ filename: "x.svg" }), new Blob([], { type: "image/svg+xml" })).kind).toBe("binary");
    expect(previewDescriptor(makeArtifact({ filename: "x.pdf" }), new Blob([], { type: "application/pdf" })).kind).toBe("binary");
    expect(previewDescriptor(makeArtifact({ filename: "x.txt" }), new Blob([], { type: "text/plain" })).kind).toBe("text");
    expect(createPreviewElement(document, { kind: "binary", filename: "x.pdf" })).not.toBeInstanceOf(HTMLIFrameElement);
  });

  it("accepts only local blob URLs for raster previews", () => {
    expect(sanitizeBlobUrl("blob:https://evil.example/id", { origin: window.location.origin })).toBeNull();
    const blob = `blob:${window.location.origin}/local-preview`;
    expect(sanitizeBlobUrl(blob, { origin: window.location.origin })).toBe(blob);
  });

  it("strips path/control/header-breaking filename and ID characters", () => {
    expect(sanitizeFilename("..\\secret\r\nContent-Disposition: attachment; filename=x")).not.toMatch(/[\\/\r\n]/);
    expect(sanitizeId('x" onerror="alert(1)')).toBe("xonerroralert1");
  });

  it("renders hostile transcripts, IDs, models, errors, diffs, and filenames as inert text", () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    const attack = hostileCorpus.join("\n") + '\n" autofocus onfocus="window.__codexXss=1';
    window.__codexXss = 0;
    const fetchSpy = vi.spyOn(window, "fetch").mockRejectedValue(new Error("unexpected request"));

    panel._selectedThreadId = 'thr_1" onmouseover="window.__codexXss=1';
    panel._selectedProjectId = 'prj_1" onclick="window.__codexXss=1';
    panel._projects = [{
      project_id: panel._selectedProjectId,
      kind: "project",
      name: attack,
      root_path: "C:/workspace/<img src=x onerror=window.__codexXss=1>",
      default_model: attack,
      default_thinking_level: "medium",
      archived_at: null,
    }];
    panel._threads = [{
      thread_id: panel._selectedThreadId,
      project_id: panel._selectedProjectId,
      project_kind: "project",
      title: attack,
      effective_model: attack,
      effective_thinking_level: "medium",
      status: "error",
      archived_at: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }];
    panel._activeThread = { ...panel._threads[0], last_error: attack, attachments: [] };
    panel._status = {
      model_catalog: {
        default_model: attack,
        default_thinking_level: "medium",
        models: [{ model: attack, display_name: attack, thinking_levels: ["medium"] }],
      },
      diagnostics: { tools: [{ name: attack, path: attack, available: false }], last_error: attack },
    };
    panel._events = [{
      event_id: "evt_1",
      thread_id: "thr_1",
      sequence: 1,
      event_type: "message.completed",
      payload: { text: `${attack}\n\n\`\`\`diff\n-${attack}\n\`\`\`` },
      timestamp: "2026-01-01T00:00:00Z",
    }];
    panel._forceMessageRebuild = true;
    panel._artifacts = [{
      artifact_id: 'art_1" onpointerenter="window.__codexXss=1',
      filename: attack,
      relative_path: attack,
      mime_type: "text/plain",
      size_bytes: 42,
    }];
    panel._selectedArtifactId = panel._artifacts[0].artifact_id;

    panel._renderProjectList();
    panel._renderToolbar();
    panel._renderStatusBanner();
    panel._renderMessages();
    panel._renderArtifacts();
    panel._renderDiagnostics();

    const root = panel.shadowRoot;
    expect(root.querySelector("script, iframe, object, embed, [srcdoc], [onerror], [onclick], [onfocus]"))
      .toBeNull();
    expect(root.querySelector('img[src^="http"], img[src^="data:"]')).toBeNull();
    expect(root.getElementById("message-list").textContent).toContain("<script>");
    expect(root.getElementById("artifact-list").textContent).toContain("<script>");
    expect(window.__codexXss).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("never embeds hostile SVG, HTML, or PDF artifacts", () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    for (const [filename, mime] of [
      ["attack.svg", "image/svg+xml"],
      ["attack.html", "text/html"],
      ["attack.pdf", "application/pdf"],
    ]) {
      const artifact = makeArtifact({ artifact_id: `art_${filename}`, filename, mime_type: mime });
      panel._selectedArtifactId = artifact.artifact_id;
      panel._artifactPreview = previewDescriptor(artifact, new Blob(["hostile"], { type: mime }));
      panel._renderArtifactPreview();
      expect(panel.shadowRoot.getElementById("artifact-preview").querySelector("img, iframe, object, embed"))
        .toBeNull();
    }
  });

  it("encodes every artifact route segment before a same-origin preview request", async () => {
    const panel = document.createElement("codex-bridge-panel");
    document.body.append(panel);
    panel._selectedThreadId = "thr/a?query#fragment";
    panel._selectedArtifactId = "art/../file?download#fragment";
    panel._artifacts = [makeArtifact({
      artifact_id: panel._selectedArtifactId,
      filename: "safe.txt",
      mime_type: "text/plain",
    })];
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
      new Response("safe", { status: 200, headers: { "Content-Type": "text/plain" } })
    );

    await panel._loadArtifactPreview(panel._selectedArtifactId);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/codex_bridge/threads/thr%2Fa%3Fquery%23fragment/artifacts/art%2F..%2Ffile%3Fdownload%23fragment",
      expect.any(Object)
    );
    fetchSpy.mockRestore();
  });
});
