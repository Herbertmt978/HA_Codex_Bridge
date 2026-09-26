import { Blob } from "node:buffer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchHomeAssistantApi } from "../src/ha-http.js";
import { fetchInlineImage, inlineImageEndpoint, INLINE_IMAGE_MAX_BYTES } from "../src/inline-images.js";
import { uploadResumableFile, UPLOAD_CHUNK_BYTES } from "../src/uploads.js";
import "../src/codex-bridge-panel.js";

const path = "/api/codex_bridge/threads/chat/uploads";
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// Match HA's public fetchWithAuth boundary: the existing Auth instance owns
// refresh; the raw request gets its current access token and is never replayed.
function haOwner(rawFetch = vi.fn().mockResolvedValue(new Response("ok"))) {
  const auth = { expired: true, accessToken: "expired-test-value", refreshAccessToken: vi.fn(async () => {
    auth.expired = false;
    auth.accessToken = "fresh-test-value";
  }) };
  const hass = { auth, fetchWithAuth: vi.fn(async (url, init) => {
    if (auth.expired) await auth.refreshAccessToken();
    init.credentials = "same-origin";
    init.headers.authorization = `Bearer ${auth.accessToken}`;
    return rawFetch(url, init);
  }) };
  return { auth, hass, rawFetch };
}

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

