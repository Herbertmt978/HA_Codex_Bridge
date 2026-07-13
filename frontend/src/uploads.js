/** Browser-side client for Home Assistant's API v1 resumable upload views. */

export const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;

const SHA256_WORDS = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export class UploadError extends Error {
  constructor(code, { status = null, retryable = false } = {}) {
    super(status === null ? `Upload ${code.replaceAll("_", " ")}` : `Upload failed (HTTP ${status})`);
    this.name = "UploadError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

class IncrementalSha256 {
  constructor() {
    this._state = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    this._tail = new Uint8Array(0);
    this._length = 0;
    this._workspace = new Uint32Array(64);
  }

  update(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    this._length += bytes.byteLength;
    let position = 0;
    if (this._tail.length) {
      const missing = 64 - this._tail.length;
      if (bytes.length < missing) {
        this._tail = appendBytes(this._tail, bytes);
        return;
      }
      const block = new Uint8Array(64);
      block.set(this._tail);
      block.set(bytes.subarray(0, missing), this._tail.length);
      this._compress(block);
      this._tail = new Uint8Array(0);
      position = missing;
    }
    while (position + 64 <= bytes.length) {
      this._compress(bytes.subarray(position, position + 64));
      position += 64;
    }
    if (position < bytes.length) {
      this._tail = bytes.slice(position);
    }
  }

  hexDigest() {
    const totalBits = BigInt(this._length) * 8n;
    const paddingLength = this._tail.length < 56 ? 64 : 128;
    const finalBlock = new Uint8Array(paddingLength);
    finalBlock.set(this._tail);
    finalBlock[this._tail.length] = 0x80;
    for (let index = 0; index < 8; index += 1) {
      finalBlock[finalBlock.length - 1 - index] = Number((totalBits >> BigInt(index * 8)) & 0xffn);
    }
    for (let position = 0; position < finalBlock.length; position += 64) {
      this._compress(finalBlock.subarray(position, position + 64));
    }
    return Array.from(this._state, (word) => word.toString(16).padStart(8, "0")).join("");
  }

  _compress(block) {
    const words = this._workspace;
    for (let index = 0; index < 16; index += 1) {
      const offset = index * 4;
      words[index] = (
        (block[offset] << 24) |
        (block[offset + 1] << 16) |
        (block[offset + 2] << 8) |
        block[offset + 3]
      ) >>> 0;
    }
    for (let index = 16; index < 64; index += 1) {
      const gamma0 = rightRotate(words[index - 15], 7) ^ rightRotate(words[index - 15], 18) ^ (words[index - 15] >>> 3);
      const gamma1 = rightRotate(words[index - 2], 17) ^ rightRotate(words[index - 2], 19) ^ (words[index - 2] >>> 10);
      words[index] = (words[index - 16] + gamma0 + words[index - 7] + gamma1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = this._state;
    for (let index = 0; index < 64; index += 1) {
      const sigma1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const choose = (e & f) ^ (~e & g);
      const temp1 = (h + sigma1 + choose + SHA256_WORDS[index] + words[index]) >>> 0;
      const sigma0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sigma0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }
    this._state[0] = (this._state[0] + a) >>> 0;
    this._state[1] = (this._state[1] + b) >>> 0;
    this._state[2] = (this._state[2] + c) >>> 0;
    this._state[3] = (this._state[3] + d) >>> 0;
    this._state[4] = (this._state[4] + e) >>> 0;
    this._state[5] = (this._state[5] + f) >>> 0;
    this._state[6] = (this._state[6] + g) >>> 0;
    this._state[7] = (this._state[7] + h) >>> 0;
  }
}

/** Hash a Blob/File in fixed slices so a 100 MiB file never becomes one buffer. */
export async function sha256File(file, { chunkSize = UPLOAD_CHUNK_BYTES, signal } = {}) {
  if (!Number.isSafeInteger(chunkSize) || chunkSize < 1 || chunkSize > UPLOAD_CHUNK_BYTES) {
    throw new UploadError("invalid_chunk_size");
  }
  const hasher = new IncrementalSha256();
  for (let offset = 0; offset < file.size; offset += chunkSize) {
    throwIfAborted(signal);
    hasher.update(await blobBytes(file.slice(offset, Math.min(file.size, offset + chunkSize)), signal));
  }
  return hasher.hexDigest();
}

/** Cancel is explicit; transient retries deliberately retain the durable session. */
export async function cancelResumableUpload({ threadId, uploadId, accessToken = "", fetchImpl = fetch, signal } = {}) {
  const url = uploadUrl(threadId, uploadId);
  return requestJson(fetchImpl, url, {
    method: "DELETE",
    headers: authorizationHeaders(accessToken),
    signal,
  }, [200]);
}

/**
 * Upload one File through only relative, authenticated Home Assistant endpoints.
 * Supplying uploadId reuses a durable session after a connection interruption while
 * the browser still holds the user-selected File.
 */
export async function uploadResumableFile({
  file,
  threadId,
  relativePath,
  uploadId = null,
  accessToken = "",
  fetchImpl = fetch,
  signal,
  onProgress = () => {},
  retryAttempts = 2,
  retryDelay = 250,
} = {}) {
  const safeFile = validateFile(file);
  const safeThreadId = validateIdentifier(threadId, "invalid_thread");
  const safePath = normaliseRelativePath(relativePath ?? safeFile.name, safeFile.name);
  if (!Number.isSafeInteger(retryAttempts) || retryAttempts < 0 || retryAttempts > 5) {
    throw new UploadError("invalid_retry_attempts");
  }
  const report = (value) => onProgress({ totalBytes: safeFile.size, ...value });
  report({ status: "hashing", completedBytes: 0 });
  const fileDigest = await sha256File(safeFile, { signal });
  throwIfAborted(signal);

  let session;
  if (uploadId) {
    session = await getUploadSession(fetchImpl, safeThreadId, uploadId, accessToken, signal);
    assertSessionMatchesFile(session, safeFile, safePath, fileDigest);
  } else {
    session = await requestJson(fetchImpl, uploadUrl(safeThreadId), {
      method: "POST",
      headers: {
        ...authorizationHeaders(accessToken),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        filename: safeFile.name,
        mime_type: safeFile.type || "application/octet-stream",
        relative_path: safePath,
        size_bytes: safeFile.size,
        sha256: fileDigest,
      }),
      signal,
    }, [201]);
  }

  session = validateSession(session, safeFile.size);
  if (session.status === "cancelled") {
    throw new UploadError("session_cancelled");
  }
  if (session.status === "completed") {
    report({ status: "completed", completedBytes: safeFile.size, uploadId: session.upload_id });
    return { upload: session, attachment: null };
  }
  while (session.status === "active") {
    const index = firstMissingIndex(session);
    if (index === session.total_chunks) {
      break;
    }
    const offset = index * session.chunk_size;
    const chunk = safeFile.slice(offset, Math.min(safeFile.size, offset + session.chunk_size));
    const chunkDigest = await sha256File(chunk, { chunkSize: Math.min(chunk.size, UPLOAD_CHUNK_BYTES), signal });
    report({ status: "uploading", completedBytes: Math.min(offset, safeFile.size), uploadId: session.upload_id });
    session = await putChunkWithReconcile({
      fetchImpl,
      threadId: safeThreadId,
      session,
      index,
      offset,
      chunk,
      chunkDigest,
      accessToken,
      signal,
      retryAttempts,
      retryDelay,
      fileSize: safeFile.size,
    });
    report({
      status: "uploading",
      completedBytes: Math.min(firstMissingIndex(session) * session.chunk_size, safeFile.size),
      uploadId: session.upload_id,
    });
  }

  if (session.status === "completed") {
    report({ status: "completed", completedBytes: safeFile.size, uploadId: session.upload_id });
    return { upload: session, attachment: null };
  }
  if (session.status === "cancelled") {
    throw new UploadError("session_cancelled");
  }

  let attachment;
  try {
    attachment = await requestJson(fetchImpl, `${uploadUrl(safeThreadId, session.upload_id)}/complete`, {
      method: "POST",
      headers: authorizationHeaders(accessToken),
      signal,
    }, [201]);
  } catch (error) {
    if (!isReconciliable(error)) {
      throw error;
    }
    session = await getUploadSession(fetchImpl, safeThreadId, session.upload_id, accessToken, signal);
    if (session.status !== "completed") {
      throw error;
    }
    attachment = null;
  }
  report({ status: "completed", completedBytes: safeFile.size, uploadId: session.upload_id });
  return { upload: session, attachment };
}

async function putChunkWithReconcile(options) {
  const {
    fetchImpl, threadId, session, index, offset, chunk, chunkDigest,
    accessToken, signal, retryAttempts, retryDelay, fileSize,
  } = options;
  let latest = session;
  let lastError;
  for (let attempt = 0; attempt <= retryAttempts; attempt += 1) {
    throwIfAborted(signal);
    try {
      return validateSession(await requestJson(fetchImpl, `${uploadUrl(threadId, latest.upload_id)}/chunks/${index}`, {
        method: "PUT",
        headers: {
          ...authorizationHeaders(accessToken),
          "Content-Type": "application/octet-stream",
          "Upload-Offset": String(offset),
          "X-Chunk-SHA256": chunkDigest,
        },
        body: chunk,
        signal,
      }, [200]), fileSize);
    } catch (error) {
      lastError = error;
      if (!isReconciliable(error)) {
        throw error;
      }
      try {
        latest = await getUploadSession(fetchImpl, threadId, latest.upload_id, accessToken, signal);
      } catch (statusError) {
        if (attempt === retryAttempts || !isReconciliable(statusError)) {
          throw lastError;
        }
      }
      if (latest.status === "completed" || receivedIndices(latest).includes(index)) {
        return latest;
      }
      if (attempt === retryAttempts) {
        throw lastError;
      }
      await delay(retryDelay, signal);
    }
  }
  throw lastError || new UploadError("network_error", { retryable: true });
}

async function getUploadSession(fetchImpl, threadId, uploadId, accessToken, signal) {
  return validateSession(await requestJson(fetchImpl, uploadUrl(threadId, uploadId), {
    method: "GET",
    headers: authorizationHeaders(accessToken),
    signal,
  }, [200]));
}

function validateSession(session, expectedSize = null) {
  if (!session || typeof session !== "object" || typeof session.upload_id !== "string" || !session.upload_id) {
    throw new UploadError("invalid_session");
  }
  if (!Number.isSafeInteger(session.chunk_size) || session.chunk_size < 1 || session.chunk_size > UPLOAD_CHUNK_BYTES ||
      !Number.isSafeInteger(session.total_chunks) || session.total_chunks < 1 ||
      !Array.isArray(session.received_indices) || !["active", "completed", "cancelled"].includes(session.status)) {
    throw new UploadError("invalid_session");
  }
  if (expectedSize !== null && session.size_bytes !== undefined && session.size_bytes !== expectedSize) {
    throw new UploadError("session_mismatch");
  }
  const indices = receivedIndices(session);
  if (indices.some((index, position) => index !== position || index >= session.total_chunks)) {
    throw new UploadError("invalid_session");
  }
  return session;
}

function assertSessionMatchesFile(session, file, relativePath, sha256) {
  validateSession(session, file.size);
  if (session.filename !== file.name || session.relative_path !== relativePath || session.sha256 !== sha256) {
    throw new UploadError("session_mismatch");
  }
}

function firstMissingIndex(session) {
  return receivedIndices(session).length;
}

function receivedIndices(session) {
  return [...session.received_indices].sort((left, right) => left - right);
}

function validateFile(file) {
  if (!file || typeof file.name !== "string" || !Number.isSafeInteger(file.size) || file.size < 1 || typeof file.slice !== "function") {
    throw new UploadError("invalid_file");
  }
  if (file.name.includes("/") || file.name.includes("\\") || containsControl(file.name)) {
    throw new UploadError("invalid_path");
  }
  return file;
}

function normaliseRelativePath(value, filename) {
  if (typeof value !== "string" || !value || containsControl(value)) {
    throw new UploadError("invalid_path");
  }
  const path = value.replaceAll("\\", "/");
  if (path.startsWith("/") || /^[a-zA-Z]:/.test(path)) {
    throw new UploadError("invalid_path");
  }
  const parts = path.split("/");
  if (parts.length > 16 || parts.some((part) => !part || part === "." || part === "..") || parts.at(-1) !== filename) {
    throw new UploadError("invalid_path");
  }
  return path;
}

function validateIdentifier(value, code) {
  if (typeof value !== "string" || !value || containsControl(value)) {
    throw new UploadError(code);
  }
  return value;
}

function uploadUrl(threadId, uploadId = null) {
  const root = `/api/codex_bridge/threads/${encodeURIComponent(threadId)}/uploads`;
  return uploadId ? `${root}/${encodeURIComponent(validateIdentifier(uploadId, "invalid_upload"))}` : root;
}

function authorizationHeaders(accessToken) {
  return typeof accessToken === "string" && accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

async function requestJson(fetchImpl, url, init, expectedStatuses) {
  throwIfAborted(init.signal);
  let response;
  try {
    response = await fetchImpl(url, init);
  } catch (error) {
    if (init.signal?.aborted || error?.name === "AbortError") {
      throw new UploadError("aborted");
    }
    throw new UploadError("network_error", { retryable: true });
  }
  if (!expectedStatuses.includes(response.status)) {
    throw new UploadError("http_error", { status: response.status, retryable: response.status >= 500 || response.status === 409 });
  }
  try {
    return await response.json();
  } catch {
    throw new UploadError("invalid_response");
  }
}

function isReconciliable(error) {
  return error instanceof UploadError && error.retryable;
}

function throwIfAborted(signal) {
  if (signal?.aborted) {
    throw new UploadError("aborted");
  }
}

function delay(milliseconds, signal) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) {
    throwIfAborted(signal);
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", aborted);
      resolve();
    }, milliseconds);
    const aborted = () => {
      clearTimeout(timer);
      reject(new UploadError("aborted"));
    };
    signal?.addEventListener("abort", aborted, { once: true });
  });
}

async function blobBytes(blob, signal) {
  throwIfAborted(signal);
  try {
    return new Uint8Array(await blob.arrayBuffer());
  } catch (error) {
    if (signal?.aborted || error?.name === "AbortError") {
      throw new UploadError("aborted");
    }
    throw new UploadError("invalid_file");
  }
}

function appendBytes(left, right) {
  const combined = new Uint8Array(left.length + right.length);
  combined.set(left);
  combined.set(right, left.length);
  return combined;
}

function rightRotate(value, amount) {
  return (value >>> amount) | (value << (32 - amount));
}

function containsControl(value) {
  return [...value].some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint <= 31 || codePoint === 127;
  });
}
