import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import {
  UPLOAD_CHUNK_BYTES,
  UploadError,
  cancelResumableUpload,
  sha256File,
  uploadResumableFile,
} from "../src/uploads.js";

const encoder = new TextEncoder();

function fileFromBytes(name, bytes, type = "text/plain") {
  const blob = new Blob([bytes], { type });
  return {
    name,
    size: blob.size,
    type,
    slice: blob.slice.bind(blob),
    arrayBuffer() {
      throw new Error("whole-file buffering is forbidden");
    },
  };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("sha256File", () => {
  it("hashes a file through bounded slices without calling File.arrayBuffer", async () => {
    const file = fileFromBytes("hello.txt", encoder.encode("hello"));

    await expect(sha256File(file, { chunkSize: 2 })).resolves.toBe(
      "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
  });

  it.each([0, 1, 55, 56, 63, 64, 65, 1024])("matches Node SHA-256 across the %i-byte boundary", async (size) => {
    const bytes = Uint8Array.from({ length: size }, (_, index) => (index * 37 + 11) % 256);
    const expected = createHash("sha256").update(bytes).digest("hex");
    const blob = new Blob([bytes]);

    await expect(sha256File(blob, { chunkSize: 17 })).resolves.toBe(expected);
  });
});

describe("uploadResumableFile", () => {
  it("creates, reconciles an idempotently retried chunk, and completes using relative HA routes", async () => {
    const file = fileFromBytes("notes.txt", encoder.encode("hello"));
    const requests = [];
    let chunkAttempts = 0;
    const fetchImpl = vi.fn(async (url, init = {}) => {
      requests.push({ url: String(url), init });
      if (String(url).endsWith("/uploads") && init.method === "POST") {
        return jsonResponse(
          {
            upload_id: "upl_123",
            chunk_size: UPLOAD_CHUNK_BYTES,
            total_chunks: 1,
            received_indices: [],
            next_offset: 0,
            status: "active",
          },
          201
        );
      }
      if (String(url).endsWith("/chunks/0")) {
        chunkAttempts += 1;
        if (chunkAttempts === 1) {
          throw new TypeError("network disconnected");
        }
        return jsonResponse({
          upload_id: "upl_123",
          chunk_size: UPLOAD_CHUNK_BYTES,
          total_chunks: 1,
          received_indices: [0],
          next_offset: UPLOAD_CHUNK_BYTES,
          status: "active",
        });
      }
      if (String(url).endsWith("/uploads/upl_123") && init.method === "GET") {
        return jsonResponse({
          upload_id: "upl_123",
          chunk_size: UPLOAD_CHUNK_BYTES,
          total_chunks: 1,
          received_indices: [],
          next_offset: 0,
          status: "active",
        });
      }
      if (String(url).endsWith("/complete")) {
        return jsonResponse({ attachment_id: "att_upl_123" }, 201);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    const progress = [];
    const result = await uploadResumableFile({
      file,
      threadId: "thread 1",
      relativePath: "folder/notes.txt",
      accessToken: "ha-access-token",
      fetchImpl,
      retryDelay: 0,
      onProgress: (value) => progress.push(value),
    });

    expect(result.attachment).toEqual({ attachment_id: "att_upl_123" });
    expect(chunkAttempts).toBe(2);
    expect(requests.map((request) => request.url)).toEqual([
      "/api/codex_bridge/threads/thread%201/uploads",
      "/api/codex_bridge/threads/thread%201/uploads/upl_123/chunks/0",
      "/api/codex_bridge/threads/thread%201/uploads/upl_123",
      "/api/codex_bridge/threads/thread%201/uploads/upl_123/chunks/0",
      "/api/codex_bridge/threads/thread%201/uploads/upl_123/complete",
    ]);
    const createBody = JSON.parse(requests[0].init.body);
    expect(createBody).toMatchObject({
      filename: "notes.txt",
      relative_path: "folder/notes.txt",
      size_bytes: 5,
    });
    expect(requests[1].init.headers).toMatchObject({
      Authorization: "Bearer ha-access-token",
      "Upload-Offset": "0",
      "X-Chunk-SHA256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    });
    expect(requests[1].init.headers).not.toHaveProperty("Content-Length");
    expect(progress.at(-1)).toMatchObject({ completedBytes: 5, totalBytes: 5, status: "completed" });
  });

  it("uploads multiple chunks without mistaking the session total for a partial size", async () => {
    const bytes = new Uint8Array(UPLOAD_CHUNK_BYTES + 1);
    bytes[UPLOAD_CHUNK_BYTES] = 1;
    const file = fileFromBytes("large.bin", bytes, "application/octet-stream");
    let received = [];
    const fetchImpl = vi.fn(async (url, init = {}) => {
      if (String(url).endsWith("/uploads") && init.method === "POST") {
        return jsonResponse({
          upload_id: "upl_large",
          chunk_size: UPLOAD_CHUNK_BYTES,
          total_chunks: 2,
          received_indices: [],
          size_bytes: file.size,
          status: "active",
        }, 201);
      }
      if (String(url).includes("/chunks/")) {
        received = [...received, Number(String(url).split("/").at(-1))];
        return jsonResponse({
          upload_id: "upl_large",
          chunk_size: UPLOAD_CHUNK_BYTES,
          total_chunks: 2,
          received_indices: received,
          size_bytes: file.size,
          status: "active",
        });
      }
      if (String(url).endsWith("/complete")) {
        return jsonResponse({ attachment_id: "att_large" }, 201);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    await expect(uploadResumableFile({ file, threadId: "thread", fetchImpl }))
      .resolves.toMatchObject({ attachment: { attachment_id: "att_large" } });
    expect(received).toEqual([0, 1]);
  });

  it("keeps HA tokens out of URLs and bounded request errors", async () => {
    const file = fileFromBytes("notes.txt", encoder.encode("hello"));
    const token = "secret-ha-token-should-never-leak";
    const fetchImpl = vi.fn(async () => new Response(`failure ${token}`, { status: 500 }));

    await expect(
      uploadResumableFile({ file, threadId: "thread", accessToken: token, fetchImpl })
    ).rejects.toMatchObject({ code: "http_error", status: 500 });

    try {
      await uploadResumableFile({ file, threadId: "thread", accessToken: token, fetchImpl });
    } catch (error) {
      expect(error).toBeInstanceOf(UploadError);
      expect(error.message).not.toContain(token);
      expect(String(error)).not.toContain(token);
    }
    expect(fetchImpl.mock.calls[0][0]).not.toContain(token);
  });

  it("rejects unsafe relative paths before creating a session", async () => {
    const fetchImpl = vi.fn();

    await expect(
      uploadResumableFile({
        file: fileFromBytes("notes.txt", encoder.encode("hello")),
        threadId: "thread",
        relativePath: "../../notes.txt",
        fetchImpl,
      })
    ).rejects.toMatchObject({ code: "invalid_path" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("cancels a known session through the authenticated same-origin route", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ status: "cancelled" }));

    await expect(
      cancelResumableUpload({
        threadId: "thread",
        uploadId: "upl_123",
        accessToken: "ha-access-token",
        fetchImpl,
      })
    ).resolves.toEqual({ status: "cancelled" });
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/codex_bridge/threads/thread/uploads/upl_123",
      expect.objectContaining({ method: "DELETE", headers: { Authorization: "Bearer ha-access-token" } })
    );
  });

  it("resumes a completed session without posting completion twice", async () => {
    const bytes = encoder.encode("hello");
    const file = fileFromBytes("notes.txt", bytes);
    const digest = createHash("sha256").update(bytes).digest("hex");
    const fetchImpl = vi.fn(async () => jsonResponse({
      upload_id: "upl_done",
      filename: "notes.txt",
      relative_path: "notes.txt",
      sha256: digest,
      size_bytes: file.size,
      chunk_size: UPLOAD_CHUNK_BYTES,
      total_chunks: 1,
      received_indices: [0],
      status: "completed",
    }));

    await expect(uploadResumableFile({ file, threadId: "thread", uploadId: "upl_done", fetchImpl }))
      .resolves.toMatchObject({ upload: { status: "completed" }, attachment: null });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0][1].method).toBe("GET");
  });
});