describe("HA-owned Bridge HTTP authentication", () => {
  it.each([
    { Authorization: "stale-test-value", Range: "bytes=0-31", "Content-Type": "application/octet-stream" },
    new Headers({ authorization: "stale-test-value", Range: "bytes=0-31", "Content-Type": "application/octet-stream" }),
    [["AUTHORIZATION", "stale-test-value"], ["Range", "bytes=0-31"], ["Content-Type", "application/octet-stream"]],
  ])("refreshes through HA without stale duplicate headers or modifying caller headers (%#)", async (headers) => {
    const { auth, hass, rawFetch } = haOwner();
    const fallback = vi.fn();
    const body = new Blob(["bytes"]);
    const signal = new AbortController().signal;
    const before = Array.from(new Headers(headers).entries());
    await fetchHomeAssistantApi(hass, path, { method: "PUT", headers, body, signal, cache: "no-store", credentials: "include", mode: "cors", redirect: "follow" }, { fetchImpl: fallback, accessToken: () => "stale-test-value" });
    expect(auth.refreshAccessToken).toHaveBeenCalledOnce();
    expect(hass.fetchWithAuth).toHaveBeenCalledOnce();
    expect(fallback).not.toHaveBeenCalled();
    const request = rawFetch.mock.calls[0][1];
    expect(request).toMatchObject({ method: "PUT", body, signal, cache: "no-store", credentials: "same-origin", mode: "same-origin", redirect: "error" });
    expect(Object.keys(request.headers).filter((key) => key.toLowerCase() === "authorization")).toEqual(["authorization"]);
    expect(new Headers(request.headers).get("authorization")).toBe("Bearer fresh-test-value");
    expect(new Headers(request.headers).get("range")).toBe("bytes=0-31");
    expect(new Headers(request.headers).get("content-type")).toBe("application/octet-stream");
    expect(Array.from(new Headers(headers).entries())).toEqual(before);
  });

  it.each(["GET", "POST", "PUT", "DELETE"])("returns a 401 for %s without replaying or switching authentication owners", async (method) => {
    const { hass, rawFetch } = haOwner(vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const fallback = vi.fn();
    expect((await fetchHomeAssistantApi(hass, path, { method }, { fetchImpl: fallback })).status).toBe(401);
    expect(rawFetch).toHaveBeenCalledOnce();
    expect(hass.fetchWithAuth).toHaveBeenCalledOnce();
    expect(fallback).not.toHaveBeenCalled();
  });

  it("does not fall back or replay when the HA request rejects", async () => {
    const error = new Error("connection interrupted");
    const { hass } = haOwner(vi.fn().mockRejectedValue(error));
    const fallback = vi.fn();
    await expect(fetchHomeAssistantApi(hass, path, { method: "POST" }, { fetchImpl: fallback })).rejects.toBe(error);
    expect(hass.fetchWithAuth).toHaveBeenCalledOnce();
    expect(fallback).not.toHaveBeenCalled();
  });

  it.each(["https://example.test/api/codex_bridge/threads/chat/uploads", "//example.test/api/codex_bridge/threads/chat/uploads", "/api/config", "/api/codex_bridge/../config", "/api/codex_bridge/%2e%2e/config", "/api/codex_bridge/threads/chat%2Fescape/uploads", "/api/codex_bridge/threads/chat%5Cescape/uploads", "/api/codex_bridge/threads/chat/uploads?token=value", "/api/codex_bridge/threads/chat/uploads#hash", "/api/codex_bridge/threads/chat/uploads\n", "/api/codex_bridge/threads//uploads", "/api/codex_bridge/%zz"])("rejects an unconfined path before either request owner: %s", async (url) => {
    const { hass } = haOwner();
    const fallback = vi.fn();
    await expect(fetchHomeAssistantApi(hass, url, {}, { fetchImpl: fallback })).rejects.toThrow("Invalid Home Assistant API path");
    expect(hass.fetchWithAuth).not.toHaveBeenCalled();
    expect(fallback).not.toHaveBeenCalled();
  });

  it("preserves cancellation before authentication begins", async () => {
    const { auth, hass } = haOwner();
    const controller = new AbortController(); controller.abort();
    await expect(fetchHomeAssistantApi(hass, path, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(auth.refreshAccessToken).not.toHaveBeenCalled();
    expect(hass.fetchWithAuth).not.toHaveBeenCalled();
  });

  it("legacy hosts share their Auth refresh and read the new token only afterwards", async () => {
    let complete;
    const pending = new Promise((resolve) => { complete = resolve; });
    const auth = { expired: true, accessToken: "expired-test-value", refreshAccessToken: vi.fn(() => pending) };
    const rawFetch = vi.fn().mockResolvedValue(new Response("ok"));
    const options = { fetchImpl: rawFetch, accessToken: () => auth.accessToken };
    const one = fetchHomeAssistantApi({ connection: { options: { auth } } }, path, {}, options);
    const two = fetchHomeAssistantApi({ auth }, path, {}, options);
    await Promise.resolve();
    expect(auth.refreshAccessToken).toHaveBeenCalledOnce();
    expect(rawFetch).not.toHaveBeenCalled();
    auth.expired = false; auth.accessToken = "fresh-test-value"; complete();
    await Promise.all([one, two]);
    expect(rawFetch).toHaveBeenCalledTimes(2);
    for (const [, request] of rawFetch.mock.calls) expect(request.headers.Authorization).toBe("Bearer fresh-test-value");
  });

  it("legacy refresh cancellation prevents a subsequent request", async () => {
    let complete;
    const auth = { expired: true, refreshAccessToken: vi.fn(() => new Promise((resolve) => { complete = resolve; })) };
    const rawFetch = vi.fn();
    const controller = new AbortController();
    const request = fetchHomeAssistantApi({ auth }, path, { signal: controller.signal }, { fetchImpl: rawFetch });
    await Promise.resolve(); controller.abort(); complete();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(rawFetch).not.toHaveBeenCalled();
  });

  it.each([null, vi.fn().mockRejectedValue(new Error("private synthetic refresh detail"))])("fails closed when legacy refresh is unavailable or rejected (%#)", async (refreshAccessToken) => {
    const rawFetch = vi.fn();
    await expect(fetchHomeAssistantApi({ auth: { expired: true, refreshAccessToken } }, path, {}, { fetchImpl: rawFetch })).rejects.toThrow(/^Home Assistant sign-in/);
    expect(rawFetch).not.toHaveBeenCalled();
  });

  it("preserves bounded attachment/image requests through HA's auth owner", async () => {
    const { hass, auth, rawFetch } = haOwner(vi.fn().mockResolvedValue(new Response("image", { headers: { "Content-Length": "5" } })));
    const signal = new AbortController().signal;
    const blob = await fetchInlineImage(inlineImageEndpoint("chat:1", "attachment", "image"), { signal, fetchImpl: (url, init) => fetchHomeAssistantApi(hass, url, init) });
    expect(blob.size).toBe(5);
    expect(auth.refreshAccessToken).toHaveBeenCalledOnce();
    expect(rawFetch).toHaveBeenCalledWith("/api/codex_bridge/threads/chat%3A1/attachments/image", expect.objectContaining({ signal, headers: { Range: `bytes=0-${INLINE_IMAGE_MAX_BYTES - 1}`, authorization: "Bearer fresh-test-value" }, mode: "same-origin", redirect: "error" }));
  });
});

describe("panel file consumers use fresh HA authentication", () => {
  it("uploads an actual file, refreshing again if the session token expires before the chunk", async () => {
    const session = { upload_id: "upload", chunk_size: UPLOAD_CHUNK_BYTES, total_chunks: 1, received_indices: [], status: "active" };
    const { hass, auth, rawFetch } = haOwner(vi.fn(async (url) => {
      if (url.endsWith("/uploads")) { auth.expired = true; return json(session, 201); }
      if (url.endsWith("/chunks/0")) return json({ ...session, received_indices: [0] });
      return json({ attachment_id: "attachment" }, 201);
    }));
    const panel = document.createElement("codex-bridge-panel"); panel._hass = hass;
    const file = new Blob(["hello"], { type: "text/plain" }); file.name = "hello.txt";
    const signal = new AbortController().signal;
    const result = await panel._uploadSingleFile(file, { relativePath: "hello.txt", threadId: "chat", signal });
    expect(result.attachment.attachment_id).toBe("attachment");
    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(2);
    expect(rawFetch.mock.calls.map(([, init]) => init.method)).toEqual(["POST", "PUT", "POST"]);
    expect(rawFetch.mock.calls[1][1]).toMatchObject({ signal, headers: { "Upload-Offset": "0", "Content-Type": "application/octet-stream", authorization: "Bearer fresh-test-value" } });
    expect(rawFetch.mock.calls[1][1].body.size).toBe(5);
  });

  it("does not reconcile or replay a chunk's HTTP 401", async () => {
    const rawFetch = vi.fn(async (url) => url.endsWith("/uploads") ? json({ upload_id: "upload", chunk_size: UPLOAD_CHUNK_BYTES, total_chunks: 1, received_indices: [], status: "active" }, 201) : new Response(null, { status: 401 }));
    const { hass } = haOwner(rawFetch);
    const file = new Blob(["hello"]); file.name = "hello.txt";
    await expect(uploadResumableFile({ file, threadId: "chat", fetchImpl: (url, init) => fetchHomeAssistantApi(hass, url, init), retryDelay: 0 })).rejects.toMatchObject({ status: 401 });
    expect(rawFetch.mock.calls.map(([, init]) => init.method)).toEqual(["POST", "PUT"]);
  });

  it("refreshes preview and download independently, preserving preview Range and full download", async () => {
    const { hass, auth, rawFetch } = haOwner(vi.fn(() => new Response("hello", { headers: { "Content-Type": "text/plain", "Content-Disposition": 'attachment; filename="hello.txt"' } })));
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel); panel._hass = hass;
    panel._selectedThreadId = "chat"; panel._selectedArtifactId = "artifact";
    panel._artifacts = [{ artifact_id: "artifact", filename: "hello.txt", mime_type: "text/plain", size_bytes: 5 }];
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:http://localhost/preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    await panel._loadArtifactPreview("artifact");
    expect(panel._artifactPreview.text).toBe("hello");
    // A preview can be saved from its cached bytes. A different file exercises
    // full download authentication rather than that existing zero-request path.
    panel._artifacts.push({ artifact_id: "download", filename: "hello.txt", mime_type: "text/plain", size_bytes: 5 });
    auth.expired = true;
    await panel._downloadArtifact("download");
    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(2);
    expect(rawFetch).toHaveBeenCalledTimes(2);
    expect(rawFetch.mock.calls[0][1].headers.Range).toBe("bytes=0-4");
    expect(rawFetch.mock.calls[1][1].headers.Range).toBeUndefined();
    expect(panel._preparedArtifactDownload.filename).toBe("hello.txt");
  });

  it("the panel's inline image controller delegates attachment and artifact reads to the current HA owner", async () => {
    const { hass } = haOwner();
    const panel = document.createElement("codex-bridge-panel"); document.body.append(panel); panel._hass = hass; panel._selectedThreadId = "chat";
    const controller = panel._inlineImages();
    for (const kind of ["attachments", "artifacts"]) await controller.fetchImpl(`/api/codex_bridge/threads/chat/${kind}/image`, { headers: { Range: "bytes=0-31" } });
    expect(hass.fetchWithAuth).toHaveBeenCalledTimes(2);
  });
});
