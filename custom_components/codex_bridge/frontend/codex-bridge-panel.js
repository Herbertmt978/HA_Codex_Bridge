// frontend/src/safe-dom.js
var RASTER_MIME_TYPES = /* @__PURE__ */ new Set([
  "image/avif",
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp"
]);
var TEXT_EXTENSIONS = /* @__PURE__ */ new Set([
  "c",
  "cfg",
  "conf",
  "cpp",
  "css",
  "csv",
  "diff",
  "env",
  "h",
  "ini",
  "js",
  "json",
  "log",
  "md",
  "py",
  "rst",
  "sh",
  "sql",
  "text",
  "toml",
  "ts",
  "tsx",
  "txt",
  "yaml",
  "yml"
]);
function removeControlChars(value) {
  return [...value].filter((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint > 31 && codePoint !== 127;
  }).join("");
}
function sanitizeId(value, fallback = "") {
  const text = removeControlChars(String(value ?? "")).replace(/[^A-Za-z0-9_.:-]/g, "").trim();
  return text.slice(0, 200) || fallback;
}
function sanitizeFilename(value, fallback = "download") {
  const text = removeControlChars(String(value ?? "")).replace(/[\\/]/g, "_").replace(/["']/g, "").trim().replace(/^\.+$/, "");
  return (text || fallback).slice(0, 255);
}
function sanitizeBlobUrl(value, { origin } = {}) {
  if (typeof value !== "string" || !value.startsWith("blob:")) return null;
  try {
    const parsed = new URL(value);
    if (origin && parsed.origin !== origin) return null;
    return parsed.href;
  } catch {
    return null;
  }
}
function extensionOf(filename) {
  const name = String(filename ?? "").toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : "";
}
function previewDescriptor(artifact = {}, blob = {}) {
  const mime = String(blob?.type || artifact?.mime_type || "").toLowerCase().split(";", 1)[0].trim();
  const filename = sanitizeFilename(artifact?.filename || artifact?.relative_path || "artifact", "artifact");
  const base = { artifactId: sanitizeId(artifact?.artifact_id), filename, contentType: mime || "application/octet-stream" };
  if (RASTER_MIME_TYPES.has(mime)) return { ...base, kind: "image", url: null };
  if (mime.startsWith("text/") && mime !== "text/html" && mime !== "text/xml") return { ...base, kind: "text", text: "" };
  if (TEXT_EXTENSIONS.has(extensionOf(filename)) && !mime.includes("html") && !mime.includes("svg") && !mime.includes("xml")) {
    return { ...base, kind: "text", text: "" };
  }
  return { ...base, kind: "binary" };
}
function createPreviewElement(document2, descriptor, { blobUrl } = {}) {
  if (!document2 || !descriptor) return null;
  if (descriptor.kind === "text") {
    const pre = document2.createElement("pre");
    pre.textContent = String(descriptor.text ?? "");
    return pre;
  }
  if (descriptor.kind === "image") {
    const url = sanitizeBlobUrl(blobUrl || descriptor.url, { origin: document2.defaultView?.location?.origin });
    if (!url || !RASTER_MIME_TYPES.has(descriptor.contentType)) return null;
    const image = document2.createElement("img");
    image.src = url;
    image.alt = descriptor.filename || "artifact preview";
    return image;
  }
  const empty = document2.createElement("div");
  empty.textContent = `${descriptor.filename || "Artifact"} preview unavailable`;
  return empty;
}

// frontend/src/protocol.js
var EVENT_TYPES = /* @__PURE__ */ new Set([
  "message.created",
  "message.completed",
  "run.started",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.queued",
  "run.dequeued",
  "run.queue_cleared",
  "attachment.added",
  "artifact.added",
  "thread.updated",
  "thread.archived",
  "thread.restored",
  "session.bound",
  "bridge.snapshot_required",
  "bridge.error"
]);
var isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
function parseEvent(value, { allowUnknownType = true } = {}) {
  if (!isRecord(value)) return null;
  if (!Number.isSafeInteger(value.sequence) || value.sequence <= 0) return null;
  if (typeof value.event_type !== "string" || !value.event_type || value.event_type.length > 120) return null;
  if (!allowUnknownType && !EVENT_TYPES.has(value.event_type)) return null;
  if (!isRecord(value.payload)) return null;
  if (value.event_id !== void 0 && (typeof value.event_id !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(value.event_id))) return null;
  if (value.thread_id !== void 0 && (typeof value.thread_id !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(value.thread_id))) return null;
  return {
    ...value,
    event_id: value.event_id === void 0 ? void 0 : sanitizeId(value.event_id),
    thread_id: value.thread_id === void 0 ? void 0 : sanitizeId(value.thread_id),
    event_type: value.event_type,
    sequence: value.sequence,
    payload: { ...value.payload }
  };
}
function parseEvents(value, options) {
  if (!Array.isArray(value)) return [];
  return normalizeEvents(value.map((item) => parseEvent(item, options)).filter(Boolean));
}
function normalizeEvents(events, { maxEvents = 1e4 } = {}) {
  const seenSequences = /* @__PURE__ */ new Set();
  const seenIds = /* @__PURE__ */ new Set();
  const valid = [];
  const candidates = [];
  for (const item of Array.isArray(events) ? events : []) {
    const event = item?.sequence ? parseEvent(item) : null;
    if (event) candidates.push(event);
  }
  candidates.sort((left, right) => left.sequence - right.sequence);
  for (const event of candidates) {
    if (seenSequences.has(event.sequence) || event.event_id && seenIds.has(event.event_id)) continue;
    seenSequences.add(event.sequence);
    if (event.event_id) seenIds.add(event.event_id);
    valid.push(event);
  }
  const limit = Number.isFinite(maxEvents) ? Math.max(1, Math.min(1e4, Math.floor(maxEvents))) : 1e4;
  return valid.slice(-limit);
}

// frontend/src/event-stream.js
function createEventStreamState({ cursor = 0, maxEvents = 1e4 } = {}) {
  return {
    cursor: Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0,
    events: [],
    maxEvents: Math.max(1, Math.min(1e4, Number(maxEvents) || 1e4)),
    needsSnapshot: false,
    error: null
  };
}
function acceptEvent(state, value) {
  const current = state || createEventStreamState();
  const event = parseEvent(value);
  if (!event) return { state: current, accepted: false, reason: "invalid" };
  if (event.sequence <= current.cursor) return { state: current, accepted: false, reason: "replay" };
  if (event.event_type === "bridge.snapshot_required") {
    return {
      state: { ...current, cursor: event.sequence, needsSnapshot: true },
      accepted: true,
      control: "snapshot",
      event
    };
  }
  if (event.event_type === "bridge.error") {
    const message = typeof event.payload?.error === "string" ? event.payload.error.slice(0, 1e3) : "Bridge event stream failed";
    return {
      state: { ...current, cursor: event.sequence, error: message },
      accepted: true,
      control: "error",
      event
    };
  }
  if (event.event_id && current.events.some((item) => item.event_id === event.event_id)) {
    return { state: current, accepted: false, reason: "duplicate" };
  }
  const events = normalizeEvents([...current.events, event], { maxEvents: current.maxEvents });
  return {
    state: { ...current, cursor: event.sequence, events, needsSnapshot: false, error: null },
    accepted: true,
    event
  };
}
function acceptEvents(state, values) {
  let next = state || createEventStreamState();
  const accepted = [];
  const controls = [];
  for (const value of normalizeEvents(values)) {
    const result = acceptEvent(next, value);
    next = result.state;
    if (result.accepted) {
      accepted.push(result.event);
      if (result.control) {
        controls.push(result.control);
        break;
      }
    }
  }
  return { state: next, accepted, controls };
}

// frontend/src/uploads.js
var UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
var SHA256_WORDS = new Uint32Array([
  1116352408,
  1899447441,
  3049323471,
  3921009573,
  961987163,
  1508970993,
  2453635748,
  2870763221,
  3624381080,
  310598401,
  607225278,
  1426881987,
  1925078388,
  2162078206,
  2614888103,
  3248222580,
  3835390401,
  4022224774,
  264347078,
  604807628,
  770255983,
  1249150122,
  1555081692,
  1996064986,
  2554220882,
  2821834349,
  2952996808,
  3210313671,
  3336571891,
  3584528711,
  113926993,
  338241895,
  666307205,
  773529912,
  1294757372,
  1396182291,
  1695183700,
  1986661051,
  2177026350,
  2456956037,
  2730485921,
  2820302411,
  3259730800,
  3345764771,
  3516065817,
  3600352804,
  4094571909,
  275423344,
  430227734,
  506948616,
  659060556,
  883997877,
  958139571,
  1322822218,
  1537002063,
  1747873779,
  1955562222,
  2024104815,
  2227730452,
  2361852424,
  2428436474,
  2756734187,
  3204031479,
  3329325298
]);
var UploadError = class extends Error {
  constructor(code, { status = null, retryable = false } = {}) {
    super(status === null ? `Upload ${code.replaceAll("_", " ")}` : `Upload failed (HTTP ${status})`);
    this.name = "UploadError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
};
var IncrementalSha256 = class {
  constructor() {
    this._state = new Uint32Array([
      1779033703,
      3144134277,
      1013904242,
      2773480762,
      1359893119,
      2600822924,
      528734635,
      1541459225
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
    finalBlock[this._tail.length] = 128;
    for (let index = 0; index < 8; index += 1) {
      finalBlock[finalBlock.length - 1 - index] = Number(totalBits >> BigInt(index * 8) & 0xffn);
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
      words[index] = (block[offset] << 24 | block[offset + 1] << 16 | block[offset + 2] << 8 | block[offset + 3]) >>> 0;
    }
    for (let index = 16; index < 64; index += 1) {
      const gamma0 = rightRotate(words[index - 15], 7) ^ rightRotate(words[index - 15], 18) ^ words[index - 15] >>> 3;
      const gamma1 = rightRotate(words[index - 2], 17) ^ rightRotate(words[index - 2], 19) ^ words[index - 2] >>> 10;
      words[index] = words[index - 16] + gamma0 + words[index - 7] + gamma1 >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = this._state;
    for (let index = 0; index < 64; index += 1) {
      const sigma1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const choose = e & f ^ ~e & g;
      const temp1 = h + sigma1 + choose + SHA256_WORDS[index] + words[index] >>> 0;
      const sigma0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const majority = a & b ^ a & c ^ b & c;
      const temp2 = sigma0 + majority >>> 0;
      h = g;
      g = f;
      f = e;
      e = d + temp1 >>> 0;
      d = c;
      c = b;
      b = a;
      a = temp1 + temp2 >>> 0;
    }
    this._state[0] = this._state[0] + a >>> 0;
    this._state[1] = this._state[1] + b >>> 0;
    this._state[2] = this._state[2] + c >>> 0;
    this._state[3] = this._state[3] + d >>> 0;
    this._state[4] = this._state[4] + e >>> 0;
    this._state[5] = this._state[5] + f >>> 0;
    this._state[6] = this._state[6] + g >>> 0;
    this._state[7] = this._state[7] + h >>> 0;
  }
};
async function sha256File(file, { chunkSize = UPLOAD_CHUNK_BYTES, signal } = {}) {
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
async function uploadResumableFile({
  file,
  threadId,
  relativePath,
  uploadId = null,
  accessToken = "",
  fetchImpl = fetch,
  signal,
  onProgress = () => {
  },
  retryAttempts = 2,
  retryDelay = 250
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
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        filename: safeFile.name,
        mime_type: safeFile.type || "application/octet-stream",
        relative_path: safePath,
        size_bytes: safeFile.size,
        sha256: fileDigest
      }),
      signal
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
      fileSize: safeFile.size
    });
    report({
      status: "uploading",
      completedBytes: Math.min(firstMissingIndex(session) * session.chunk_size, safeFile.size),
      uploadId: session.upload_id
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
      signal
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
    fetchImpl,
    threadId,
    session,
    index,
    offset,
    chunk,
    chunkDigest,
    accessToken,
    signal,
    retryAttempts,
    retryDelay,
    fileSize
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
          "X-Chunk-SHA256": chunkDigest
        },
        body: chunk,
        signal
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
    signal
  }, [200]));
}
function validateSession(session, expectedSize = null) {
  if (!session || typeof session !== "object" || typeof session.upload_id !== "string" || !session.upload_id) {
    throw new UploadError("invalid_session");
  }
  if (!Number.isSafeInteger(session.chunk_size) || session.chunk_size < 1 || session.chunk_size > UPLOAD_CHUNK_BYTES || !Number.isSafeInteger(session.total_chunks) || session.total_chunks < 1 || !Array.isArray(session.received_indices) || !["active", "completed", "cancelled"].includes(session.status)) {
    throw new UploadError("invalid_session");
  }
  if (expectedSize !== null && session.size_bytes !== void 0 && session.size_bytes !== expectedSize) {
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
  return value >>> amount | value << 32 - amount;
}
function containsControl(value) {
  return [...value].some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint <= 31 || codePoint === 127;
  });
}

// frontend/src/views/auth.js
var PLAN_NAMES = /* @__PURE__ */ new Map([
  ["free", "Free"],
  ["go", "Go"],
  ["plus", "Plus"],
  ["pro", "Pro"],
  ["prolite", "Pro"],
  ["team", "Team"],
  ["self_serve_business_usage_based", "Business"],
  ["business", "Business"],
  ["enterprise_cbp_usage_based", "Enterprise"],
  ["enterprise", "Enterprise"],
  ["edu", "Education"]
]);
var TERMINAL_STATES = /* @__PURE__ */ new Set(["ok", "signed_out", "cancelled", "login_failed", "expired"]);
var ACTIVE_STATES = /* @__PURE__ */ new Set(["login_starting", "login_running"]);
var BUSY_STATES = /* @__PURE__ */ new Set(["login_canceling", "login_completing", "logout_running"]);
function normalizePlanType(value) {
  return typeof value === "string" ? PLAN_NAMES.get(value.trim().toLowerCase()) || "Unknown" : "Unknown";
}
function getAuthViewModel(auth = {}) {
  const state = typeof auth.state === "string" ? auth.state : "unknown";
  const loginActive = ACTIVE_STATES.has(state);
  const busy = BUSY_STATES.has(state);
  const code = loginActive && typeof auth.user_code === "string" && auth.user_code.trim() ? auth.user_code.trim() : null;
  const authMode = auth.auth_mode ?? auth.account?.auth_mode ?? null;
  const unsupported = state === "unsupported" || typeof authMode === "string" && authMode !== "chatgpt";
  const signedIn = !unsupported && state === "ok" && !auth.auth_required;
  const actions = [];
  if (loginActive) {
    actions.push({ id: "open-chatgpt", label: "Open ChatGPT" });
    if (code) actions.push({ id: "copy-auth-code", label: "Copy code" });
    actions.push({ id: "cancel-sign-in", label: "Cancel" });
  } else if (busy) {
  } else if (unsupported) {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Sign out now" : "Sign out"
    });
  } else if (state === "logout_failed") {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Try sign-out again" : "Sign out"
    });
  } else if (!signedIn) {
    actions.push({ id: "start-auth-login", label: "Sign in with ChatGPT", primary: true });
  } else {
    actions.push({
      id: auth.signedOutConfirmed ? "sign-out" : "confirm-sign-out",
      label: auth.signedOutConfirmed ? "Sign out now" : "Sign out"
    });
  }
  let message = "Sign in with the ChatGPT account that includes your Codex access.";
  if (unsupported) message = "Only ChatGPT account sign-in is supported.";
  else if (signedIn) message = "Codex is connected through your ChatGPT account.";
  else if (state === "login_failed") message = "Sign-in did not complete. Try again from Home Assistant.";
  else if (state === "logout_failed") message = "Sign-out did not complete. Try again from Home Assistant.";
  else if (state === "expired") message = "Your ChatGPT sign-in expired. Sign in again to continue.";
  else if (state === "login_canceling") message = "Cancelling ChatGPT sign-in. Please wait.";
  else if (state === "login_completing") message = "Finishing ChatGPT sign-in. Please wait.";
  else if (state === "logout_running") message = "Signing out of ChatGPT. Please wait.";
  else if (loginActive) message = "Enter this one-time code in the ChatGPT sign-in page.";
  return {
    state: signedIn ? "signed_in" : unsupported ? "unsupported" : state,
    signedIn,
    busy,
    plan: normalizePlanType(auth.account?.plan_type ?? auth.plan_type),
    code: TERMINAL_STATES.has(state) ? null : code,
    canCopyCode: Boolean(code),
    canOpen: loginActive,
    message,
    guidance: loginActive ? "Continue in ChatGPT on your phone or another signed-in device." : "",
    actions
  };
}
function renderAuth(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = "auth-card";
  const title = document.createElement("strong");
  title.textContent = model.signedIn ? "ChatGPT connected" : "ChatGPT sign-in";
  card.append(title);
  if (model.plan !== "Unknown") {
    const plan = document.createElement("span");
    plan.className = "auth-plan";
    plan.textContent = `${model.plan} plan`;
    card.append(plan);
  }
  if (model.message) {
    const message = document.createElement("p");
    message.textContent = model.message;
    card.append(message);
  }
  if (model.code) {
    const code = document.createElement("code");
    code.className = "auth-code";
    code.textContent = model.code;
    card.append(code);
  }
  if (model.guidance) {
    const guidance = document.createElement("p");
    guidance.textContent = model.guidance;
    card.append(guidance);
  }
  const actions = document.createElement("div");
  actions.className = "auth-actions";
  for (const action of model.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = action.id;
    button.className = action.primary ? "primary" : "";
    button.textContent = action.label;
    actions.append(button);
  }
  card.append(actions);
  container.append(card);
}

// frontend/src/views/approval.js
var APPROVAL_ACTIONS = /* @__PURE__ */ new Map([
  ["accept", "Accept"],
  ["decline", "Decline"],
  ["cancel", "Cancel"]
]);
var MAX_TITLE_LENGTH = 160;
var MAX_SUMMARY_LENGTH = 512;
var MAX_COMMAND_LENGTH = 512;
var MAX_SCOPE_PATHS = 128;
function plainText(value, limit) {
  if (typeof value !== "string") return "";
  return [...value].filter((character) => {
    const code = character.codePointAt(0);
    return code > 31 && code !== 127;
  }).join("").trim().slice(0, limit);
}
function safeWorkspacePath(value) {
  const path = plainText(value, 240).replaceAll("\\", "/");
  if (!path || path.startsWith("/") || /^[A-Za-z]:/u.test(path) || path.includes("://")) return null;
  const segments = path.split("/");
  return segments.every((segment) => segment && segment !== "." && segment !== "..") ? path : null;
}
function expiryState(value, now) {
  if (typeof value !== "string") return { expired: true, label: "Expiry unavailable" };
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return { expired: true, label: "Expiry unavailable" };
  if (timestamp <= now) return { expired: true, label: "Expired" };
  return { expired: false, label: `Expires ${new Date(timestamp).toISOString().replace(".000", "")}` };
}
function getApprovalViewModel(interaction = {}, { now = Date.now(), pending = false, stale = false } = {}) {
  const display = interaction && typeof interaction.display === "object" ? interaction.display : {};
  const expiry = expiryState(interaction?.expires_at, now);
  const interactionPending = interaction?.status === "pending";
  const unavailable = Boolean(pending || stale || expiry.expired || !interactionPending);
  const state = pending ? "submitting" : stale ? "stale" : expiry.expired ? "expired" : interactionPending ? "ready" : "unavailable";
  const allowedActions = Array.isArray(interaction?.allowed_actions) ? interaction.allowed_actions : [];
  const actions = [...APPROVAL_ACTIONS].filter(([action]) => allowedActions.includes(action)).map(([id, label]) => ({ id, label, disabled: unavailable }));
  const scope = Array.isArray(display.workspace_paths) ? display.workspace_paths.map(safeWorkspacePath).filter(Boolean).slice(0, MAX_SCOPE_PATHS) : [];
  const command = plainText(display.command, MAX_COMMAND_LENGTH);
  const kind = interaction?.kind === "file_change_approval" ? "File change approval" : "Command approval";
  return {
    interactionId: typeof interaction?.interaction_id === "string" ? interaction.interaction_id : "",
    title: plainText(display.title, MAX_TITLE_LENGTH) || kind,
    summary: plainText(display.summary, MAX_SUMMARY_LENGTH) || "Codex needs your decision to continue.",
    kind,
    command: command || null,
    scope,
    expiry: expiry.label,
    state,
    disabled: unavailable,
    actions
  };
}
function renderApproval(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = `approval-card approval-${model.state}`;
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "false");
  card.tabIndex = -1;
  const accessibleId = /^[A-Za-z0-9_.:-]{1,128}$/u.test(model.interactionId) ? model.interactionId : "pending";
  const titleId = `approval-${accessibleId}-title`;
  const summaryId = `approval-${accessibleId}-summary`;
  card.setAttribute("aria-labelledby", titleId);
  card.setAttribute("aria-describedby", summaryId);
  const title = document.createElement("h3");
  title.id = titleId;
  title.textContent = model.title;
  const summary = document.createElement("p");
  summary.id = summaryId;
  summary.textContent = model.summary;
  const status = document.createElement("p");
  status.className = "decision-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = model.state === "submitting" ? "Sending decision..." : model.expiry;
  card.append(title, summary, status);
  if (model.command) {
    const commandLabel = document.createElement("span");
    commandLabel.className = "decision-label";
    commandLabel.textContent = "Command";
    const command = document.createElement("pre");
    command.className = "decision-command";
    command.textContent = model.command;
    card.append(commandLabel, command);
  }
  if (model.scope.length) {
    const scopeLabel = document.createElement("span");
    scopeLabel.className = "decision-label";
    scopeLabel.textContent = "Workspace files";
    const list = document.createElement("ul");
    list.className = "decision-scope";
    for (const path of model.scope) {
      const item = document.createElement("li");
      item.textContent = path;
      list.append(item);
    }
    card.append(scopeLabel, list);
  }
  const actions = document.createElement("div");
  actions.className = "decision-actions";
  for (const action of model.actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = `${action.id}-interaction`;
    button.dataset.decision = action.id;
    button.textContent = action.label;
    button.disabled = action.disabled;
    button.setAttribute("aria-disabled", String(action.disabled));
    actions.append(button);
  }
  card.append(actions);
  container.append(card);
}

// frontend/src/views/onboarding.js
var STAGE_DEFINITIONS = [
  ["App connected", "Connect the Home Assistant App.", (state) => state.appConnected],
  ["Integration confirmed", "Confirm the integration in Home Assistant.", (state) => state.integrationReady],
  ["Bridge ready", "Wait for the bridge to become ready.", (state) => state.bridgeReady],
  ["Codex ready", "Sign in, create a workspace, and start your first chat.", (state) => state.signedIn && state.workspaceReady && state.threadCount > 0]
];
function getOnboardingViewModel(state = {}) {
  const normalized = {
    appConnected: Boolean(state.appConnected),
    integrationReady: Boolean(state.integrationReady),
    bridgeReady: Boolean(state.bridgeReady),
    signedIn: Boolean(state.signedIn),
    workspaceReady: Boolean(state.workspaceReady),
    threadCount: Number.isFinite(state.threadCount) ? state.threadCount : 0
  };
  const stages = STAGE_DEFINITIONS.map(([label, note, complete], index) => ({
    id: ["app", "integration", "bridge", "codex"][index],
    label,
    note,
    complete: complete(normalized),
    action: index === 0 && !normalized.appConnected ? "retry-app" : null
  }));
  return { stages, complete: stages.every((stage) => stage.complete) };
}
function renderOnboarding(container, model) {
  container.replaceChildren();
  const list = document.createElement("ol");
  list.className = "onboarding-checklist";
  for (const stage of model.stages) {
    const item = document.createElement("li");
    item.className = `onboarding-stage ${stage.complete ? "complete" : "pending"}`;
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = stage.label;
    const note = document.createElement("span");
    note.textContent = stage.complete ? "Complete" : stage.note;
    copy.append(title, note);
    item.append(copy);
    if (stage.action) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.dataset.action = stage.action;
      retry.textContent = "Retry";
      item.append(retry);
    }
    list.append(item);
  }
  container.append(list);
}

// frontend/src/views/runtime-strip.js
function safeVersion(value) {
  return typeof value === "string" && /^[0-9][0-9A-Za-z.+-]{0,31}$/u.test(value) ? value : null;
}
function runtimeItem(label, ready, version) {
  return { label, state: ready ? "ready" : "attention", version: safeVersion(version) };
}
function getRuntimeStripViewModel(status = {}) {
  const apiVersion = Number(status.api_version);
  const diagnostics = status.diagnostics || {};
  const appReady = status.app?.connected === true;
  const integrationReady = status.integration?.ready === true;
  const bridgeReady = apiVersion === 1 && status.bridge_ready !== false;
  const codexVersion = diagnostics.app_server_version || diagnostics.active_codex_version;
  const codexReady = codexVersion ? bridgeReady : status.codex_ready === true && bridgeReady;
  const legacy = apiVersion === 0 || String(status.connection_type || "").startsWith("external");
  return {
    items: [
      runtimeItem("App", appReady, status.app?.version || diagnostics.app_version),
      runtimeItem("Integration", integrationReady, status.integration?.version),
      runtimeItem("Bridge", bridgeReady, diagnostics.bridge_version),
      runtimeItem("Codex", codexReady, codexVersion)
    ],
    notice: legacy ? "This older connection is capability-limited and supported for existing setups only." : ""
  };
}
function renderRuntimeStrip(container, model) {
  container.replaceChildren();
  const strip = document.createElement("div");
  strip.className = "runtime-strip";
  for (const item of model.items) {
    const status = document.createElement("span");
    status.className = `runtime-item ${item.state}`;
    status.textContent = item.version ? `${item.label} ${item.version}` : item.label;
    strip.append(status);
  }
  container.append(strip);
  if (model.notice) {
    const notice = document.createElement("p");
    notice.className = "runtime-notice";
    notice.textContent = model.notice;
    container.append(notice);
  }
}

// frontend/src/views/user-input.js
var MAX_QUESTIONS = 3;
var MAX_OPTIONS = 3;
var MAX_FREE_TEXT = 4096;
function plainText2(value, limit) {
  if (typeof value !== "string") return "";
  return [...value].filter((character) => {
    const code = character.codePointAt(0);
    return code > 31 && code !== 127;
  }).join("").trim().slice(0, limit);
}
function safeId(value, fallback) {
  return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/u.test(value) ? value : fallback;
}
function expiryState2(value, now) {
  if (typeof value !== "string") return { expired: true, label: "Expiry unavailable" };
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return { expired: true, label: "Expiry unavailable" };
  if (timestamp <= now) return { expired: true, label: "Expired" };
  return { expired: false, label: `Expires ${new Date(timestamp).toISOString().replace(".000", "")}` };
}
function selectedValues(value, options, allowFreeText) {
  const raw = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
  const allowed = new Set(options.map((option) => option.label));
  return raw.map((item) => plainText2(item, MAX_FREE_TEXT)).filter((item) => item && (allowed.has(item) || allowFreeText)).slice(0, 32);
}
function getUserInputViewModel(interaction = {}, { now = Date.now(), pending = false, stale = false, answers = {} } = {}) {
  const display = interaction && typeof interaction.display === "object" ? interaction.display : {};
  const expiry = expiryState2(interaction?.expires_at, now);
  const interactionPending = interaction?.status === "pending";
  const unavailable = Boolean(pending || stale || expiry.expired || !interactionPending);
  const state = pending ? "submitting" : stale ? "stale" : expiry.expired ? "expired" : interactionPending ? "ready" : "unavailable";
  const questions = (Array.isArray(display.questions) ? display.questions : []).slice(0, MAX_QUESTIONS).map((raw, index) => {
    const question = raw && typeof raw === "object" ? raw : {};
    const id = plainText2(question.question_id, 128) || `question-${index + 1}`;
    const domId = `${safeId(question.question_id, `question-${index + 1}`)}-${index + 1}`;
    const options = (Array.isArray(question.options) ? question.options : []).slice(0, MAX_OPTIONS).map((rawOption) => {
      const option = rawOption && typeof rawOption === "object" ? rawOption : {};
      return { label: plainText2(option.label, 160), description: plainText2(option.description, 512) };
    }).filter((option) => option.label && option.description);
    const allowFreeText = Boolean(question.allow_free_text);
    const selected = selectedValues(answers?.[id], options, allowFreeText);
    return {
      id,
      domId,
      header: plainText2(question.header, 160) || `Question ${index + 1}`,
      prompt: plainText2(question.prompt, 2048) || "Choose an answer to continue.",
      options,
      multiple: Boolean(question.multiple),
      allowFreeText,
      selected,
      complete: selected.length > 0
    };
  });
  const ready = questions.length > 0 && questions.every((question) => question.complete);
  const allowedActions = Array.isArray(interaction?.allowed_actions) ? interaction.allowed_actions : [];
  return {
    interactionId: typeof interaction?.interaction_id === "string" ? interaction.interaction_id : "",
    title: plainText2(display.title, 160) || "Codex has a question",
    summary: plainText2(display.summary, 512) || "Answer to continue this Codex turn.",
    expiry: expiry.label,
    state,
    disabled: unavailable,
    questions,
    submitDisabled: unavailable || !ready || !allowedActions.includes("answer")
  };
}
function renderUserInput(container, model) {
  container.replaceChildren();
  const card = document.createElement("section");
  card.className = `user-input-card user-input-${model.state}`;
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "false");
  card.tabIndex = -1;
  const accessibleId = /^[A-Za-z0-9_.:-]{1,128}$/u.test(model.interactionId) ? model.interactionId : "pending";
  const titleId = `question-${accessibleId}-title`;
  const summaryId = `question-${accessibleId}-summary`;
  card.setAttribute("aria-labelledby", titleId);
  card.setAttribute("aria-describedby", summaryId);
  const title = document.createElement("h3");
  title.id = titleId;
  title.textContent = model.title;
  const summary = document.createElement("p");
  summary.id = summaryId;
  summary.textContent = model.summary;
  const status = document.createElement("p");
  status.className = "decision-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = model.state === "submitting" ? "Sending answer..." : model.expiry;
  card.append(title, summary, status);
  for (const question of model.questions) {
    const fieldset = document.createElement("fieldset");
    fieldset.disabled = model.disabled;
    const legend = document.createElement("legend");
    legend.textContent = question.header;
    const prompt = document.createElement("p");
    prompt.textContent = question.prompt;
    fieldset.append(legend, prompt);
    for (const [index, option] of question.options.entries()) {
      const id = `question-${accessibleId}-${question.domId}-option-${index + 1}`;
      const optionLabel = document.createElement("label");
      optionLabel.htmlFor = id;
      const control = document.createElement("input");
      control.type = question.multiple ? "checkbox" : "radio";
      control.id = id;
      control.name = `question-${accessibleId}-${question.domId}`;
      control.value = option.label;
      control.dataset.questionId = question.id;
      control.dataset.answerValue = option.label;
      control.checked = question.selected.includes(option.label);
      const copy = document.createElement("span");
      copy.textContent = option.label;
      const description = document.createElement("small");
      description.textContent = option.description;
      optionLabel.append(control, copy, description);
      fieldset.append(optionLabel);
    }
    if (question.allowFreeText) {
      const freeTextId = `question-${accessibleId}-${question.domId}-free-text`;
      const freeTextLabel = document.createElement("label");
      freeTextLabel.htmlFor = freeTextId;
      freeTextLabel.textContent = "Other answer";
      const textarea = document.createElement("textarea");
      textarea.id = freeTextId;
      textarea.name = `question-${accessibleId}-${question.domId}-free-text`;
      textarea.maxLength = MAX_FREE_TEXT;
      textarea.disabled = model.disabled;
      textarea.dataset.questionId = question.id;
      textarea.dataset.questionFreeText = "true";
      textarea.setAttribute("aria-label", `${question.header}: other answer`);
      const freeText = question.selected.find((value) => !question.options.some((option) => option.label === value));
      textarea.value = freeText || "";
      fieldset.append(freeTextLabel, textarea);
    }
    card.append(fieldset);
  }
  const actions = document.createElement("div");
  actions.className = "decision-actions";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.dataset.action = "answer-interaction";
  submit.textContent = "Submit answer";
  submit.disabled = model.submitDisabled;
  submit.setAttribute("aria-disabled", String(model.submitDisabled));
  actions.append(submit);
  card.append(actions);
  container.append(card);
}
function collectUserInputAnswers(container, model) {
  const answers = [];
  for (const question of model.questions) {
    const selected = [...container.querySelectorAll("[data-question-id][data-answer-value]:checked")].filter((control) => control.dataset.questionId === question.id).map((control) => plainText2(control.value, MAX_FREE_TEXT));
    const freeText = [...container.querySelectorAll('[data-question-id][data-question-free-text="true"]')].find((control) => control.dataset.questionId === question.id);
    let values = selected;
    if (freeText) {
      const value = plainText2(freeText.value, MAX_FREE_TEXT);
      if (value) values = question.multiple ? [...selected, value] : [value];
    }
    const unique = [...new Set(values)].slice(0, question.multiple ? 32 : 1);
    if (unique.length) answers.push({ question_id: question.id, values: unique });
  }
  return answers;
}

// frontend/src/codex-bridge-panel.js
var PANEL_VERSION = "0.6.4";
var SYSTEM_EVENT_SCOPES = Object.freeze(["auth", "runtime"]);
var AUTH_VERIFICATION_HOSTS = /* @__PURE__ */ new Set([
  "auth.openai.com",
  "chatgpt.com",
  "platform.openai.com"
]);
var AUTH_ACTION_IDS = /* @__PURE__ */ new Set([
  "start-auth-login",
  "open-chatgpt",
  "cancel-sign-in",
  "confirm-sign-out",
  "sign-out",
  "refresh-auth-status",
  "copy-auth-code"
]);
var AUTH_POLL_INTERVAL_MS = 2e3;
var CREATED_THREAD_REFRESH_GRACE_MS = 5e3;
var MODE_OPTIONS = [
  {
    value: "observe",
    label: "Observe",
    description: "Read-only workspace access; asks before commands; network access stays off."
  },
  {
    value: "edit",
    label: "Edit",
    description: "Workspace edits are allowed; commands still require approval; network access stays off."
  },
  {
    value: "full-auto",
    label: "Full auto",
    description: "Workspace changes run automatically; network and private host paths remain blocked."
  }
];
var INTERACTION_EVENT_TYPES = /* @__PURE__ */ new Set([
  "interaction.created",
  "interaction.resolved",
  "interaction.expired",
  "interaction.outcome_unknown"
]);
var INTERACTION_ERROR_CODES = /* @__PURE__ */ new Set([
  "interaction_already_resolved",
  "interaction_kind_mismatch",
  "interaction_not_found",
  "interaction_outcome_unknown",
  "interaction_stale",
  "interaction_thread_mismatch",
  "runtime_request_conflict",
  "turn_changed"
]);
var ARTIFACT_PREVIEW_MAX_BYTES = 512 * 1024;
var ARTIFACT_PREVIEW_MAX_LABEL = "512 KB";
function artifactPreviewSizeState(artifact) {
  const sizeBytes = artifact?.size_bytes;
  if (!Number.isSafeInteger(sizeBytes) || sizeBytes < 0) {
    return "unknown";
  }
  return sizeBytes <= ARTIFACT_PREVIEW_MAX_BYTES ? "within-limit" : "oversized";
}
function isAutoPreviewCandidate(artifact) {
  return artifactPreviewSizeState(artifact) === "within-limit" && previewDescriptor(artifact, { type: artifact?.mime_type || "" }).kind !== "binary";
}
function previewUnavailableMessage(sizeState) {
  if (sizeState === "oversized") {
    return `Preview limited to files up to ${ARTIFACT_PREVIEW_MAX_LABEL}. Download it to view the full file.`;
  }
  return "Preview unavailable because the file size is unknown. Download it to view the full file.";
}
var template = document.createElement("template");
template.innerHTML = `
  <style>
    :host {
      --panel-bg: var(--primary-background-color, #f5f7fb);
      --surface-bg: var(--ha-card-background, var(--card-background-color, var(--primary-background-color, #ffffff)));
      --surface-alt: var(--secondary-background-color, color-mix(in srgb, var(--surface-bg) 94%, var(--panel-bg) 6%));
      --surface-muted: var(--input-fill-color, var(--secondary-background-color, color-mix(in srgb, var(--surface-bg) 92%, var(--panel-bg) 8%)));
      --border-color: var(--divider-color, color-mix(in srgb, var(--secondary-text-color, #667085) 24%, var(--surface-bg) 76%));
      --text-color: var(--primary-text-color, #151b29);
      --muted-color: var(--secondary-text-color, #667085);
      --accent-color: var(--primary-color, #28a0f0);
      --rail-bg: color-mix(in srgb, var(--surface-bg) 94%, var(--text-color) 6%);
      --canvas-bg: color-mix(in srgb, var(--surface-bg) 99%, var(--text-color) 1%);
      --context-bg: color-mix(in srgb, var(--surface-bg) 96%, var(--text-color) 4%);
      --focus-ring-color: color-mix(in srgb, var(--accent-color) 74%, var(--surface-bg) 26%);
      --focus-ring-contrast: var(--surface-bg);
      --brand-cyan: #64748b;
      --brand-blue: #475569;
      --brand-violet: #475569;
      --brand-emerald: #1dbf73;
      --brand-amber: #f59e0b;
      --accent-soft: color-mix(in srgb, var(--accent-color) 12%, var(--surface-bg) 88%);
      --danger-color: var(--error-color, #e25563);
      --accent-surface: color-mix(in srgb, var(--accent-color) 9%, var(--surface-bg) 91%);
      --danger-surface: color-mix(in srgb, var(--danger-color) 11%, var(--surface-bg) 89%);
      --warning-surface: color-mix(in srgb, var(--brand-amber) 10%, var(--surface-bg) 90%);
      --success-surface: color-mix(in srgb, var(--brand-emerald) 10%, var(--surface-bg) 90%);
      --shadow-soft: 0 1px 2px rgba(15, 23, 42, 0.06);
      --shadow-card: 0 2px 8px rgba(15, 23, 42, 0.06);
      display: block;
      height: 100%;
      color: var(--text-color);
    }

    * {
      box-sizing: border-box;
    }

    button,
    input,
    textarea,
    select {
      font: inherit;
      color: inherit;
    }

    button {
      border: 1px solid var(--border-color);
      background: var(--surface-bg);
      border-radius: 8px;
      cursor: pointer;
      padding: 0;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease, box-shadow 120ms ease, transform 120ms ease;
    }

    button:hover {
      border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color) 45%);
      background: color-mix(in srgb, var(--surface-bg) 92%, var(--accent-soft) 8%);
    }

    button:disabled,
    input:disabled,
    textarea:disabled,
    select:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    input,
    textarea,
    select {
      border: 1px solid var(--border-color);
      background: var(--surface-bg);
      border-radius: 8px;
      outline: none;
    }

    input:focus,
    textarea:focus,
    select:focus {
      border-color: color-mix(in srgb, var(--accent-color) 68%, var(--surface-bg) 32%);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color) 14%, transparent);
    }

    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    select:focus-visible {
      outline: 2px solid var(--focus-ring-contrast);
      outline-offset: 2px;
      box-shadow: 0 0 0 4px var(--focus-ring-color);
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(228px, 278px) minmax(0, 1fr) minmax(228px, 286px);
      gap: 12px;
      height: 100%;
      max-height: 100vh;
      min-height: 0;
      padding: 12px;
      background:
        linear-gradient(135deg, color-mix(in srgb, var(--brand-cyan) 10%, transparent), transparent 34%),
        linear-gradient(180deg, color-mix(in srgb, var(--panel-bg) 92%, white 8%), var(--panel-bg));
    }

    .pane {
      position: relative;
      min-width: 0;
      min-height: 0;
      background: color-mix(in srgb, var(--surface-bg) 98%, #f5f8fd 2%);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: var(--shadow-soft);
    }

    .pane::before {
      content: "";
      position: absolute;
      inset: 0 0 auto 0;
      height: 3px;
      background: linear-gradient(90deg, var(--brand-cyan), var(--brand-blue), var(--brand-violet));
      opacity: 0.86;
      pointer-events: none;
      z-index: 1;
    }

    .rail-pane,
    .side-pane {
      position: relative;
    }

    .rail-header,
    .main-header,
    .side-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border-color);
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--surface-bg) 96%, #f1f7ff 4%), color-mix(in srgb, var(--surface-bg) 99%, #f8fbff 1%));
    }

    .title-block {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }

    .eyeline {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .title {
      font-size: 17px;
      font-weight: 600;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .account-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      width: fit-content;
      max-width: 100%;
      margin-top: 4px;
      padding: 4px 8px;
      border-radius: 999px;
      color: color-mix(in srgb, var(--accent-color) 78%, black 22%);
      background: linear-gradient(90deg, color-mix(in srgb, var(--brand-cyan) 13%, white 87%), color-mix(in srgb, var(--brand-blue) 10%, white 90%));
      border: 1px solid color-mix(in srgb, var(--accent-color) 22%, var(--border-color) 78%);
      font-size: 11px;
      font-weight: 600;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .account-pill.unavailable {
      color: var(--muted-color);
      background: color-mix(in srgb, var(--surface-bg) 94%, #eef3fb 6%);
      border-color: var(--border-color);
    }

    .subline,
    .meta-line,
    .row-meta,
    .status-text,
    .empty-note,
    .timestamp,
    .label-text {
      font-size: 12px;
      color: var(--muted-color);
      line-height: 1.45;
    }

    .hidden {
      display: none !important;
    }

    .section-scroll,
    .message-list,
    .side-scroll,
    .browse-list,
    .artifact-preview {
      overflow: auto;
      min-height: 0;
    }

    .section-scroll,
    .message-list,
    .side-scroll {
      flex: 1 1 auto;
    }

    .icon-button,
    .copy-button,
    .tool-button,
    .download-button,
    .action-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .icon-button,
    .download-button,
    .action-button {
      width: 34px;
      height: 34px;
      color: var(--muted-color);
      flex: 0 0 auto;
    }

    .icon-button.small,
    .download-button.small,
    .action-button.small {
      width: 28px;
      height: 28px;
      border-radius: 7px;
    }

    .tool-button {
      justify-content: flex-start;
      width: 100%;
      padding: 10px 12px;
      color: var(--text-color);
      background: transparent;
      border-color: transparent;
      border-radius: 10px;
    }

    .tool-button:hover {
      background: color-mix(in srgb, var(--accent-soft) 38%, white 62%);
      border-color: transparent;
    }

    .tool-button svg,
    .icon-button svg,
    .copy-button svg,
    .download-button svg,
    .send-button svg,
    .action-button svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
    }

    .tool-button span {
      font-size: 14px;
      font-weight: 500;
    }

    .rail-actions,
    .forms-stack {
      display: grid;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 98%, #f6f9fd 2%);
    }

    .search-shell {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      padding: 0 10px;
      height: 40px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: var(--surface-bg);
      color: var(--muted-color);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.64);
    }

    .search-shell input {
      border: 0;
      background: transparent;
      padding: 0;
      height: 100%;
      font-size: 14px;
      color: var(--text-color);
    }

    .search-shell input:focus {
      border: 0;
    }

    .panel-form {
      display: none;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-bg) 97%, #f3f9ff 3%), var(--surface-bg));
      box-shadow: var(--shadow-card);
    }

    .panel-form.visible {
      display: grid;
    }

    .field,
    .field-select,
    .composer textarea {
      width: 100%;
      padding: 10px 12px;
      background: var(--surface-bg);
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .browser-card {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: color-mix(in srgb, var(--surface-bg) 99%, #f7fbff 1%);
    }

    .browser-label,
    .section-label,
    .setting-label,
    .limit-label {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .browse-list {
      display: grid;
      gap: 6px;
      max-height: 170px;
      padding-right: 2px;
    }

    .browse-row {
      text-align: left;
      padding: 9px 10px;
      border-radius: 8px;
      font-size: 13px;
    }

    .browser-actions,
    .form-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .text-button {
      height: 34px;
      padding: 0 12px;
      border-radius: 8px;
      color: var(--muted-color);
      font-size: 13px;
    }

    .send-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      height: 38px;
      padding: 0 16px;
      border-radius: 10px;
      color: white;
      background: linear-gradient(135deg, color-mix(in srgb, var(--accent-color) 90%, white 10%), color-mix(in srgb, var(--accent-color) 72%, #00d4ff 28%));
      border-color: transparent;
      font-weight: 600;
      box-shadow: 0 10px 22px color-mix(in srgb, var(--accent-color) 24%, transparent);
    }

    .send-button:hover {
      border-color: transparent;
      background: linear-gradient(135deg, color-mix(in srgb, var(--accent-color) 82%, white 18%), color-mix(in srgb, var(--accent-color) 64%, #00d4ff 36%));
      transform: translateY(-1px);
    }

    .rail-sections {
      display: grid;
      gap: 4px;
      padding: 8px 10px 10px;
      align-content: start;
    }

    .rail-section {
      overflow: hidden;
      background: transparent;
    }

    .rail-section.flat {
      border: 0;
      background: transparent;
    }

    .section-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      padding: 8px 6px 6px;
      border-bottom: 0;
      background: transparent;
    }

    .section-head.compact {
      border-bottom: 0;
    }

    .section-head-button,
    .project-button {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      text-align: left;
      background: transparent;
      border: 0;
      padding: 0;
      color: inherit;
    }

    .section-head-button:hover,
    .project-button:hover {
      border: 0;
      background: transparent;
    }

    .section-title-line {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .section-title-line svg {
      width: 16px;
      height: 16px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
      color: var(--muted-color);
    }

    .section-name,
    .project-name,
    .thread-name {
      font-size: 14px;
      font-weight: 600;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .project-list,
    .chat-list {
      display: grid;
      gap: 1px;
    }

    .project-shell {
      display: grid;
      gap: 0;
    }

    .project-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 8px;
      padding: 8px 8px 4px;
      border-top: 0;
      border-radius: 10px;
    }

    .project-shell:first-child .project-head {
      border-top: 0;
    }

    .project-head.active {
      background: linear-gradient(90deg, color-mix(in srgb, var(--accent-color) 12%, transparent), transparent 78%);
      box-shadow: inset 3px 0 0 color-mix(in srgb, var(--accent-color) 74%, var(--brand-cyan) 26%);
    }

    .project-meta {
      display: grid;
      gap: 1px;
      min-width: 0;
    }

    .project-actions,
    .row-actions {
      display: flex;
      gap: 6px;
      align-items: center;
      flex: 0 0 auto;
    }

    .chat-list {
      margin-left: 14px;
      padding: 0 0 8px 12px;
      border-left: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent);
    }

    .chat-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 6px;
      padding: 1px 0;
    }

    .chat-select {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      min-width: 0;
      padding: 9px 10px;
      text-align: left;
      border-radius: 9px;
      background: transparent;
      border: 1px solid transparent;
      color: inherit;
    }

    .chat-select.active {
      background: linear-gradient(90deg, color-mix(in srgb, var(--accent-soft) 74%, white 26%), color-mix(in srgb, var(--surface-bg) 98%, #f7fbff 2%));
      border-color: color-mix(in srgb, var(--accent-color) 28%, var(--border-color) 72%);
      box-shadow: inset 3px 0 0 var(--accent-color), 0 8px 18px rgba(15, 23, 42, 0.06);
    }

    .chat-select:hover {
      background: color-mix(in srgb, var(--accent-soft) 36%, white 64%);
      border-color: transparent;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 10px;
      height: 10px;
      min-width: 10px;
      padding: 0;
      border-radius: 999px;
      background: color-mix(in srgb, var(--surface-bg) 92%, #eef3fb 8%);
      color: var(--muted-color);
      font-size: 0;
      border: 1px solid color-mix(in srgb, var(--border-color) 82%, transparent);
    }

    .status-pill.running {
      color: var(--accent-color);
      background: color-mix(in srgb, var(--accent-color) 12%, white 88%);
      box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent-color) 10%, transparent);
    }

    .status-pill.error {
      color: var(--danger-color);
      background: color-mix(in srgb, var(--danger-color) 10%, white 90%);
    }

    .status-pill.idle {
      color: var(--brand-emerald);
      background: color-mix(in srgb, var(--brand-emerald) 12%, white 88%);
      box-shadow: 0 0 0 4px color-mix(in srgb, var(--brand-emerald) 10%, transparent);
    }

    .main-pane {
      background: color-mix(in srgb, var(--surface-bg) 99%, #fafcff 1%);
    }

    .main-top {
      display: grid;
      flex: 0 1 auto;
      gap: 6px;
      max-height: min(30vh, 300px);
      padding: 10px 14px 0;
      overflow: auto;
    }

    .runtime-shell {
      display: grid;
      gap: 6px;
    }

    .runtime-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }

    .runtime-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 26px;
      padding: 0 9px;
      border: 1px solid var(--border-color);
      border-radius: 999px;
      background: var(--surface-bg);
      color: var(--muted-color);
      font-size: 11px;
      font-weight: 650;
      letter-spacing: 0.01em;
    }

    .runtime-item::before {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--brand-amber);
      content: "";
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand-amber) 12%, transparent);
    }

    .runtime-item.ready::before {
      background: var(--brand-emerald);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand-emerald) 12%, transparent);
    }

    .runtime-notice {
      margin: 0;
      padding: 7px 9px;
      border: 1px solid color-mix(in srgb, var(--brand-amber) 25%, var(--border-color) 75%);
      border-radius: 8px;
      background: color-mix(in srgb, var(--brand-amber) 7%, var(--surface-bg) 93%);
      color: var(--muted-color);
      font-size: 11px;
      line-height: 1.4;
    }

    .onboarding-shell {
      display: grid;
      gap: 10px;
      padding: 12px;
      border: 1px solid color-mix(in srgb, var(--accent-color) 23%, var(--border-color) 77%);
      border-radius: 12px;
      background:
        linear-gradient(135deg, color-mix(in srgb, var(--brand-cyan) 7%, var(--surface-bg) 93%), transparent 45%),
        var(--surface-bg);
      box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
    }

    .onboarding-heading {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
    }

    .onboarding-heading strong {
      font-size: 13px;
    }

    .onboarding-heading span {
      color: var(--muted-color);
      font-size: 11px;
    }

    .onboarding-checklist {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
      counter-reset: onboarding;
    }

    .onboarding-stage {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-width: 0;
      padding: 9px;
      border: 1px solid var(--border-color);
      border-radius: 9px;
      background: color-mix(in srgb, var(--surface-bg) 96%, var(--surface-alt) 4%);
      counter-increment: onboarding;
    }

    .onboarding-stage > div {
      display: grid;
      gap: 3px;
      min-width: 0;
    }

    .onboarding-stage strong {
      font-size: 11px;
      overflow-wrap: anywhere;
    }

    .onboarding-stage span {
      color: var(--muted-color);
      font-size: 10px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .onboarding-stage.complete {
      border-color: color-mix(in srgb, var(--brand-emerald) 28%, var(--border-color) 72%);
      background: color-mix(in srgb, var(--brand-emerald) 6%, var(--surface-bg) 94%);
    }

    .onboarding-stage button {
      min-height: 28px;
      padding: 0 9px;
      color: var(--accent-color);
      font-size: 11px;
      font-weight: 650;
    }

    .auth-card {
      display: grid;
      gap: 8px;
    }

    .auth-card > strong {
      font-size: 13px;
    }

    .auth-plan {
      width: fit-content;
      padding: 3px 7px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--brand-violet) 10%, var(--surface-bg) 90%);
      color: color-mix(in srgb, var(--brand-violet) 80%, var(--text-color) 20%);
      font-size: 11px;
      font-weight: 650;
    }

    .auth-code {
      width: fit-content;
      max-width: 100%;
      padding: 8px 10px;
      border: 1px dashed color-mix(in srgb, var(--accent-color) 42%, var(--border-color) 58%);
      border-radius: 8px;
      background: var(--surface-alt);
      font-size: 15px;
      font-weight: 750;
      letter-spacing: 0.08em;
      overflow-wrap: anywhere;
    }

    .auth-card p {
      margin: 0;
      color: var(--muted-color);
      font-size: 11px;
      line-height: 1.45;
    }

    .auth-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .auth-actions button {
      min-height: 30px;
      padding: 0 10px;
      font-size: 11px;
      font-weight: 650;
    }

    .auth-actions button.primary {
      border-color: transparent;
      background: linear-gradient(135deg, var(--brand-blue), var(--brand-violet));
      color: white;
      box-shadow: 0 8px 18px color-mix(in srgb, var(--brand-blue) 20%, transparent);
    }

    .error-strip {
      display: none;
      min-height: 28px;
      padding: 7px 10px;
      border-radius: 8px;
      border: 1px solid color-mix(in srgb, var(--danger-color) 20%, transparent);
      background: color-mix(in srgb, var(--danger-color) 7%, white 93%);
      color: color-mix(in srgb, var(--danger-color) 88%, black 12%);
      font-size: 12px;
      line-height: 1.45;
    }

    .error-strip.visible {
      display: block;
    }

    .status-banner {
      display: none;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-height: 30px;
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid color-mix(in srgb, var(--brand-amber) 28%, transparent);
      background: color-mix(in srgb, var(--brand-amber) 9%, white 91%);
      color: color-mix(in srgb, var(--brand-amber) 74%, black 26%);
      font-size: 12px;
      line-height: 1.35;
    }

    .banner-content {
      display: grid;
      gap: 8px;
      min-width: 0;
    }

    .banner-message {
      white-space: normal;
      overflow-wrap: anywhere;
    }

    .banner-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .banner-action {
      min-height: 28px;
      padding: 0 10px;
      font-size: 12px;
      font-weight: 600;
      color: inherit;
      background: color-mix(in srgb, var(--surface-bg) 82%, transparent);
    }

    .banner-action.primary {
      color: white;
      border-color: color-mix(in srgb, var(--danger-color) 65%, black 10%);
      background: linear-gradient(135deg, var(--danger-color), color-mix(in srgb, var(--danger-color) 72%, var(--brand-violet) 28%));
      box-shadow: 0 8px 20px color-mix(in srgb, var(--danger-color) 18%, transparent);
    }

    .status-banner.visible {
      display: grid;
    }

    .status-banner.error {
      border-color: color-mix(in srgb, var(--danger-color) 24%, transparent);
      background: color-mix(in srgb, var(--danger-color) 8%, white 92%);
      color: color-mix(in srgb, var(--danger-color) 82%, black 18%);
    }

    .banner-dismiss {
      width: 24px;
      height: 24px;
      border-radius: 7px;
      font-size: 16px;
      line-height: 1;
      color: inherit;
      background: transparent;
    }

    .interaction-region {
      display: grid;
      flex: 2 1 340px;
      gap: 8px;
      max-height: min(48vh, 520px);
      min-height: 340px;
      padding: 8px 16px 2px;
      overflow: auto;
      scroll-padding-block: 52px 8px;
    }

    .interaction-region:empty {
      display: none;
      min-height: 0;
    }

    .interaction-card {
      min-width: 0;
      scroll-margin-top: 52px;
    }

    .approval-card,
    .user-input-card {
      display: grid;
      gap: 9px;
      padding: 13px 14px;
      border: 1px solid color-mix(in srgb, var(--brand-amber) 34%, var(--border-color) 66%);
      border-radius: 11px;
      background:
        linear-gradient(135deg, color-mix(in srgb, var(--brand-amber) 7%, transparent), transparent 48%),
        var(--surface-bg);
      box-shadow: var(--shadow-card);
    }

    .approval-card:focus-visible,
    .user-input-card:focus-visible {
      outline: 2px solid var(--accent-color);
      outline-offset: 2px;
    }

    .approval-card h3,
    .user-input-card h3,
    .approval-card p,
    .user-input-card p {
      margin: 0;
    }

    .approval-card h3,
    .user-input-card h3 {
      font-size: 14px;
    }

    .approval-card > p,
    .user-input-card > p,
    .decision-status,
    .decision-notice {
      color: var(--muted-color);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .decision-label {
      color: var(--muted-color);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .decision-command {
      max-height: 104px;
      margin: 0;
      padding: 9px 10px;
      overflow: auto;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      background: var(--surface-alt);
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      user-select: text;
    }

    .decision-scope {
      display: grid;
      gap: 3px;
      max-height: 56px;
      margin: 0;
      padding-left: 20px;
      overflow: auto;
      color: var(--muted-color);
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .decision-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }

    .decision-actions button {
      min-height: 32px;
      padding: 0 12px;
      font-size: 12px;
      font-weight: 650;
    }

    .decision-actions button[data-decision="accept"],
    .decision-actions button[data-action="answer-interaction"] {
      border-color: transparent;
      background: linear-gradient(135deg, var(--brand-blue), var(--brand-violet));
      color: white;
    }

    .decision-actions button[data-decision="decline"],
    .decision-actions button[data-decision="cancel"] {
      color: color-mix(in srgb, var(--danger-color) 56%, var(--text-color) 44%);
    }

    .user-input-card fieldset {
      display: grid;
      gap: 7px;
      min-width: 0;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--border-color);
      border-radius: 9px;
    }

    .user-input-card legend {
      padding: 0 4px;
      font-size: 12px;
      font-weight: 700;
    }

    .user-input-card fieldset > label:not([for$="free-text"]) {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 2px 8px;
      align-items: start;
      padding: 7px 8px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      cursor: pointer;
    }

    .user-input-card fieldset > label input {
      grid-row: 1 / span 2;
      margin-top: 2px;
    }

    .user-input-card small {
      color: var(--muted-color);
      line-height: 1.35;
    }

    .user-input-card textarea {
      min-height: 72px;
      padding: 9px 10px;
      resize: vertical;
    }

    .mode-boundaries {
      display: grid;
      gap: 4px;
      margin: 0;
      padding: 8px 10px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      color: var(--muted-color);
      font-size: 11px;
      line-height: 1.4;
      list-style: none;
    }

    .mode-boundaries strong {
      color: var(--text-color);
    }

    .compact-toolbar {
      display: grid;
      grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.25fr);
      gap: 6px;
      align-items: stretch;
    }

    .toolbar-card {
      display: grid;
      gap: 6px;
      min-width: 0;
      padding: 7px 9px;
      border: 1px solid var(--border-color);
      border-radius: 9px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-bg) 98%, #f5faff 2%), var(--surface-bg));
      box-shadow: 0 7px 18px rgba(15, 23, 42, 0.04);
    }

    .toolbar-card.limits {
      align-content: start;
    }

    .toolbar-card.controls {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      align-items: start;
    }

    .limit-pair {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .mini-limit,
    .mini-control {
      display: grid;
      gap: 3px;
      min-width: 0;
    }

    .mini-limit-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      min-width: 0;
    }

    .mini-limit-name {
      font-size: 11px;
      color: var(--muted-color);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .limit-value {
      font-size: 14px;
      font-weight: 700;
      line-height: 1;
      white-space: nowrap;
    }

    .limit-subline,
    .setting-foot {
      font-size: 11px;
      color: var(--muted-color);
      line-height: 1.3;
    }

    .mini-limit-bar {
      height: 4px;
      border-radius: 999px;
      overflow: hidden;
      background: color-mix(in srgb, var(--border-color) 68%, transparent);
      margin-top: 1px;
    }

    .mini-limit-fill {
      display: block;
      height: 100%;
      width: var(--limit-width, 0%);
      border-radius: inherit;
      background: linear-gradient(90deg, var(--limit-color, var(--brand-emerald)), color-mix(in srgb, var(--limit-color, var(--brand-emerald)) 72%, white 28%));
      transition: width 220ms ease;
    }

    .compact-select {
      width: 100%;
      min-width: 0;
      height: 30px;
      padding: 0 8px;
      border-radius: 8px;
      background: linear-gradient(180deg, var(--surface-bg), color-mix(in srgb, var(--surface-bg) 96%, #f0f5fb 4%));
      font-size: 12px;
    }

    .mini-control .setting-label {
      margin-bottom: 1px;
    }

    .message-list {
      padding: 10px 16px 6px;
      display: grid;
      flex-basis: 120px;
      gap: 12px;
      align-content: start;
      min-height: 60px;
    }

    .message {
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }

    .message.user {
      grid-template-columns: minmax(0, 1fr) 28px;
    }

    .message.user .avatar {
      order: 2;
      justify-self: end;
    }

    .message.user .bubble {
      order: 1;
      justify-self: end;
    }

    .avatar {
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      color: color-mix(in srgb, var(--accent-color) 88%, black 12%);
      background: linear-gradient(135deg, color-mix(in srgb, var(--accent-color) 13%, white 87%), color-mix(in srgb, var(--brand-cyan) 12%, white 88%));
      border: 1px solid color-mix(in srgb, var(--accent-color) 24%, var(--border-color) 76%);
      box-shadow: 0 7px 16px rgba(15, 23, 42, 0.06);
    }

    .bubble {
      max-width: min(780px, 100%);
      min-width: 0;
      padding: 12px 14px;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      background: linear-gradient(180deg, var(--surface-bg), color-mix(in srgb, var(--surface-bg) 97%, #f6faff 3%));
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
    }

    .message.user .avatar {
      color: color-mix(in srgb, var(--brand-violet) 72%, black 28%);
      background: linear-gradient(135deg, color-mix(in srgb, var(--brand-violet) 15%, white 85%), color-mix(in srgb, var(--brand-blue) 13%, white 87%));
      border-color: color-mix(in srgb, var(--brand-violet) 24%, var(--border-color) 76%);
    }

    .message.user .bubble {
      background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 66%, white 34%), color-mix(in srgb, var(--surface-bg) 94%, #eff8ff 6%));
      border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color) 78%);
    }

    .message-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }

    .bubble-text {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace);
      font-size: 14px;
      line-height: 1.6;
      background: transparent;
      color: inherit;
      user-select: text;
      -webkit-user-select: text;
    }

    .copy-button {
      gap: 6px;
      min-width: 0;
      padding: 0 10px;
      height: 30px;
      border-radius: 8px;
      color: var(--muted-color);
      font-size: 12px;
      background: color-mix(in srgb, var(--surface-bg) 94%, #f4f8fb 6%);
    }

    .code-block {
      display: grid;
      gap: 0;
      margin: 10px 0;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      overflow: hidden;
      background: color-mix(in srgb, var(--surface-bg) 96%, #eef3fb 4%);
    }

    .code-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 7px 9px;
      border-bottom: 1px solid var(--border-color);
      color: var(--muted-color);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .code-text {
      margin: 0;
      padding: 12px;
      white-space: pre;
      overflow: auto;
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace);
      font-size: 13px;
      line-height: 1.55;
      user-select: text;
      -webkit-user-select: text;
    }

    .event-row {
      font-size: 12px;
      color: var(--muted-color);
      padding-left: 38px;
      user-select: text;
      -webkit-user-select: text;
    }

    .composer-shell {
      display: grid;
      flex: 0 0 auto;
      gap: 10px;
      padding: 10px 16px 14px;
      border-top: 1px solid var(--border-color);
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--surface-bg) 97%, #f4faff 3%), var(--surface-bg));
    }

    .attachment-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }

    .attachment-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .attachment-chips {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
      min-height: 20px;
    }

    .attachment-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 100%;
      padding: 5px 9px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--surface-bg) 94%, #edf4ff 6%);
      border: 1px solid var(--border-color);
      font-size: 12px;
      color: var(--muted-color);
    }

    .attachment-chip strong {
      color: var(--text-color);
      font-weight: 600;
    }

    .composer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }

    .composer textarea {
      min-height: 112px;
      max-height: 280px;
      resize: vertical;
      line-height: 1.5;
      padding: 12px 14px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
    }

    .composer-status {
      min-height: 16px;
      margin: -4px 0 0;
      color: var(--muted-color);
      font-size: 11px;
      line-height: 1.35;
    }

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 180px;
      padding: 18px;
      text-align: center;
      color: var(--muted-color);
    }

    .side-scroll {
      display: grid;
      gap: 10px;
      padding: 10px;
      align-content: start;
    }

    .side-section {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-bg) 98%, #f6fbff 2%), var(--surface-bg));
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
    }

    .section-head-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .thread-actions {
      display: inline-flex;
      gap: 6px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 120ms ease;
    }

    .chat-row.selected .thread-actions,
    .chat-row.archived .thread-actions,
    .chat-row:hover .thread-actions,
    .chat-row:focus-within .thread-actions {
      opacity: 1;
      pointer-events: auto;
    }

    .progress-list,
    .context-list,
    .artifact-list,
    .diagnostics-list {
      display: grid;
      gap: 8px;
    }

    .progress-row,
    .context-row,
    .file-row,
    .diagnostics-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }

    .context-row {
      grid-template-columns: minmax(74px, 0.42fr) minmax(0, 1fr);
    }

    .context-row .label-text,
    .context-row .row-meta {
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .context-row .row-meta {
      justify-self: end;
      text-align: right;
      white-space: normal;
    }

    .progress-row {
      grid-template-columns: 16px minmax(0, 1fr);
    }

    .progress-dot {
      width: 16px;
      height: 16px;
      border-radius: 999px;
      border: 2px solid color-mix(in srgb, var(--border-color) 82%, transparent);
      margin-top: 1px;
    }

    .progress-dot.complete {
      border-color: #1dbf73;
      background: color-mix(in srgb, #1dbf73 16%, white 84%);
    }

    .progress-dot.active {
      border-color: var(--accent-color);
      background: color-mix(in srgb, var(--accent-color) 14%, white 86%);
    }

    .progress-dot.error {
      border-color: var(--danger-color);
      background: color-mix(in srgb, var(--danger-color) 12%, white 88%);
    }

    .file-main {
      display: grid;
      gap: 2px;
      min-width: 0;
    }

    .file-name {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-color);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .file-select {
      width: 100%;
      text-align: left;
      background: transparent;
      border: 1px solid transparent;
      padding: 0;
      color: inherit;
    }

    .file-select.active .file-name {
      color: color-mix(in srgb, var(--accent-color) 84%, black 16%);
    }

    .file-row.active {
      padding: 8px 9px;
      margin: -4px -5px;
      border-radius: 10px;
      background: linear-gradient(90deg, color-mix(in srgb, var(--accent-soft) 66%, white 34%), var(--surface-bg));
      box-shadow: inset 3px 0 0 var(--accent-color);
    }

    .artifact-preview {
      min-height: 220px;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      background: var(--surface-bg);
      overflow: auto;
    }

    .artifact-preview pre {
      margin: 0;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace);
      font-size: 12px;
      line-height: 1.5;
      user-select: text;
      -webkit-user-select: text;
    }

    .artifact-preview img {
      width: 100%;
      border: 0;
      display: block;
      background: white;
    }

    .artifact-preview img {
      height: auto;
      max-height: 560px;
      object-fit: contain;
    }

    .preview-empty,
    .preview-binary {
      display: grid;
      gap: 8px;
      place-items: center;
      min-height: 220px;
      padding: 16px;
      text-align: center;
      color: var(--muted-color);
    }

    .tool-chip-list {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    .tool-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 7px;
      border-radius: 999px;
      border: 1px solid var(--border-color);
      background: color-mix(in srgb, var(--surface-bg) 95%, #eef3fb 5%);
      color: var(--muted-color);
      font-size: 11px;
    }

    .tool-chip.available {
      color: color-mix(in srgb, var(--brand-emerald) 75%, black 25%);
      border-color: color-mix(in srgb, var(--brand-emerald) 28%, var(--border-color) 72%);
      background: color-mix(in srgb, var(--brand-emerald) 10%, white 90%);
    }

    .stop-button {
      color: var(--danger-color);
      border-color: color-mix(in srgb, var(--danger-color) 24%, var(--border-color) 76%);
      background: color-mix(in srgb, var(--danger-color) 8%, white 92%);
    }

    /* The workspace intentionally uses a quiet, mostly flat Codex-like hierarchy.
     * These rules sit near the responsive rules so stateful controls above retain their
     * existing selectors and behaviour while sharing one visual language. */
    .shell {
      grid-template-columns: 224px minmax(0, 1fr) 260px;
      gap: 0;
      padding: 0;
      background: var(--canvas-bg);
    }

    .pane {
      border: 0;
      border-radius: 0;
      box-shadow: none;
      background: var(--surface-bg);
    }

    .pane::before {
      display: none;
    }

    .rail-pane,
    .side-pane {
      background: var(--rail-bg);
    }

    .side-pane {
      background: var(--context-bg);
    }

    .rail-pane {
      border-right: 1px solid var(--border-color);
    }

    .side-pane {
      border-left: 1px solid var(--border-color);
    }

    .rail-header,
    .main-header,
    .side-header {
      min-height: 56px;
      padding: 12px 14px;
      background: transparent;
    }

    .main-header {
      padding-inline: max(20px, calc((100% - 900px) / 2));
    }

    .eyeline,
    .browser-label,
    .section-label,
    .setting-label,
    .limit-label,
    .mini-limit-name {
      font-size: 10px;
      font-weight: 650;
      letter-spacing: 0.06em;
    }

    .title {
      font-size: 15px;
      font-weight: 650;
    }

    .account-pill,
    .runtime-item,
    .tool-chip,
    .attachment-chip {
      border-radius: 7px;
      background: var(--surface-bg);
      color: var(--muted-color);
    }

    .artifact-preview:has(.preview-empty),
    .artifact-preview:has(.preview-empty) .preview-empty {
      min-height: 96px;
    }

    .account-pill {
      border-color: var(--border-color);
    }

    .rail-actions,
    .forms-stack {
      padding: 10px;
      background: transparent;
    }

    .tool-button {
      min-height: 34px;
      padding: 8px 9px;
      border-radius: 6px;
    }

    #new-direct-chat-button {
      border-color: color-mix(in srgb, var(--text-color) 12%, var(--border-color) 88%);
      background: color-mix(in srgb, var(--text-color) 7%, var(--surface-bg) 93%);
      font-weight: 650;
    }

    #new-direct-chat-button:hover {
      border-color: color-mix(in srgb, var(--accent-color) 38%, var(--border-color) 62%);
      background: color-mix(in srgb, var(--accent-color) 10%, var(--surface-bg) 90%);
    }

    .tool-button:hover,
    .chat-select:hover {
      background: color-mix(in srgb, var(--accent-color) 7%, var(--surface-bg) 93%);
    }

    .project-head.active,
    .chat-select.active {
      background: color-mix(in srgb, var(--accent-color) 13%, var(--surface-bg) 87%);
      box-shadow: inset 3px 0 0 var(--accent-color);
    }

    .project-head.active .project-name,
    .chat-select.active .thread-name {
      font-weight: 700;
    }

    .project-head.active .row-meta,
    .chat-select.active .row-meta,
    .chat-select.active .timestamp {
      color: var(--text-color);
    }

    .chat-select.active {
      border-color: transparent;
    }

    .main-pane {
      background: var(--canvas-bg);
    }

    #thread-status-text:not(:empty) {
      padding: 4px 7px;
      border: 1px solid var(--border-color);
      border-radius: 6px;
      background: var(--surface-bg);
      font-size: 10px;
      font-weight: 650;
      letter-spacing: 0.02em;
    }

    .main-top,
    .interaction-region,
    .message-list {
      width: min(calc(100% - 32px), 900px);
      margin-inline: auto;
    }

    .main-top {
      max-height: min(25vh, 220px);
      padding: 10px 0 0;
    }

    .runtime-item {
      min-height: 24px;
      font-weight: 600;
    }

    .onboarding-shell {
      gap: 4px;
      padding: 8px 10px;
      border-color: var(--border-color);
      border-radius: 7px;
      background: var(--surface-muted);
      box-shadow: none;
    }

    .onboarding-shell:not(:has(.onboarding-stage.pending)) {
      display: flex;
      align-items: center;
      min-height: 34px;
    }

    .onboarding-shell:not(:has(.onboarding-stage.pending)) #onboarding {
      display: none;
    }

    .onboarding-shell:not(:has(.onboarding-stage.pending)) .onboarding-heading {
      width: 100%;
    }

    .onboarding-checklist {
      gap: 6px;
    }

    .onboarding-stage {
      padding: 7px;
      border-radius: 6px;
      background: var(--surface-bg);
    }

    .compact-toolbar {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr);
      gap: 8px;
      padding-top: 2px;
      border-top: 1px solid var(--border-color);
    }

    .toolbar-card {
      padding: 7px 0 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }

    .toolbar-card.controls {
      border-left: 1px solid var(--border-color);
      padding-left: 10px;
    }

    .compact-select {
      height: 28px;
      border-radius: 6px;
      background: var(--surface-bg);
    }

    .mini-limit-fill,
    .send-button,
    .auth-actions button.primary,
    .decision-actions button[data-decision="accept"],
    .decision-actions button[data-action="answer-interaction"],
    .banner-action.primary {
      background: color-mix(in srgb, var(--accent-color) 62%, black 38%);
      box-shadow: none;
    }

    .send-button {
      min-width: 42px;
      height: 42px;
      padding: 0 13px;
      border-radius: 8px;
    }

    .send-button:hover {
      background: color-mix(in srgb, var(--accent-color) 64%, black 36%);
      transform: translateY(-1px);
    }

    .message-list {
      padding: 20px 0 8px;
      gap: 18px;
    }

    .message {
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 10px;
    }

    .message.user {
      grid-template-columns: minmax(0, 1fr) 24px;
    }

    .avatar {
      width: 24px;
      height: 24px;
      border-radius: 6px;
      background: var(--surface-muted);
      border-color: var(--border-color);
      box-shadow: none;
      color: var(--muted-color);
    }

    .message.user .avatar {
      background: color-mix(in srgb, var(--accent-color) 12%, var(--surface-bg) 88%);
      border-color: color-mix(in srgb, var(--accent-color) 26%, var(--border-color) 74%);
      color: var(--accent-color);
    }

    .bubble,
    .message.user .bubble {
      max-width: min(760px, 100%);
      padding: 8px 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }

    .message.user .bubble {
      padding: 10px 12px;
      border: 1px solid color-mix(in srgb, var(--accent-color) 18%, var(--border-color) 82%);
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent-color) 8%, var(--surface-bg) 92%);
    }

    .bubble-text {
      font-family: var(--paper-font-body1_-_font-family, var(--primary-font-family, system-ui, sans-serif));
      font-size: 14px;
      line-height: 1.6;
    }

    .composer-shell {
      position: sticky;
      z-index: 2;
      bottom: 12px;
      width: min(calc(100% - 32px), 900px);
      margin: 8px auto 12px;
      padding: 10px 10px max(10px, env(safe-area-inset-bottom));
      border: 1px solid color-mix(in srgb, var(--border-color) 88%, var(--text-color) 12%);
      border-radius: 12px;
      background: var(--surface-bg);
      box-shadow: 0 12px 30px color-mix(in srgb, var(--text-color) 13%, transparent);
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }

    .composer-shell:focus-within {
      border-color: color-mix(in srgb, var(--accent-color) 56%, var(--border-color) 44%);
      box-shadow: 0 14px 34px color-mix(in srgb, var(--text-color) 15%, transparent), 0 0 0 3px color-mix(in srgb, var(--accent-color) 12%, transparent);
      transform: translateY(-1px);
    }

    .composer-shell.retry-ready {
      border-color: color-mix(in srgb, var(--brand-amber) 50%, var(--border-color) 50%);
      box-shadow: 0 10px 24px color-mix(in srgb, var(--brand-amber) 12%, transparent);
    }

    .composer-shell.retry-ready .composer-status {
      color: color-mix(in srgb, var(--brand-amber) 70%, var(--text-color) 30%);
      font-weight: 650;
    }

    .composer textarea {
      min-height: 88px;
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface-muted) 86%, var(--surface-bg) 14%);
      box-shadow: none;
    }

    .empty-state.empty-state-main {
      align-content: center;
      justify-items: center;
      min-height: clamp(280px, 48vh, 520px);
      padding: 48px 24px;
      color: var(--text-color);
    }

    .empty-state-main .empty-state-body {
      display: grid;
      justify-items: center;
      gap: 10px;
      max-width: 440px;
    }

    .empty-state-mark {
      display: inline-grid;
      width: 52px;
      height: 52px;
      margin-bottom: 6px;
      place-items: center;
      border: 1px solid color-mix(in srgb, var(--accent-color) 24%, var(--border-color) 76%);
      border-radius: 14px;
      background: color-mix(in srgb, var(--accent-color) 9%, var(--surface-bg) 91%);
      color: color-mix(in srgb, var(--accent-color) 70%, var(--text-color) 30%);
      box-shadow: 0 10px 28px color-mix(in srgb, var(--text-color) 10%, transparent);
    }

    .empty-state-mark svg {
      width: 28px;
      height: 28px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .empty-state-main .empty-state-kicker {
      color: var(--muted-color);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .empty-state-main .title {
      font-size: clamp(22px, 3vw, 30px);
      letter-spacing: -0.025em;
      white-space: normal;
    }

    .empty-state-main .empty-note {
      max-width: 360px;
      font-size: 14px;
    }

    .empty-state-cta {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 40px;
      margin-top: 6px;
      padding: 0 14px;
      border-color: transparent;
      border-radius: 8px;
      color: var(--surface-bg);
      background: color-mix(in srgb, var(--accent-color) 64%, var(--text-color) 36%);
      font-weight: 650;
    }

    .empty-state-cta svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .empty-state-cta:hover {
      border-color: transparent;
      background: color-mix(in srgb, var(--accent-color) 72%, var(--text-color) 28%);
    }

    .interaction-summary {
      position: sticky;
      top: 0;
      z-index: 1;
      display: flex;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
      padding: 2px 2px 4px;
      background: var(--surface-bg);
      color: var(--text-color);
    }

    .interaction-summary strong {
      font-size: 13px;
      font-weight: 700;
    }

    .interaction-summary-count,
    .interaction-summary-cue {
      color: var(--muted-color);
      font-size: 11px;
    }

    .interaction-summary-cue {
      margin-left: auto;
      text-align: right;
    }

    .error-strip {
      background: var(--danger-surface);
      color: var(--text-color);
    }

    .error-strip.visible {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
      padding: 10px 11px;
      border-color: color-mix(in srgb, var(--danger-color) 34%, var(--border-color) 66%);
      border-left-width: 3px;
    }

    .error-copy {
      display: grid;
      gap: 2px;
      min-width: 0;
    }

    .error-title {
      color: var(--text-color);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
    }

    .error-message {
      color: var(--muted-color);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .error-actions {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }

    .error-action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 30px;
      padding: 0 9px;
      border-radius: 6px;
      color: var(--text-color);
      background: var(--surface-bg);
      font-size: 11px;
      font-weight: 650;
    }

    .error-action.primary {
      border-color: color-mix(in srgb, var(--accent-color) 48%, var(--border-color) 52%);
      background: color-mix(in srgb, var(--accent-color) 12%, var(--surface-bg) 88%);
    }

    .error-action svg {
      width: 14px;
      height: 14px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .status-banner {
      background: var(--warning-surface);
      color: var(--text-color);
    }

    .status-banner.error {
      background: var(--danger-surface);
      color: var(--text-color);
    }

    .approval-card,
    .user-input-card {
      background: var(--surface-bg);
      box-shadow: none;
    }

    .approval-card .decision-command,
    .approval-card .decision-scope,
    .user-input-card fieldset,
    .mode-boundaries {
      background: var(--surface-alt);
    }

    .status-pill.running,
    .message.user .avatar,
    .progress-dot.active {
      background: var(--accent-surface);
    }

    .status-pill.idle,
    .progress-dot.complete,
    .tool-chip.available {
      background: var(--success-surface);
    }

    .status-pill.error,
    .progress-dot.error,
    .stop-button {
      background: var(--danger-surface);
    }

    .runtime-notice {
      background: var(--warning-surface);
      color: var(--text-color);
    }

    .auth-plan {
      color: var(--text-color);
      background: var(--surface-alt);
    }

    .toolbar-card,
    .compact-select,
    .copy-button,
    .attachment-chip,
    .tool-chip,
    .file-row.active,
    .browser-card,
    .panel-form {
      background: var(--surface-bg);
      box-shadow: none;
    }

    .onboarding-shell,
    .onboarding-stage {
      background: var(--surface-muted);
      box-shadow: none;
    }

    .side-scroll {
      gap: 0;
      padding: 0;
    }

    .side-section {
      gap: 8px;
      padding: 12px 14px;
      border: 0;
      border-bottom: 1px solid var(--border-color);
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }

    .mobile-header-actions,
    .mobile-drawer-scrim {
      display: none;
    }

    @media (max-width: 1120px) {
      .shell {
        grid-template-columns: minmax(220px, 264px) minmax(0, 1fr);
        grid-template-rows: minmax(0, 1fr) clamp(260px, 34vh, 340px);
      }

      .onboarding-checklist {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .side-pane {
        grid-column: 1 / -1;
        grid-row: 2;
        min-height: 0;
      }
    }

    @media (max-width: 880px) {
      .shell {
        display: block;
        height: 100dvh;
        max-height: 100dvh;
        min-height: 100dvh;
        overflow: hidden;
      }

      .main-pane {
        min-height: 100dvh;
        height: 100dvh;
        position: relative;
        z-index: 1;
      }

      .main-header {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto auto;
        padding-inline: 16px;
      }

      .main-header .status-text {
        display: none;
      }

      .mobile-header-actions {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-left: 0;
      }

      .main-header .row-actions {
        gap: 4px;
      }

      .mobile-drawer-toggle {
        display: inline-flex;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        background: var(--surface-muted);
      }

      .mobile-drawer-toggle svg {
        pointer-events: none;
      }

      .rail-pane,
      .side-pane {
        position: fixed;
        top: 0;
        bottom: 0;
        z-index: 4;
        width: min(86vw, 324px);
        min-height: 100dvh;
        height: 100dvh;
        transition: transform 180ms ease, box-shadow 180ms ease;
        box-shadow: 0 12px 32px color-mix(in srgb, var(--text-color) 18%, transparent);
        will-change: transform;
      }

      .rail-pane {
        left: 0;
        border-right: 1px solid var(--border-color);
        transform: translateX(-105%);
      }

      .side-pane {
        right: 0;
        border-left: 1px solid var(--border-color);
        transform: translateX(105%);
      }

      .rail-pane.drawer-open,
      .side-pane.drawer-open {
        transform: translateX(0);
      }

      .mobile-drawer-scrim {
        display: block;
        position: fixed;
        inset: 0;
        z-index: 3;
        width: 100%;
        height: 100%;
        border: 0;
        border-radius: 0;
        background: color-mix(in srgb, var(--text-color) 28%, transparent);
        cursor: default;
        opacity: 0;
        transition: opacity 180ms ease;
      }

      .mobile-drawer-scrim[hidden] {
        display: none;
      }

      .mobile-drawer-scrim.open {
        opacity: 1;
      }

      .main-top,
      .interaction-region,
      .message-list,
      .composer-shell {
        width: calc(100% - 24px);
      }

      .compact-toolbar {
        grid-template-columns: 1fr;
      }

      .field-grid {
        grid-template-columns: 1fr;
      }

      .composer {
        grid-template-columns: 1fr;
      }

      .onboarding-checklist {
        grid-template-columns: 1fr;
      }

      .onboarding-heading {
        align-items: flex-start;
        flex-direction: column;
        gap: 3px;
      }

      .interaction-region {
        flex: 0 1 auto;
        max-height: 38vh;
        min-height: 0;
        padding-inline: 0;
      }

      .interaction-summary {
        flex-wrap: wrap;
        gap: 4px 8px;
      }

      .interaction-summary-cue {
        width: 100%;
        margin-left: 0;
        text-align: left;
      }

      .main-top {
        max-height: none;
        overflow: visible;
      }

      .message-list {
        flex: 1 1 auto;
        min-height: 0;
      }

      .composer-shell {
        bottom: 8px;
        margin-block: 8px;
      }

      .decision-actions button {
        flex: 1 1 120px;
      }

      .error-strip.visible {
        grid-template-columns: 1fr;
        gap: 8px;
      }

      .error-actions {
        width: 100%;
      }

      .error-action.primary {
        flex: 1 1 auto;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      *,
      *::before,
      *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
      }
    }
  </style>
  <div class="shell">
    <aside class="pane rail-pane" id="workspace-drawer">
      <div class="rail-header">
        <div class="title-block">
          <span class="eyeline">Workspace</span>
          <span class="title" id="panel-title">Codex Bridge</span>
          <span class="account-pill unavailable" id="account-pill">Account unavailable</span>
        </div>
      </div>
      <div class="rail-actions">
        <button class="tool-button" type="button" data-action="new-direct-chat" id="new-direct-chat-button"></button>
        <button class="tool-button" type="button" data-action="toggle-project-form" id="new-project-button"></button>
        <label class="search-shell" for="search-input">
          <span id="search-icon"></span>
          <input id="search-input" type="text" placeholder="Search chats and projects" />
        </label>
      </div>
      <div class="forms-stack">
        <section class="panel-form" id="project-form-panel"></section>
        <section class="panel-form" id="thread-form-panel"></section>
      </div>
      <div class="section-scroll">
        <div class="rail-sections">
          <section class="rail-section" id="direct-section"></section>
          <section class="rail-section flat" id="project-section"></section>
          <section class="rail-section" id="archived-section"></section>
        </div>
      </div>
    </aside>

    <main class="pane main-pane">
      <div class="main-header">
        <div class="title-block">
          <span class="eyeline" id="thread-project-label">Ready</span>
          <span class="title" id="thread-title-label">Select a chat</span>
          <span class="subline" id="thread-path-label"></span>
        </div>
        <div class="mobile-header-actions" role="group" aria-label="Panel navigation">
          <button class="icon-button mobile-drawer-toggle" type="button" data-action="toggle-mobile-nav" id="mobile-nav-toggle" aria-label="Chats" aria-controls="workspace-drawer" aria-expanded="false"></button>
          <button class="icon-button mobile-drawer-toggle" type="button" data-action="toggle-mobile-context" id="mobile-context-toggle" aria-label="Context" aria-controls="context-drawer" aria-expanded="false"></button>
        </div>
        <div class="row-actions">
          <div class="status-text" id="thread-status-text"></div>
          <button class="icon-button stop-button hidden" type="button" data-action="stop-run" title="Stop run" aria-label="Stop run" id="stop-run-button"></button>
          <button class="icon-button" type="button" data-action="refresh-thread" title="Refresh" aria-label="Refresh" id="refresh-thread-button"></button>
        </div>
      </div>
      <div class="main-top">
        <div class="runtime-shell" id="runtime-strip"></div>
        <div class="error-strip" id="error-strip" role="alert" aria-live="assertive"></div>
        <section class="onboarding-shell" id="onboarding-shell">
          <div class="onboarding-heading">
            <strong id="onboarding-title">Home Assistant setup</strong>
            <span id="onboarding-summary">Everything stays behind your Home Assistant sign-in.</span>
          </div>
          <div id="onboarding"></div>
        </section>
        <div class="status-banner" id="status-banner" role="status" aria-live="polite"></div>
      </div>
      <section class="interaction-region" id="interaction-region" aria-label="Codex decisions" aria-live="polite" aria-relevant="additions removals"></section>
      <div class="message-list" id="message-list" role="log" aria-live="polite" aria-relevant="additions"></div>
      <div class="composer-shell">
        <div class="attachment-toolbar">
          <div class="attachment-actions">
            <button class="icon-button" type="button" data-action="upload-file" title="Upload files" aria-label="Upload files" id="upload-file-button"></button>
            <button class="icon-button" type="button" data-action="upload-folder" title="Upload folder" aria-label="Upload folder" id="upload-folder-button"></button>
            <span class="label-text" id="attachment-meta"></span>
          </div>
          <div class="attachment-chips" id="attachment-chip-list"></div>
        </div>
        <div class="composer">
          <textarea id="prompt-input" placeholder="Message Codex through Home Assistant" aria-label="Message Codex" aria-describedby="composer-status"></textarea>
          <button class="send-button" type="button" data-action="send-prompt" id="send-button" aria-describedby="composer-status"></button>
        </div>
        <div class="compact-toolbar" id="compact-toolbar"></div>
        <p class="composer-status" id="composer-status" role="status" aria-live="polite"></p>
        <input id="file-input" type="file" multiple class="hidden" />
        <input id="folder-input" type="file" webkitdirectory directory multiple class="hidden" />
      </div>
    </main>

    <button class="mobile-drawer-scrim" type="button" data-action="close-mobile-drawer" id="mobile-drawer-scrim" aria-label="Close panel drawer" hidden></button>

    <aside class="pane side-pane" id="context-drawer">
      <div class="side-header">
        <div class="title-block">
          <span class="eyeline">Context</span>
          <span class="title">Progress and artifacts</span>
        </div>
      </div>
      <div class="side-scroll">
        <section class="side-section">
          <span class="section-label">ChatGPT account</span>
          <div id="auth-panel"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Progress</span>
          <div class="progress-list" id="progress-list" role="list" aria-label="Chat progress" aria-live="polite"></div>
        </section>
        <section class="side-section">
          <div class="section-head-row">
            <span class="section-label">Artifacts</span>
            <button class="icon-button small" type="button" data-action="create-workspace-archive" title="Zip this chat workspace" aria-label="Zip this chat workspace" id="workspace-archive-button"></button>
          </div>
          <div class="artifact-list" id="artifact-list"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Preview</span>
          <div class="artifact-preview" id="artifact-preview"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Details</span>
          <div class="context-list" id="context-list"></div>
        </section>
        <section class="side-section">
          <span class="section-label">Versions</span>
          <div class="diagnostics-list" id="diagnostics-list"></div>
        </section>
      </div>
    </aside>
  </div>
`;
var iconSvg = (path) => `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    ${path}
  </svg>
`;
var icons = {
  brand: iconSvg('<path d="m10 4-6 4v8l6 4v-3l-3-2V9l3-2Z"></path><path d="m14 4 6 4v8l-6 4v-3l3-2V9l-3-2Z"></path><path d="M10 12h4"></path><path d="M9 15h6"></path><path d="M8 18h8"></path>'),
  plus: iconSvg('<path d="M12 5v14"></path><path d="M5 12h14"></path>'),
  refresh: iconSvg('<path d="M20 11a8 8 0 1 0 2 5.3"></path><path d="M20 4v7h-7"></path>'),
  upload: iconSvg('<path d="M12 16V4"></path><path d="m7 9 5-5 5 5"></path><path d="M5 20h14"></path>'),
  folderUpload: iconSvg('<path d="M3 7h6l2 2h10v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><path d="M12 17V9"></path><path d="m8.5 12.5 3.5-3.5 3.5 3.5"></path>'),
  send: iconSvg('<path d="m22 2-7 20-4-9-9-4 20-7Z"></path><path d="M22 2 11 13"></path>'),
  stop: iconSvg('<rect x="6" y="6" width="12" height="12" rx="2"></rect>'),
  download: iconSvg('<path d="M12 4v12"></path><path d="m7 11 5 5 5-5"></path><path d="M5 20h14"></path>'),
  user: iconSvg('<path d="M20 21a8 8 0 1 0-16 0"></path><circle cx="12" cy="7" r="4"></circle>'),
  bot: iconSvg('<rect x="5" y="7" width="14" height="10" rx="4"></rect><path d="M12 3v4"></path><circle cx="10" cy="12" r="1"></circle><circle cx="14" cy="12" r="1"></circle>'),
  folder: iconSvg('<path d="M3 7h6l2 2h10v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2"></path>'),
  edit: iconSvg('<path d="M12 20h9"></path><path d="m16.5 3.5 4 4L8 20H4v-4Z"></path>'),
  chat: iconSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"></path>'),
  copy: iconSvg('<rect x="9" y="9" width="10" height="10" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'),
  save: iconSvg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"></path><path d="M17 21v-8H7v8"></path><path d="M7 3v5h8"></path>'),
  browse: iconSvg('<path d="M3 12h18"></path><path d="M12 3v18"></path>'),
  search: iconSvg('<circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>'),
  chevronDown: iconSvg('<path d="m6 9 6 6 6-6"></path>'),
  chevronRight: iconSvg('<path d="m9 6 6 6-6 6"></path>'),
  archive: iconSvg('<path d="M3 7h18"></path><path d="M5 7v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7"></path><path d="M9 11h6"></path><path d="M4 4h16v3H4z"></path>'),
  restore: iconSvg('<path d="M3 7h18"></path><path d="M5 7v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7"></path><path d="m9 14 3-3 3 3"></path><path d="M12 11v7"></path><path d="M4 4h16v3H4z"></path>'),
  trash: iconSvg('<path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path>'),
  package: iconSvg('<path d="m3 8.5 9-4.5 9 4.5"></path><path d="M21 8.5v7L12 20l-9-4.5v-7"></path><path d="M12 4v16"></path>'),
  menu: iconSvg('<path d="M4 7h16"></path><path d="M4 12h16"></path><path d="M4 17h16"></path>'),
  panelRight: iconSvg('<rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M15 4v16"></path>')
};
var CodexBridgePanel = class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.appendChild(template.content.cloneNode(true));
    this._hass = null;
    this._panel = null;
    this._staticUiInstalled = false;
    this._config = null;
    this._status = null;
    this._projects = [];
    this._threads = [];
    this._selectedProjectId = null;
    this._selectedThreadId = null;
    this._threadSelectionEpoch = 0;
    this._threadSnapshotEpoch = 0;
    this._threadRefreshGraceUntil = 0;
    this._activeThread = null;
    this._events = [];
    this._artifacts = [];
    this._artifactPreview = null;
    this._selectedArtifactId = null;
    this._previewToken = 0;
    this._sequence = 0;
    this._draft = "";
    this._drafts = /* @__PURE__ */ new Map();
    this._searchQuery = "";
    this._showProjectForm = false;
    this._showThreadForm = false;
    this._projectFormMode = "create";
    this._editingProjectId = null;
    this._projectForm = {
      name: "",
      rootPath: "",
      defaultModel: "",
      defaultThinkingLevel: "medium"
    };
    this._threadForm = {
      title: "",
      mode: "full-auto",
      projectId: null
    };
    this._folderDraft = "";
    this._browseState = null;
    this._pollTimer = null;
    this._pollTick = 0;
    this._pollActive = false;
    this._pollGeneration = 0;
    this._pollInFlight = false;
    this._lastStatusRefreshAt = 0;
    this._eventUnsubscribe = null;
    this._eventSubscriptionPending = null;
    this._eventSubscriptionActive = false;
    this._eventSubscriptionGeneration = 0;
    this._eventRefreshTimer = null;
    this._eventReconnectTimer = null;
    this._eventReconnectAttempt = 0;
    this._eventStream = createEventStreamState();
    this._systemEventUnsubscribe = null;
    this._systemEventSubscriptionPending = null;
    this._systemEventSubscriptionActive = false;
    this._systemEventGeneration = 0;
    this._systemEventCursor = 0;
    this._systemRefreshTimer = null;
    this._systemReconnectTimer = null;
    this._systemReconnectAttempt = 0;
    this._confirmSignOut = false;
    this._authActionPending = false;
    this._authPollTimer = null;
    this._authPollInFlight = false;
    this._authPollGeneration = 0;
    this._uploadProgress = null;
    this._uploadAbortController = null;
    this._isLoading = false;
    this._error = "";
    this._errorRetryable = false;
    this._errorSource = "";
    this._errorRevision = 0;
    this._dismissedBannerKey = "";
    this._renderedThreadId = null;
    this._renderedSequence = 0;
    this._renderedToolbarKey = "";
    this._renderedProjectFormKey = "";
    this._renderedThreadFormKey = "";
    this._forceMessageRebuild = true;
    this._pendingUploads = 0;
    this._pendingInteractions = [];
    this._interactionMutations = /* @__PURE__ */ new Map();
    this._interactionAnswers = /* @__PURE__ */ new Map();
    this._announcedInteractionIds = /* @__PURE__ */ new Set();
    this._interactionExpiryTimer = null;
    this._promptMutations = /* @__PURE__ */ new Map();
    this._promptMutation = null;
    this._suspendUiRefresh = false;
    this._queuedRender = false;
    this._collapsedProjects = {};
    this._collapsedSections = {
      direct: false,
      archived: true
    };
    this._mobileDrawer = null;
    this._mobileDrawerReturnFocus = null;
    this._mobileDrawerMedia = null;
    this._mobileDrawerMediaListener = null;
    this._mobileDrawerMediaListening = false;
  }
  connectedCallback() {
    this._installStaticUi();
    if (this._mobileDrawerMedia && this._mobileDrawerMediaListener && !this._mobileDrawerMediaListening) {
      this._mobileDrawerMedia.addEventListener("change", this._mobileDrawerMediaListener);
      this._mobileDrawerMediaListening = true;
    }
    if (this._config && this._hass) {
      this._startSystemEventSubscription();
    }
    if (this._selectedThreadId && this._hass) {
      this._startEventSubscription();
    }
    this._syncAuthPolling();
    this._render();
  }
  disconnectedCallback() {
    this._stopPolling();
    this._stopEventSubscription();
    this._stopSystemEventSubscription();
    this._stopAuthPolling();
    this._clearInteractionExpiryTimer();
    this._uploadAbortController?.abort();
    this._uploadAbortController = null;
    this._revokePreviewUrl();
    this._mobileDrawerMedia?.removeEventListener("change", this._mobileDrawerMediaListener);
    this._mobileDrawerMediaListening = false;
  }
  set hass(value) {
    this._hass = value;
    if (!this._config) {
      this._bootstrap();
      return;
    }
    this._render();
  }
  get hass() {
    return this._hass;
  }
  set panel(value) {
    this._panel = value;
    this._render();
  }
  get panel() {
    return this._panel;
  }
  async _bootstrap() {
    if (!this._hass || this._isLoading) {
      return;
    }
    this._isLoading = true;
    try {
      this._config = await this._callWS("get_config");
      await this._startSystemEventSubscription();
      await Promise.all([this._loadStatus(), this._loadProjects()]);
      await this._loadThreads();
      this._clearError();
    } catch (error) {
      this._setError(error);
    } finally {
      this._isLoading = false;
      this._render();
    }
  }
  async _callWS(action, payload = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `codex_bridge/${action}`,
      ...payload
    });
  }
  _accessToken() {
    return this._hass?.auth?.data?.access_token || this._hass?.auth?.data?.accessToken || this._hass?.auth?.accessToken || this._hass?.connection?.options?.auth?.accessToken || "";
  }
  _installStaticUi() {
    if (this._staticUiInstalled) {
      return;
    }
    this._staticUiInstalled = true;
    this._setTrustedButtonContent(this.shadowRoot.getElementById("new-direct-chat-button"), icons.chat, "New chat");
    this._setTrustedButtonContent(this.shadowRoot.getElementById("new-project-button"), icons.plus, "New project");
    this._setTrustedButtonContent(this.shadowRoot.getElementById("search-icon"), icons.search);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("refresh-thread-button"), icons.refresh);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("stop-run-button"), icons.stop);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("upload-file-button"), icons.upload);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("upload-folder-button"), icons.folderUpload);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("workspace-archive-button"), icons.package);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("send-button"), icons.send, "Send");
    this._setTrustedButtonContent(this.shadowRoot.getElementById("mobile-nav-toggle"), icons.menu);
    this._setTrustedButtonContent(this.shadowRoot.getElementById("mobile-context-toggle"), icons.panelRight);
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
    this.shadowRoot.addEventListener("change", (event) => this._handleChange(event));
    this.shadowRoot.addEventListener("paste", (event) => this._handlePaste(event));
    this.shadowRoot.addEventListener("keydown", (event) => this._handleKeyDown(event));
    this.shadowRoot.addEventListener("focusin", (event) => this._handleFocusIn(event));
    this.shadowRoot.addEventListener("focusout", (event) => this._handleFocusOut(event));
    this._mobileDrawerMedia = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 880px)") : {
      matches: false,
      addEventListener() {
      },
      removeEventListener() {
      }
    };
    this._mobileDrawerMediaListener = () => {
      this._syncMobileDrawer();
      queueMicrotask(() => this._scrollInteractionTargetIntoView(this.shadowRoot.activeElement));
    };
    this._mobileDrawerMedia.addEventListener("change", this._mobileDrawerMediaListener);
    this._mobileDrawerMediaListening = true;
    this._syncMobileDrawer();
    this.shadowRoot.getElementById("file-input").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length) {
        this._uploadFiles(files, { useRelativePaths: false });
      }
      event.target.value = "";
    });
    this.shadowRoot.getElementById("folder-input").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length) {
        this._uploadFiles(files, { useRelativePaths: true });
      }
      event.target.value = "";
    });
  }
  _toggleMobileDrawer(drawer, trigger) {
    if (!this._mobileDrawerMedia?.matches) {
      return;
    }
    if (this._mobileDrawer === drawer) {
      this._closeMobileDrawer();
      return;
    }
    this._mobileDrawer = drawer;
    this._mobileDrawerReturnFocus = trigger instanceof HTMLElement ? trigger : null;
    this._syncMobileDrawer();
    const drawerElement = this.shadowRoot.getElementById(
      drawer === "navigation" ? "workspace-drawer" : "context-drawer"
    );
    queueMicrotask(() => {
      const firstControl = drawerElement?.querySelector("button:not(:disabled), input:not(:disabled), select:not(:disabled)");
      firstControl?.focus();
    });
  }
  _closeMobileDrawer({ restoreFocus = true } = {}) {
    if (!this._mobileDrawer) {
      return;
    }
    const returnFocus = this._mobileDrawerReturnFocus;
    this._mobileDrawer = null;
    this._mobileDrawerReturnFocus = null;
    this._syncMobileDrawer();
    if (restoreFocus) {
      queueMicrotask(() => returnFocus?.focus());
    }
  }
  _syncMobileDrawer() {
    const navigation = this.shadowRoot.getElementById("workspace-drawer");
    const context = this.shadowRoot.getElementById("context-drawer");
    const main = this.shadowRoot.querySelector(".main-pane");
    const scrim = this.shadowRoot.getElementById("mobile-drawer-scrim");
    const navigationToggle = this.shadowRoot.getElementById("mobile-nav-toggle");
    const contextToggle = this.shadowRoot.getElementById("mobile-context-toggle");
    const mobile = Boolean(this._mobileDrawerMedia?.matches);
    if (!mobile) {
      this._mobileDrawer = null;
      this._mobileDrawerReturnFocus = null;
      for (const drawer of [navigation, context]) {
        drawer?.classList.remove("drawer-open");
        drawer?.removeAttribute("aria-hidden");
        if (drawer) drawer.inert = false;
      }
      if (main) main.inert = false;
      scrim?.classList.remove("open");
      if (scrim) scrim.hidden = true;
      navigationToggle?.setAttribute("aria-expanded", "false");
      contextToggle?.setAttribute("aria-expanded", "false");
      return;
    }
    const navigationOpen = this._mobileDrawer === "navigation";
    const contextOpen = this._mobileDrawer === "context";
    navigation?.classList.toggle("drawer-open", navigationOpen);
    context?.classList.toggle("drawer-open", contextOpen);
    if (navigation) {
      navigation.inert = !navigationOpen;
      navigation.setAttribute("aria-hidden", String(!navigationOpen));
    }
    if (context) {
      context.inert = !contextOpen;
      context.setAttribute("aria-hidden", String(!contextOpen));
    }
    if (main) main.inert = Boolean(this._mobileDrawer);
    if (scrim) {
      scrim.hidden = !this._mobileDrawer;
      scrim.classList.toggle("open", Boolean(this._mobileDrawer));
    }
    navigationToggle?.setAttribute("aria-expanded", String(navigationOpen));
    contextToggle?.setAttribute("aria-expanded", String(contextOpen));
  }
  _handleClick(event) {
    const actionTarget = event.target.closest("[data-action]");
    if (!actionTarget) {
      return;
    }
    const action = actionTarget.dataset.action;
    switch (action) {
      case "toggle-mobile-nav":
        this._toggleMobileDrawer("navigation", actionTarget);
        break;
      case "toggle-mobile-context":
        this._toggleMobileDrawer("context", actionTarget);
        break;
      case "close-mobile-drawer":
        this._closeMobileDrawer();
        break;
      case "new-direct-chat":
        this._openThreadFormForProject(null);
        break;
      case "toggle-project-form":
        this._openProjectFormForCreate();
        break;
      case "refresh-thread":
        this._refreshActiveThread();
        break;
      case "retry-error":
        this._retryError();
        break;
      case "dismiss-error":
        this._clearError();
        this._render();
        break;
      case "save-project":
        this._saveProject();
        break;
      case "cancel-project-form":
        this._closeProjectForm();
        break;
      case "browse-current":
        this._browseProjectPath(this._projectForm.rootPath || this._browseState?.path || null);
        break;
      case "browse-up":
        this._browseProjectPath(this._browseState?.parent_path || null);
        break;
      case "browse-roots":
        this._browseProjectPath(null);
        break;
      case "browse-entry":
        this._selectBrowseEntry(actionTarget.dataset.path || "");
        break;
      case "create-folder":
        this._createFolder();
        break;
      case "save-thread":
        this._createThread();
        break;
      case "cancel-thread-form":
        this._showThreadForm = false;
        this._render();
        break;
      case "toggle-section":
        this._toggleSection(actionTarget.dataset.section || "");
        break;
      case "toggle-project-collapse":
        this._toggleProjectCollapse(actionTarget.dataset.projectId || "");
        break;
      case "select-project":
        this._closeMobileDrawer({ restoreFocus: false });
        this._selectProject(actionTarget.dataset.projectId || null);
        break;
      case "edit-project":
        this._openProjectFormForEdit(actionTarget.dataset.projectId || "");
        break;
      case "archive-project":
        this._archiveProject(actionTarget.dataset.projectId || "");
        break;
      case "restore-project":
        this._restoreProject(actionTarget.dataset.projectId || "");
        break;
      case "delete-project":
        this._deleteProject(actionTarget.dataset.projectId || "");
        break;
      case "new-chat":
        this._openThreadFormForProject(actionTarget.dataset.projectId || null);
        break;
      case "select-thread":
        this._closeMobileDrawer({ restoreFocus: false });
        this._selectThread(actionTarget.dataset.threadId || null);
        break;
      case "archive-thread":
        this._archiveThread(actionTarget.dataset.threadId || "");
        break;
      case "restore-thread":
        this._restoreThread(actionTarget.dataset.threadId || "");
        break;
      case "delete-thread":
        this._deleteThread(actionTarget.dataset.threadId || "");
        break;
      case "send-prompt":
        this._sendPrompt();
        break;
      case "accept-interaction":
      case "decline-interaction":
      case "cancel-interaction":
        this._decideInteractionFromTarget(actionTarget, action.replace("-interaction", ""));
        break;
      case "answer-interaction":
        this._answerInteractionFromTarget(actionTarget);
        break;
      case "stop-run":
        this._cancelRun();
        break;
      case "upload-file":
        this.shadowRoot.getElementById("file-input").click();
        break;
      case "upload-folder":
        this.shadowRoot.getElementById("folder-input").click();
        break;
      case "select-artifact":
        this._selectArtifact(actionTarget.dataset.artifactId || "");
        break;
      case "download-artifact":
        this._downloadArtifact(actionTarget.dataset.artifactId || "");
        break;
      case "create-workspace-archive":
        this._createWorkspaceArchive();
        break;
      case "copy-message":
        this._copyMessage(actionTarget.dataset.sequence || "");
        break;
      case "copy-code-block":
        this._copyCodeBlock(actionTarget);
        break;
      case "start-auth-login":
        this._startAuthLogin();
        break;
      case "open-chatgpt":
        this._openChatGptSignIn();
        break;
      case "cancel-sign-in":
        this._cancelAuthLogin();
        break;
      case "confirm-sign-out":
        this._confirmSignOut = true;
        this._renderAuthSurface();
        break;
      case "sign-out":
        this._logoutAuth();
        break;
      case "refresh-auth-status":
        this._refreshAuthStatus();
        break;
      case "copy-auth-code":
        this._copyAuthCode();
        break;
      case "retry-app":
        this._retryAppConnection();
        break;
      case "dismiss-banner":
        this._dismissStatusBanner();
        break;
      default:
        break;
    }
  }
  _handleInput(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.closest("[data-interaction-id]")) {
      this._captureInteractionAnswers(target);
      return;
    }
    if (target.id === "prompt-input") {
      this._draft = target.value;
      this._setDraftForThread(this._selectedThreadId, target.value);
      return;
    }
    if (target.id === "search-input") {
      this._searchQuery = target.value;
      this._render();
      return;
    }
    if (target.id === "project-name-input") {
      this._projectForm.name = target.value;
      return;
    }
    if (target.id === "project-root-input") {
      this._projectForm.rootPath = target.value;
      return;
    }
    if (target.id === "thread-title-input") {
      this._threadForm.title = target.value;
      return;
    }
    if (target.id === "folder-name-input") {
      this._folderDraft = target.value;
    }
  }
  _handleChange(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.closest("[data-interaction-id]")) {
      this._captureInteractionAnswers(target);
      return;
    }
    if (target.id === "project-model-select") {
      this._projectForm.defaultModel = target.value;
      const supportedLevels = this._thinkingLevelsForModel(target.value);
      if (!supportedLevels.includes(this._projectForm.defaultThinkingLevel)) {
        this._projectForm.defaultThinkingLevel = this._defaultThinkingLevel(target.value);
      }
      const thinkingSelect = this.shadowRoot.getElementById("project-thinking-select");
      if (thinkingSelect) {
        thinkingSelect.replaceChildren();
        this._appendThinkingOptions(thinkingSelect, this._projectForm.defaultThinkingLevel, this._projectForm.defaultModel);
      }
      this._renderedProjectFormKey = this._projectFormRenderKey();
      return;
    }
    if (target.id === "project-thinking-select") {
      this._projectForm.defaultThinkingLevel = target.value;
      this._renderedProjectFormKey = this._projectFormRenderKey();
      return;
    }
    if (target.id === "thread-mode-select") {
      this._threadForm.mode = target.value;
      return;
    }
    if (target.id === "thread-model-select") {
      const modelOverride = target.value || null;
      const project = this._activeProject();
      const effectiveModel = modelOverride || project?.default_model || this._defaultModel();
      const effectiveThinkingLevel = this._activeThread?.thinking_override || this._activeThread?.effective_thinking_level || project?.default_thinking_level || this._defaultThinkingLevel(effectiveModel);
      const updates = { model_override: modelOverride };
      const modelRecord = this._modelRecords().find((item) => item.model === effectiveModel);
      const advertisedLevels = Array.isArray(modelRecord?.thinking_levels) ? modelRecord.thinking_levels : [];
      if (advertisedLevels.length && !advertisedLevels.includes(effectiveThinkingLevel)) {
        updates.thinking_override = this._defaultThinkingLevel(effectiveModel);
      }
      const thinkingSelect = this.shadowRoot.getElementById("thread-thinking-select");
      if (thinkingSelect) {
        const selectedThinkingOverride = Object.hasOwn(updates, "thinking_override") ? updates.thinking_override : this._activeThread?.thinking_override || null;
        this._populateThreadThinkingSelect(
          thinkingSelect,
          selectedThinkingOverride,
          effectiveModel,
          project?.default_thinking_level || null
        );
      }
      this._updateThreadSettings(updates);
      return;
    }
    if (target.id === "thread-thinking-select") {
      this._updateThreadSettings({ thinking_override: target.value || null });
    }
  }
  _handlePaste(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || target.id !== "prompt-input") {
      return;
    }
    const files = this._clipboardFiles(event.clipboardData);
    if (!files.length) {
      return;
    }
    event.preventDefault();
    if (!this._selectedThreadId) {
      this._setError("Select a chat before pasting a screenshot.");
      return;
    }
    this._uploadFiles(files, { useRelativePaths: false });
  }
  _handleKeyDown(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (event.key === "Escape" && this._mobileDrawer) {
      event.preventDefault();
      this._closeMobileDrawer();
      return;
    }
    const interactionCard = target.closest("[data-interaction-id]");
    if (interactionCard) {
      const interaction = this._pendingInteractions.find(
        (item) => item.interaction_id === interactionCard.dataset.interactionId
      );
      if (!interaction) {
        return;
      }
      if (event.key === "Escape" && interaction.kind !== "user_input") {
        const cancel = interactionCard.querySelector('[data-action="cancel-interaction"]:not(:disabled)');
        if (cancel) {
          event.preventDefault();
          this._decideInteraction(interaction.interaction_id, "cancel");
          this._focusPrompt();
        }
        return;
      }
      const wantsAnswer = interaction.kind === "user_input" && event.key === "Enter" && (event.ctrlKey || event.metaKey || target.tagName !== "TEXTAREA");
      if (wantsAnswer) {
        const submit = interactionCard.querySelector('[data-action="answer-interaction"]:not(:disabled)');
        if (submit) {
          event.preventDefault();
          this._answerInteractionFromTarget(submit);
        }
      }
      return;
    }
    if (target.id === "prompt-input" && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this._sendPrompt();
    }
  }
  _handleFocusIn(event) {
    const target = event.target;
    this._scrollInteractionTargetIntoView(target);
    if (!this._isRefreshLockTarget(target)) {
      return;
    }
    this._suspendUiRefresh = true;
  }
  _scrollInteractionTargetIntoView(target) {
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const interactionCard = target.closest("[data-interaction-id]");
    if (!interactionCard) {
      return;
    }
    const isControl = ["BUTTON", "INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
    const scrollTarget = isControl ? target : interactionCard;
    if (typeof scrollTarget.scrollIntoView === "function") {
      scrollTarget.scrollIntoView({ block: isControl ? "nearest" : "start", inline: "nearest" });
    }
  }
  _handleFocusOut(event) {
    if (!this._isRefreshLockTarget(event.target)) {
      return;
    }
    window.setTimeout(() => {
      const activeElement = this.shadowRoot.activeElement;
      if (this._isRefreshLockTarget(activeElement)) {
        return;
      }
      this._suspendUiRefresh = false;
      if (this._queuedRender) {
        this._render(true);
      }
    }, 0);
  }
  _render(force = false) {
    if (!force && this._suspendUiRefresh) {
      this._queuedRender = true;
      return;
    }
    this._queuedRender = false;
    const activeThread = this._activeThread;
    const activeProject = this._activeProject();
    const contextName = activeProject?.kind === "direct" ? "Direct chat" : activeProject?.name || "Ready";
    this.shadowRoot.getElementById("panel-title").textContent = this._config?.panel_title || "Codex Bridge";
    const accountPill = this.shadowRoot.getElementById("account-pill");
    const account = this._status?.account;
    accountPill.textContent = this._accountLabel(account);
    accountPill.title = this._accountTitle(account);
    accountPill.classList.toggle("unavailable", !this._authViewModel().signedIn);
    this.shadowRoot.getElementById("thread-project-label").textContent = contextName;
    this.shadowRoot.getElementById("thread-title-label").textContent = activeThread?.title || (activeProject?.kind === "direct" ? "Select a chat" : activeProject?.name || "Select a chat");
    this.shadowRoot.getElementById("thread-path-label").textContent = this._workspaceLabel(activeThread?.workspace_path || activeProject?.root_path, "");
    this.shadowRoot.getElementById("thread-status-text").textContent = activeThread ? `Status: ${activeThread.status}` : "";
    this.shadowRoot.getElementById("stop-run-button").classList.toggle(
      "hidden",
      !activeThread || activeThread.status !== "running"
    );
    this.shadowRoot.getElementById("attachment-meta").textContent = this._pendingUploads ? this._uploadProgressText() : activeThread ? `${activeThread.attachments.length} upload${activeThread.attachments.length === 1 ? "" : "s"} - paste screenshot` : "No chat selected";
    this._renderComposerState(activeThread);
    this._renderErrorSurface();
    this._renderRuntimeSurface();
    this._renderOnboardingSurface();
    this._renderAuthSurface();
    this._renderProjectForm();
    this._renderStatusBanner();
    this._renderThreadForm();
    this.shadowRoot.getElementById("project-form-panel").parentElement.classList.toggle(
      "hidden",
      !this._showProjectForm && !this._showThreadForm
    );
    this._renderDirectSection();
    this._renderProjectList();
    this._renderArchivedSection();
    this._renderToolbar();
    this._renderAttachmentChips();
    this._renderInteractions();
    this._renderMessages();
    this._renderProgress();
    this._renderArtifacts();
    this._renderArtifactPreview();
    this._renderContext();
    this._renderDiagnostics();
  }
  _renderErrorSurface() {
    const errorStrip = this.shadowRoot.getElementById("error-strip");
    errorStrip.replaceChildren();
    if (!this._error) {
      errorStrip.className = "error-strip";
      delete errorStrip.dataset.retryable;
      return;
    }
    errorStrip.className = "error-strip visible";
    errorStrip.dataset.retryable = String(this._errorRetryable);
    const copy = document.createElement("div");
    copy.className = "error-copy";
    copy.append(
      this._textElement("strong", "error-title", this._errorRetryable ? "Connection issue" : "Codex needs attention"),
      this._textElement("span", "error-message", this._error)
    );
    const actions = document.createElement("div");
    actions.className = "error-actions";
    const dismiss = this._actionButton("error-action", "dismiss-error", "Dismiss error");
    dismiss.textContent = "Dismiss";
    if (this._errorRetryable) {
      const retry = this._actionButton("error-action primary", "retry-error", "Retry connection");
      this._appendTrustedIcon(retry, icons.refresh);
      retry.append(this._textElement("span", "", "Retry"));
      actions.append(retry);
    }
    actions.append(dismiss);
    errorStrip.append(copy, actions);
  }
  _renderComposerState(activeThread) {
    const promptInput = this.shadowRoot.getElementById("prompt-input");
    const sendButton = this.shadowRoot.getElementById("send-button");
    const composerStatus = this.shadowRoot.getElementById("composer-status");
    const composerShell = this.shadowRoot.querySelector(".composer-shell");
    const isRunning = activeThread?.status === "running";
    const mutation = this._promptMutationForThread(this._selectedThreadId);
    const retryable = mutation?.state === "retryable";
    composerShell?.classList.toggle("retry-ready", retryable);
    const locked = Boolean(mutation);
    const draft = retryable ? mutation.prompt : this._draftForThread(this._selectedThreadId);
    if (promptInput.value !== draft) {
      promptInput.value = draft;
    }
    promptInput.placeholder = isRunning ? "Steer the running Codex turn" : "Message Codex through Home Assistant";
    promptInput.disabled = !activeThread || locked;
    sendButton.disabled = !activeThread || locked && !retryable || !retryable && !promptInput.value.trim();
    this._setTrustedButtonContent(
      sendButton,
      icons.send,
      retryable ? "Retry" : isRunning ? "Steer" : "Send"
    );
    sendButton.title = isRunning ? "Queue steering for this running Codex turn" : "Send message to Codex";
    if (mutation?.state === "sending") {
      composerStatus.textContent = "Sending through Home Assistant...";
    } else if (mutation?.state === "reconciling") {
      composerStatus.textContent = "Checking whether Home Assistant accepted this message...";
    } else if (retryable) {
      composerStatus.textContent = "The response was interrupted. Retry safely with the same request ID.";
    } else {
      composerStatus.textContent = activeThread ? "Enter sends; Shift+Enter adds a new line." : "Select a chat before sending a message.";
    }
  }
  _renderInteractions() {
    const region = this.shadowRoot.getElementById("interaction-region");
    region.replaceChildren();
    if (!this._selectedThreadId || !this._isSupervisorConnection()) {
      return;
    }
    const visibleInteractions = this._pendingInteractions.filter(
      (interaction) => interaction.thread_id === this._selectedThreadId
    );
    if (visibleInteractions.length) {
      const summary = document.createElement("div");
      summary.className = "interaction-summary";
      const heading = document.createElement("strong");
      heading.textContent = "Codex needs your input";
      const count = document.createElement("span");
      count.className = "interaction-summary-count";
      count.textContent = `${visibleInteractions.length} pending ${visibleInteractions.length === 1 ? "decision" : "decisions"}`;
      const cue = document.createElement("span");
      cue.className = "interaction-summary-cue";
      cue.textContent = "Tab through each action or scroll to review all.";
      summary.append(heading, count, cue);
      region.append(summary);
    }
    const newCards = [];
    for (const interaction of this._pendingInteractions) {
      if (interaction.thread_id !== this._selectedThreadId) {
        continue;
      }
      const wrapper = document.createElement("div");
      wrapper.className = "interaction-card";
      wrapper.dataset.interactionId = interaction.interaction_id;
      wrapper.dataset.interactionKind = interaction.kind;
      const mutation = this._interactionMutations.get(interaction.interaction_id) || null;
      const pending = mutation?.state === "sending" || mutation?.state === "reconciling";
      if (interaction.kind === "user_input") {
        const model = getUserInputViewModel(interaction, {
          pending,
          answers: this._interactionAnswers.get(interaction.interaction_id) || {}
        });
        renderUserInput(wrapper, model);
        if (mutation?.state === "retryable") {
          for (const fieldset of wrapper.querySelectorAll("fieldset")) {
            fieldset.disabled = true;
          }
          const submit = wrapper.querySelector('[data-action="answer-interaction"]');
          if (submit) {
            submit.disabled = false;
            submit.setAttribute("aria-disabled", "false");
            submit.textContent = "Retry answer";
          }
        }
      } else {
        const model = getApprovalViewModel(interaction, { pending });
        renderApproval(wrapper, model);
        if (mutation?.state === "retryable") {
          for (const button of wrapper.querySelectorAll("[data-decision]")) {
            const isOriginalDecision = button.dataset.decision === mutation.decision;
            button.disabled = !isOriginalDecision;
            button.setAttribute("aria-disabled", String(!isOriginalDecision));
            if (isOriginalDecision) {
              button.textContent = `Retry ${button.textContent.toLowerCase()}`;
            }
          }
        }
      }
      if (mutation?.state === "reconciling" || mutation?.state === "retryable") {
        const card = wrapper.querySelector("[role='alertdialog']");
        const notice = this._textElement(
          "p",
          "decision-notice",
          mutation.state === "reconciling" ? "Checking whether this response reached Codex..." : "The response was interrupted. Retrying will use the same request ID."
        );
        notice.setAttribute("role", "status");
        card?.insertBefore(notice, card.querySelector(".decision-actions"));
      }
      region.append(wrapper);
      if (!this._announcedInteractionIds.has(interaction.interaction_id)) {
        newCards.push(wrapper);
        this._announcedInteractionIds.add(interaction.interaction_id);
      }
    }
    const currentIds = new Set(this._pendingInteractions.map((item) => item.interaction_id));
    for (const interactionId of [...this._announcedInteractionIds]) {
      if (!currentIds.has(interactionId)) {
        this._announcedInteractionIds.delete(interactionId);
      }
    }
    this._scheduleInteractionExpiryRefresh();
    if (newCards.length) {
      const active = this.shadowRoot.activeElement;
      if (!active || !["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(active.tagName)) {
        newCards[0].querySelector("[role='alertdialog']")?.focus();
      }
    }
  }
  _captureInteractionAnswers(target) {
    const wrapper = target.closest("[data-interaction-id]");
    const interaction = this._pendingInteractions.find(
      (item) => item.interaction_id === wrapper?.dataset.interactionId && item.kind === "user_input"
    );
    if (!wrapper || !interaction) {
      return;
    }
    const model = getUserInputViewModel(interaction, {
      answers: this._interactionAnswers.get(interaction.interaction_id) || {}
    });
    const answers = collectUserInputAnswers(wrapper, model);
    this._interactionAnswers.set(
      interaction.interaction_id,
      Object.fromEntries(answers.map((answer) => [answer.question_id, answer.values]))
    );
    const submit = wrapper.querySelector('[data-action="answer-interaction"]');
    if (submit) {
      const complete = answers.length === model.questions.length && model.questions.length > 0;
      const locked = this._interactionMutations.has(interaction.interaction_id);
      submit.disabled = !complete || locked || !interaction.allowed_actions.includes("answer");
      submit.setAttribute("aria-disabled", String(submit.disabled));
    }
  }
  _decideInteractionFromTarget(target, decision) {
    const wrapper = target.closest("[data-interaction-id]");
    if (wrapper?.dataset.interactionId) {
      this._decideInteraction(wrapper.dataset.interactionId, decision);
    }
  }
  _answerInteractionFromTarget(target) {
    const wrapper = target.closest("[data-interaction-id]");
    const interaction = this._pendingInteractions.find(
      (item) => item.interaction_id === wrapper?.dataset.interactionId && item.kind === "user_input"
    );
    if (!wrapper || !interaction) {
      return;
    }
    const model = getUserInputViewModel(interaction, {
      answers: this._interactionAnswers.get(interaction.interaction_id) || {}
    });
    const answers = collectUserInputAnswers(wrapper, model);
    if (answers.length !== model.questions.length || !answers.length) {
      this._setError("Answer every Codex question before continuing.");
      return;
    }
    this._answerInteraction(interaction.interaction_id, answers);
  }
  async _decideInteraction(interactionId, decision) {
    const interaction = this._pendingInteractions.find((item) => item.interaction_id === interactionId);
    if (!interaction || interaction.kind === "user_input" || interaction.thread_id !== this._selectedThreadId || !interaction.allowed_actions.includes(decision)) {
      return;
    }
    await this._submitInteractionResponse(interaction, {
      action: "decide_interaction",
      kind: "decision",
      fingerprint: `decision:${decision}`,
      decision,
      payload: { decision }
    });
  }
  async _answerInteraction(interactionId, answers) {
    const interaction = this._pendingInteractions.find((item) => item.interaction_id === interactionId);
    if (!interaction || interaction.kind !== "user_input" || interaction.thread_id !== this._selectedThreadId || !interaction.allowed_actions.includes("answer")) {
      return;
    }
    const normalizedAnswers = answers.map((answer) => ({
      question_id: answer.question_id,
      values: [...answer.values]
    }));
    await this._submitInteractionResponse(interaction, {
      action: "answer_interaction",
      kind: "answer",
      fingerprint: `answer:${JSON.stringify(normalizedAnswers)}`,
      answers: normalizedAnswers,
      payload: { answers: normalizedAnswers }
    });
  }
  async _submitInteractionResponse(interaction, request) {
    const existing = this._interactionMutations.get(interaction.interaction_id) || null;
    if (existing && ["sending", "reconciling"].includes(existing.state)) {
      return;
    }
    if (existing && existing.fingerprint !== request.fingerprint) {
      this._setError("Retry the original Codex response before choosing another action.");
      return;
    }
    const mutation = existing || {
      threadId: interaction.thread_id,
      kind: request.kind,
      fingerprint: request.fingerprint,
      clientRequestId: this._createClientRequestId(request.kind),
      decision: request.decision || null,
      answers: request.answers || null,
      state: "sending"
    };
    mutation.state = "sending";
    this._interactionMutations.set(interaction.interaction_id, mutation);
    this._render();
    try {
      await this._callWS(request.action, {
        interaction_id: interaction.interaction_id,
        thread_id: interaction.thread_id,
        run_id: interaction.run_id,
        turn_id: interaction.turn_id,
        item_id: interaction.item_id,
        ...request.payload,
        client_request_id: mutation.clientRequestId
      });
      if (this._interactionMutations.get(interaction.interaction_id) !== mutation || interaction.thread_id !== this._selectedThreadId) {
        return;
      }
      mutation.state = "reconciling";
      mutation.deliveryConfirmed = true;
      this._render();
      let items;
      try {
        items = await this._refreshInteractions(interaction.thread_id);
      } catch {
        if (this._interactionMutations.get(interaction.interaction_id) === mutation && interaction.thread_id === this._selectedThreadId) {
          this._assignError("Home Assistant accepted this response. The request stays locked until its final state can be refreshed.");
          this._render();
        }
        return;
      }
      if (interaction.thread_id !== this._selectedThreadId) {
        return;
      }
      if (items.some((item) => item.interaction_id === interaction.interaction_id)) {
        this._assignError("Home Assistant accepted this response. The request stays locked while Codex finishes reconciling it.");
        this._render();
        return;
      }
      this._clearError();
      this._focusPrompt();
    } catch (error) {
      if (this._interactionMutations.get(interaction.interaction_id) !== mutation || interaction.thread_id !== this._selectedThreadId) {
        return;
      }
      const errorCode = this._bridgeErrorCode(error);
      mutation.state = "reconciling";
      this._render();
      let stillPending;
      try {
        const items = await this._listPendingInteractions(interaction.thread_id);
        stillPending = items.some((item) => item.interaction_id === interaction.interaction_id);
        if (interaction.thread_id === this._selectedThreadId) {
          this._replacePendingInteractions(items);
        }
      } catch {
        stillPending = true;
      }
      if (this._interactionMutations.get(interaction.interaction_id) !== mutation || interaction.thread_id !== this._selectedThreadId) {
        return;
      }
      if (!stillPending) {
        this._interactionMutations.delete(interaction.interaction_id);
        this._interactionAnswers.delete(interaction.interaction_id);
        if (errorCode === "interaction_outcome_unknown") {
          this._assignError("Codex received the response, but its final outcome could not be confirmed. Refresh before continuing.");
        } else if (stillPending) {
          this._assignError(error);
        } else {
          this._assignError("This Codex request is no longer pending.");
        }
        this._render();
        this._focusPrompt();
        return;
      }
      if (INTERACTION_ERROR_CODES.has(errorCode)) {
        mutation.state = "reconciling";
        this._assignError(errorCode === "interaction_outcome_unknown" ? "Codex received the response, but its final outcome could not be confirmed. This request stays locked while Home Assistant reconciles it." : "This Codex response could not be applied. The request stays locked until Home Assistant refreshes it.");
        this._render();
        return;
      }
      mutation.state = "retryable";
      this._assignError("The Home Assistant response was interrupted. Retry safely with the same request ID.");
      this._render();
    }
  }
  async _refreshInteractions(threadId = this._selectedThreadId) {
    if (!threadId || threadId !== this._selectedThreadId) {
      return [];
    }
    const items = await this._listPendingInteractions(threadId);
    if (threadId === this._selectedThreadId) {
      this._replacePendingInteractions(items);
      this._render();
    }
    return items;
  }
  _replacePendingInteractions(items) {
    const next = Array.isArray(items) ? items : [];
    const nextIds = new Set(next.map((item) => item.interaction_id));
    let resolved = false;
    for (const interaction of this._pendingInteractions) {
      if (!nextIds.has(interaction.interaction_id)) {
        resolved = resolved || this._interactionMutations.has(interaction.interaction_id);
        this._interactionMutations.delete(interaction.interaction_id);
        this._interactionAnswers.delete(interaction.interaction_id);
      }
    }
    this._pendingInteractions = next;
    if (resolved) {
      this._focusPrompt();
    }
  }
  async _listPendingInteractions(threadId) {
    if (!this._isSupervisorConnection() || !threadId) {
      return [];
    }
    const response = await this._callWS("list_pending_interactions", { thread_id: threadId });
    const items = Array.isArray(response?.items) ? response.items : [];
    return items.map((item) => this._normalizePendingInteraction(item, threadId)).filter(Boolean);
  }
  _normalizePendingInteraction(value, threadId) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    const identifier = (candidate, limit = 256) => typeof candidate === "string" && candidate.length <= limit && /^[A-Za-z0-9_.:-]+$/u.test(candidate) ? candidate : null;
    const interactionId = identifier(value.interaction_id, 128);
    const actualThreadId = identifier(value.thread_id, 128);
    const kind = ["command_approval", "file_change_approval", "user_input"].includes(value.kind) ? value.kind : null;
    const expiresAt = typeof value.expires_at === "string" && value.expires_at.length <= 64 && Number.isFinite(Date.parse(value.expires_at)) ? value.expires_at : null;
    const allowed = Array.isArray(value.allowed_actions) ? [...new Set(value.allowed_actions.filter((action) => ["accept", "decline", "cancel", "answer"].includes(action)))].slice(0, 4) : [];
    if (!interactionId || actualThreadId !== threadId || !kind || !identifier(value.run_id, 128) || !identifier(value.turn_id, 256) || !identifier(value.item_id, 256) || !Number.isSafeInteger(value.event_id) || value.event_id < 0 || value.status !== "pending" || !expiresAt || !value.display || typeof value.display !== "object" || Array.isArray(value.display) || (kind === "user_input" ? !allowed.includes("answer") : !allowed.some((action) => ["accept", "decline", "cancel"].includes(action)))) {
      return null;
    }
    return {
      interaction_id: interactionId,
      kind,
      thread_id: actualThreadId,
      run_id: value.run_id,
      turn_id: value.turn_id,
      item_id: value.item_id,
      event_id: value.event_id,
      status: "pending",
      expires_at: expiresAt,
      display: { ...value.display },
      allowed_actions: allowed
    };
  }
  _scheduleInteractionExpiryRefresh() {
    if (this._interactionExpiryTimer || !this._pendingInteractions.length) {
      return;
    }
    const now = Date.now();
    const expiry = Math.min(...this._pendingInteractions.map((item) => Date.parse(item.expires_at)));
    const remaining = expiry - now;
    const delay2 = remaining <= 0 ? 5e3 : Math.max(250, Math.min(2147483647, remaining + 25));
    this._interactionExpiryTimer = window.setTimeout(async () => {
      this._interactionExpiryTimer = null;
      if (!this._selectedThreadId) {
        return;
      }
      this._renderInteractions();
      try {
        await this._refreshInteractions(this._selectedThreadId);
      } catch {
        this._scheduleInteractionExpiryRefresh();
      }
    }, delay2);
  }
  _clearInteractionExpiryTimer() {
    if (this._interactionExpiryTimer) {
      window.clearTimeout(this._interactionExpiryTimer);
      this._interactionExpiryTimer = null;
    }
  }
  _focusPrompt() {
    const prompt = this.shadowRoot.getElementById("prompt-input");
    if (prompt && !prompt.disabled) {
      prompt.focus();
    }
  }
  _createClientRequestId(prefix = "request") {
    const uuid = globalThis.crypto?.randomUUID?.();
    if (uuid) {
      return `${prefix}-${uuid}`;
    }
    const bytes = new Uint8Array(16);
    globalThis.crypto?.getRandomValues?.(bytes);
    const entropy = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${prefix}-${Date.now().toString(36)}-${entropy || Math.random().toString(36).slice(2)}`;
  }
  _bridgeErrorCode(error) {
    const candidates = [
      error?.code,
      error?.body?.code,
      error?.body?.error?.code,
      error?.error?.code
    ];
    return candidates.find((value) => typeof value === "string") || "";
  }
  _isSupervisorConnection() {
    return this._config?.connection_type === "supervisor" && Number(this._config?.api_version) === 1;
  }
  _isLegacyConnection() {
    return !this._isSupervisorConnection() && Boolean(this._config);
  }
  _authViewModel() {
    return getAuthViewModel({
      ...this._status?.auth || {},
      account: this._status?.account || {},
      signedOutConfirmed: this._confirmSignOut
    });
  }
  _runtimeViewModel() {
    const diagnostics = this._status?.diagnostics || {};
    const bridgeReady = Boolean(this._status);
    return getRuntimeStripViewModel({
      api_version: this._config?.api_version,
      connection_type: this._config?.connection_type,
      app: {
        connected: this._isSupervisorConnection() && bridgeReady,
        version: diagnostics.app_version
      },
      integration: {
        ready: Boolean(this._config),
        version: PANEL_VERSION
      },
      bridge_ready: bridgeReady,
      codex_ready: bridgeReady && Boolean(diagnostics.active_codex_version || diagnostics.bundled_codex_version),
      diagnostics: {
        bridge_version: diagnostics.bridge_version,
        app_server_version: diagnostics.active_codex_version || diagnostics.bundled_codex_version
      }
    });
  }
  _renderRuntimeSurface() {
    const container = this.shadowRoot.getElementById("runtime-strip");
    renderRuntimeStrip(container, this._runtimeViewModel());
  }
  _renderOnboardingSurface() {
    const shell = this.shadowRoot.getElementById("onboarding-shell");
    const model = getOnboardingViewModel({
      appConnected: this._isSupervisorConnection() && Boolean(this._status),
      integrationReady: Boolean(this._config),
      bridgeReady: Boolean(this._status),
      signedIn: this._authViewModel().signedIn,
      workspaceReady: this._projects.some((project) => project.kind === "project" && !project.archived_at),
      threadCount: this._threads.filter((thread) => !thread.archived_at).length
    });
    shell.classList.toggle("hidden", this._isLegacyConnection());
    this.shadowRoot.getElementById("onboarding-title").textContent = model.complete ? "Home Assistant setup complete" : "Finish setup in Home Assistant";
    this.shadowRoot.getElementById("onboarding-summary").textContent = model.complete ? "The App, Integration, workspace, and ChatGPT account are ready." : "No Bridge address or account credential is exposed to this browser.";
    renderOnboarding(this.shadowRoot.getElementById("onboarding"), model);
  }
  _renderAuthSurface() {
    const container = this.shadowRoot.getElementById("auth-panel");
    if (this._isLegacyConnection()) {
      const card = document.createElement("section");
      card.className = "auth-card";
      card.append(
        this._textElement("strong", "", "Account controls need the Home Assistant App"),
        this._textElement(
          "p",
          "",
          "This older connection can show chats, but ChatGPT sign-in and sign-out are available only through the private Home Assistant App."
        )
      );
      container.replaceChildren(card);
      return;
    }
    renderAuth(container, this._authViewModel());
    if (this._authActionPending) {
      for (const button of container.querySelectorAll("button[data-action]")) {
        button.disabled = true;
      }
    }
  }
  _projectFormRenderKey() {
    return JSON.stringify({
      mode: this._projectFormMode,
      editingProjectId: this._editingProjectId,
      browsePath: this._browseState?.path || "",
      browseParent: this._browseState?.parent_path || "",
      browseDirectories: (this._browseState?.directories || []).map((entry) => [entry.name, entry.path]),
      model: this._projectForm.defaultModel,
      thinking: this._projectForm.defaultThinkingLevel,
      models: this._modelRecords()
    });
  }
  _renderProjectForm() {
    const panel = this.shadowRoot.getElementById("project-form-panel");
    panel.classList.toggle("visible", this._showProjectForm);
    if (!this._showProjectForm) {
      panel.replaceChildren();
      this._renderedProjectFormKey = "";
      return;
    }
    const isEditMode = this._projectFormMode === "edit";
    const formKey = this._projectFormRenderKey();
    if (formKey === this._renderedProjectFormKey && panel.childElementCount) {
      return;
    }
    panel.replaceChildren();
    const titleBlock = document.createElement("div");
    titleBlock.className = "title-block";
    titleBlock.append(
      this._textElement("span", "eyeline", isEditMode ? "Edit project" : "New project"),
      this._textElement("span", "title", isEditMode ? "Project settings" : "Create project")
    );
    const nameInput = this._input("field", "project-name-input", "Project name", this._projectForm.name, "Project name");
    panel.append(titleBlock, nameInput);
    if (isEditMode) {
      const rootInput = this._input(
        "field",
        "project-root-input",
        "team/project",
        this._projectForm.rootPath,
        "Workspace path"
      );
      const fieldGrid = document.createElement("div");
      fieldGrid.className = "field-grid";
      const modelSelect = this._select("field-select stable-select", "project-model-select", "Default model");
      this._appendModelOptions(modelSelect, this._projectForm.defaultModel);
      const thinkingSelect = this._select(
        "field-select stable-select",
        "project-thinking-select",
        "Default thinking level"
      );
      this._appendThinkingOptions(thinkingSelect, this._projectForm.defaultThinkingLevel, this._projectForm.defaultModel);
      fieldGrid.append(modelSelect, thinkingSelect);
      const browserCard = document.createElement("div");
      browserCard.className = "browser-card";
      const browseActions = document.createElement("div");
      browseActions.className = "browser-actions";
      for (const [action, label] of [["browse-current", "Browse"], ["browse-up", "Up"], ["browse-roots", "Workspace root"]]) {
        const button = this._actionButton("text-button", action);
        button.textContent = label;
        browseActions.append(button);
      }
      const browseList = document.createElement("div");
      browseList.className = "browse-list";
      const directories = this._browseState?.directories || [];
      if (directories.length) {
        for (const entry of directories) {
          const entryButton = this._actionButton("browse-row", "browse-entry");
          entryButton.dataset.path = String(entry.path || "");
          entryButton.textContent = entry.name || "";
          browseList.append(entryButton);
        }
      } else {
        browseList.append(this._textElement("div", "empty-note", "Browse a path to list folders."));
      }
      const folderActions = document.createElement("div");
      folderActions.className = "browser-actions";
      const folderInput = this._input(
        "field",
        "folder-name-input",
        "New folder name",
        this._folderDraft,
        "New folder name"
      );
      const createFolder = this._actionButton("text-button", "create-folder");
      createFolder.textContent = "Create folder";
      folderActions.append(folderInput, createFolder);
      browserCard.append(
        this._textElement("span", "browser-label", "App workspace browser"),
        this._textElement("div", "meta-line", this._workspaceLabel(this._browseState?.path, "No folder loaded yet")),
        browseActions,
        browseList,
        folderActions
      );
      panel.append(rootInput, fieldGrid, browserCard);
    }
    const formActions = document.createElement("div");
    formActions.className = "form-actions";
    const save = this._actionButton("send-button", "save-project");
    this._setTrustedButtonContent(save, icons.save, isEditMode ? "Update project" : "Create project");
    const close = this._actionButton("text-button", "cancel-project-form");
    close.textContent = "Close";
    formActions.append(save, close);
    panel.append(formActions);
    this._renderedProjectFormKey = formKey;
  }
  _renderThreadForm() {
    const panel = this.shadowRoot.getElementById("thread-form-panel");
    panel.classList.toggle("visible", this._showThreadForm);
    if (!this._showThreadForm) {
      panel.replaceChildren();
      this._renderedThreadFormKey = "";
      return;
    }
    const targetProject = this._threadForm.projectId ? this._projects.find((project) => project.project_id === this._threadForm.projectId) || null : this._directProject();
    const isDirect = !this._threadForm.projectId || targetProject?.kind === "direct";
    const formKey = JSON.stringify({
      projectId: this._threadForm.projectId || "",
      targetProjectId: targetProject?.project_id || "",
      targetProjectName: targetProject?.name || "",
      targetProjectPath: targetProject?.root_path || "",
      isDirect
    });
    if (formKey === this._renderedThreadFormKey && panel.childElementCount) {
      return;
    }
    panel.replaceChildren();
    const titleBlock = document.createElement("div");
    titleBlock.className = "title-block";
    titleBlock.append(
      this._textElement("span", "eyeline", isDirect ? "New direct chat" : "New project chat"),
      this._textElement("span", "title", targetProject?.name || "Choose a target")
    );
    const titleInput = this._input("field", "thread-title-input", "Chat title", this._threadForm.title, "Chat title");
    const modeSelect = this._select("field-select stable-select", "thread-mode-select", "Chat permission mode");
    modeSelect.setAttribute("aria-describedby", "thread-mode-description");
    for (const option of MODE_OPTIONS) {
      this._appendOption(modeSelect, option.value, option.label, option.value === this._threadForm.mode);
    }
    const modeDescription = document.createElement("ul");
    modeDescription.id = "thread-mode-description";
    modeDescription.className = "mode-boundaries";
    for (const option of MODE_OPTIONS) {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = `${option.label}: `;
      item.append(label, document.createTextNode(option.description));
      modeDescription.append(item);
    }
    const formActions = document.createElement("div");
    formActions.className = "form-actions";
    const save = this._actionButton("send-button", "save-thread");
    this._setTrustedButtonContent(save, icons.chat, "Create chat");
    const close = this._actionButton("text-button", "cancel-thread-form");
    close.textContent = "Close";
    formActions.append(save, close);
    panel.append(
      titleBlock,
      titleInput,
      modeSelect,
      modeDescription,
      this._textElement(
        "div",
        "meta-line",
        this._workspaceLabel(targetProject?.root_path, "Pick a project or direct chat context first.")
      ),
      formActions
    );
    this._renderedThreadFormKey = formKey;
  }
  _isRefreshLockTarget(target) {
    if (!(target instanceof HTMLElement)) {
      return false;
    }
    if (target.closest("[data-interaction-id]") && ["BUTTON", "INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
      return true;
    }
    if (target.classList.contains("stable-select")) {
      return true;
    }
    if (!["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
      return false;
    }
    return Boolean(target.closest("#thread-form-panel, #project-form-panel"));
  }
  _clipboardFiles(clipboardData) {
    if (!clipboardData?.items?.length) {
      return [];
    }
    const files = [];
    for (const item of Array.from(clipboardData.items)) {
      if (item.kind !== "file") {
        continue;
      }
      const rawFile = item.getAsFile();
      if (!rawFile) {
        continue;
      }
      files.push(this._normalizeClipboardFile(rawFile, files.length));
    }
    return files;
  }
  _normalizeClipboardFile(file, index = 0) {
    if (file.name) {
      return file;
    }
    const extension = this._extensionFromMime(file.type);
    const stamp = (/* @__PURE__ */ new Date()).toISOString().replace(/[:.]/g, "-");
    const filename = `clipboard-${stamp}${index ? `-${index + 1}` : ""}.${extension}`;
    return new File([file], filename, {
      type: file.type || "application/octet-stream",
      lastModified: Date.now()
    });
  }
  _extensionFromMime(mimeType) {
    const map = {
      "image/png": "png",
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/gif": "gif",
      "image/bmp": "bmp"
    };
    return map[mimeType] || "bin";
  }
  _renderDirectSection() {
    const section = this.shadowRoot.getElementById("direct-section");
    const directThreads = this._directThreads(false);
    const collapsed = Boolean(this._collapsedSections.direct);
    const directProject = this._directProject();
    const hasMatches = directThreads.length || !this._searchQuery.trim();
    if (!directProject && !hasMatches) {
      section.replaceChildren();
      return;
    }
    section.replaceChildren();
    const sectionHead = document.createElement("div");
    sectionHead.className = `section-head${collapsed ? " compact" : ""}`;
    const toggle = this._actionButton("section-head-button", "toggle-section", "Toggle direct chats");
    toggle.dataset.section = "direct";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-controls", "direct-chat-list");
    toggle.append(this._sectionTitleLine(collapsed ? icons.chevronRight : icons.chevronDown, icons.chat, "Direct chats"));
    const actions = document.createElement("div");
    actions.className = "project-actions";
    const newChat = this._actionButton("icon-button small", "new-direct-chat", "New direct chat");
    this._setTrustedButtonContent(newChat, icons.plus);
    actions.append(newChat);
    sectionHead.append(toggle, actions);
    section.append(sectionHead);
    const chatList = document.createElement("div");
    chatList.id = "direct-chat-list";
    chatList.className = "chat-list";
    chatList.hidden = collapsed;
    if (directThreads.length) {
      for (const thread of directThreads) {
        chatList.append(this._threadRow(thread));
      }
    } else {
      chatList.append(this._textElement("div", "empty-note", "No direct chats yet."));
    }
    section.append(chatList);
  }
  _renderProjectList() {
    const section = this.shadowRoot.getElementById("project-section");
    const projects = this._projects.filter((project) => project.kind !== "direct" && !project.archived_at);
    const visibleProjects = projects.filter((project) => this._projectIsVisible(project));
    section.replaceChildren();
    const sectionHead = document.createElement("div");
    sectionHead.className = "section-head compact";
    sectionHead.append(this._sectionTitleLine(null, icons.folder, "Projects"));
    section.append(sectionHead);
    if (!projects.length) {
      section.append(this._emptyStateNode("No projects yet", "Create a project to provision a private App workspace."));
      return;
    }
    const projectList = document.createElement("div");
    projectList.className = "project-list";
    if (visibleProjects.length) {
      for (const project of visibleProjects) {
        projectList.append(this._projectSection(project));
      }
    } else {
      projectList.append(this._emptyStateNode("No matches", "Try a broader search."));
    }
    section.append(projectList);
  }
  _projectSection(project, { archived = false, includeArchivedThreads = false } = {}) {
    const threads = this._projectThreads(project.project_id, includeArchivedThreads);
    const collapsed = Boolean(this._collapsedProjects[project.project_id]);
    const active = this._selectedProjectId === project.project_id || this._activeThread?.project_id === project.project_id;
    const chatCount = threads.length === 1 ? "1 chat" : `${threads.length} chats`;
    const shell = document.createElement("section");
    shell.className = `project-shell${archived ? " archived" : ""}`;
    const projectHead = document.createElement("div");
    projectHead.className = `project-head${active ? " active" : ""}`;
    const projectButton = this._actionButton("project-button", "select-project", `Select ${project.name || "project"}`);
    projectButton.dataset.projectId = String(project.project_id || "");
    const projectMeta = document.createElement("div");
    projectMeta.className = "project-meta";
    const titleLine = document.createElement("div");
    titleLine.className = "section-title-line";
    const projectName = this._textElement("span", "project-name", project.name || "");
    this._appendTrustedIcon(titleLine, icons.folder);
    titleLine.append(projectName);
    projectMeta.append(titleLine, this._textElement("span", "row-meta", chatCount));
    projectButton.append(projectMeta);
    const projectActions = document.createElement("div");
    projectActions.className = "project-actions";
    const chatListId = this._projectChatListId(project.project_id);
    const collapse = this._actionButton(
      "icon-button small project-collapse-button",
      "toggle-project-collapse",
      `${collapsed ? "Expand" : "Collapse"} ${project.name || "project"}`
    );
    collapse.dataset.projectId = String(project.project_id || "");
    collapse.setAttribute("aria-expanded", String(!collapsed));
    collapse.setAttribute("aria-controls", chatListId);
    this._setTrustedButtonContent(collapse, collapsed ? icons.chevronRight : icons.chevronDown);
    projectActions.append(collapse);
    if (project.kind === "project") {
      const actions = archived ? [["restore-project", "Restore project", icons.restore], ["delete-project", "Delete project", icons.trash]] : [
        ["new-chat", "New chat", icons.plus],
        ...this._isLegacyConnection() ? [] : [["edit-project", "Edit project", icons.edit]],
        ["archive-project", "Archive project", icons.archive],
        ["delete-project", "Delete project", icons.trash]
      ];
      for (const [action, label, icon] of actions) {
        const button = this._actionButton("icon-button small", action, label);
        button.dataset.projectId = String(project.project_id || "");
        this._setTrustedButtonContent(button, icon);
        projectActions.append(button);
      }
    }
    projectHead.append(projectButton, projectActions);
    shell.append(projectHead);
    const chatList = document.createElement("div");
    chatList.id = chatListId;
    chatList.className = "chat-list";
    chatList.hidden = collapsed;
    if (threads.length) {
      for (const thread of threads) {
        chatList.append(this._threadRow(thread, { archived: Boolean(thread.archived_at) }));
      }
    } else {
      chatList.append(this._textElement("div", "empty-note", "No chats yet."));
    }
    shell.append(chatList);
    return shell;
  }
  _renderArchivedSection() {
    const section = this.shadowRoot.getElementById("archived-section");
    const archivedProjects = this._projects.filter((project) => Boolean(project.archived_at) && this._projectMatchesQuery(project));
    const archivedProjectIds = new Set(archivedProjects.map((project) => project.project_id));
    const archivedThreads = this._threads.filter(
      (thread) => Boolean(thread.archived_at) && !archivedProjectIds.has(thread.project_id) && this._threadMatchesQuery(thread)
    );
    const collapsed = Boolean(this._collapsedSections.archived);
    if (!archivedProjects.length && !archivedThreads.length) {
      section.replaceChildren();
      return;
    }
    section.replaceChildren();
    const sectionHead = document.createElement("div");
    sectionHead.className = `section-head${collapsed ? " compact" : ""}`;
    const toggle = this._actionButton("section-head-button", "toggle-section", "Toggle archived chats");
    toggle.dataset.section = "archived";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-controls", "archived-chat-list");
    toggle.append(this._sectionTitleLine(collapsed ? icons.chevronRight : icons.chevronDown, icons.archive, "Archived"));
    sectionHead.append(toggle);
    section.append(sectionHead);
    const chatList = document.createElement("div");
    chatList.id = "archived-chat-list";
    chatList.className = "chat-list";
    chatList.hidden = collapsed;
    for (const project of archivedProjects) {
      chatList.append(this._projectSection(project, { archived: true, includeArchivedThreads: true }));
    }
    for (const thread of archivedThreads) {
      chatList.append(this._threadRow(thread, { archived: true }));
    }
    section.append(chatList);
  }
  _threadRow(thread, { archived = false } = {}) {
    const statusClass = thread.status === "running" ? "running" : thread.status === "error" ? "error" : "idle";
    const meta = `${thread.effective_model} / ${thread.effective_thinking_level}`;
    const timestamp = this._timeAgo(thread.updated_at || thread.created_at);
    const selected = thread.thread_id === this._selectedThreadId;
    const row = document.createElement("div");
    row.className = `chat-row${selected ? " selected" : ""}${archived ? " archived" : ""}`;
    const select = this._actionButton(`chat-select${selected ? " active" : ""}`, "select-thread");
    select.dataset.threadId = String(thread.thread_id || "");
    if (selected) {
      select.setAttribute("aria-current", "page");
    }
    const titleBlock = document.createElement("div");
    titleBlock.className = "title-block";
    titleBlock.append(
      this._textElement("span", "thread-name", thread.title || ""),
      this._textElement("span", "row-meta", meta)
    );
    select.append(titleBlock, this._textElement("span", "timestamp", timestamp));
    const rowActions = document.createElement("div");
    rowActions.className = "row-actions";
    const status = document.createElement("span");
    status.className = `status-pill ${statusClass}`;
    status.title = `Status: ${thread.status || ""}`;
    status.setAttribute("role", "img");
    status.setAttribute("aria-label", `Status ${thread.status || ""}`);
    const threadActions = document.createElement("div");
    threadActions.className = "thread-actions";
    const archiveAction = archived ? "restore-thread" : "archive-thread";
    const archiveLabel = archived ? "Restore chat" : "Archive chat";
    const archiveIcon = archived ? icons.restore : icons.archive;
    const archiveButton = this._actionButton("action-button small", archiveAction, archiveLabel);
    archiveButton.dataset.threadId = String(thread.thread_id || "");
    this._setTrustedButtonContent(archiveButton, archiveIcon);
    const deleteButton = this._actionButton("action-button small", "delete-thread", "Delete chat");
    deleteButton.dataset.threadId = String(thread.thread_id || "");
    this._setTrustedButtonContent(deleteButton, icons.trash);
    threadActions.append(archiveButton, deleteButton);
    rowActions.append(status, threadActions);
    row.append(select, rowActions);
    return row;
  }
  _renderToolbar() {
    const container = this.shadowRoot.getElementById("compact-toolbar");
    const focused = this.shadowRoot.activeElement;
    if (focused instanceof HTMLElement && container.contains(focused) && focused.tagName === "SELECT") {
      return;
    }
    const thread = this._activeThread;
    const project = this._activeProject();
    const modelRecords = this._modelRecords();
    const effectiveModel = thread?.model_override || thread?.effective_model || project?.default_model || this._defaultModel();
    const thinkingLevels = this._thinkingLevelsForModel(effectiveModel, thread?.thinking_override || null);
    const limits = this._status?.limits;
    const toolbarKey = JSON.stringify({
      threadId: thread?.thread_id || null,
      projectId: project?.project_id || null,
      modelOverride: thread?.model_override || null,
      thinkingOverride: thread?.thinking_override || null,
      effectiveModel: thread?.effective_model || null,
      effectiveThinking: thread?.effective_thinking_level || null,
      limits,
      modelRecords,
      thinkingLevels
    });
    if (toolbarKey === this._renderedToolbarKey) {
      return;
    }
    const modelValue = thread?.model_override || "";
    const thinkingValue = thread?.thinking_override || "";
    container.replaceChildren();
    const limitsCard = document.createElement("div");
    limitsCard.className = "toolbar-card limits";
    const limitPair = document.createElement("div");
    limitPair.className = "limit-pair";
    const shortWindowEmptyLabel = limits?.available && !limits?.primary && limits?.secondary ? "Off" : "Unavailable";
    limitPair.append(
      this._compactLimitCard("5h", limits?.primary, shortWindowEmptyLabel),
      this._compactLimitCard("Week", limits?.secondary)
    );
    limitsCard.append(
      this._textElement("span", "setting-label", "Limits"),
      limitPair,
      this._textElement("span", "setting-foot", this._limitsFootnote(limits))
    );
    const controlsCard = document.createElement("div");
    controlsCard.className = "toolbar-card controls";
    controlsCard.append(
      this._toolbarControl("Model", thread, () => {
        const select = this._select("compact-select stable-select", "thread-model-select", "Chat model override");
        this._appendOption(select, "", project?.default_model ? `Inherit (${project.default_model})` : "Inherit default", !modelValue);
        this._appendModelOptions(select, modelValue);
        return [select, `Effective ${thread.effective_model || project?.default_model || this._defaultModel()}`];
      }),
      this._toolbarControl("Thinking", thread, () => {
        const select = this._select(
          "compact-select stable-select",
          "thread-thinking-select",
          "Chat thinking level override"
        );
        this._populateThreadThinkingSelect(
          select,
          thinkingValue || null,
          effectiveModel,
          project?.default_thinking_level || null,
          thinkingLevels
        );
        return [select, `Effective ${thread.effective_thinking_level || project?.default_thinking_level || "medium"}`];
      })
    );
    container.append(limitsCard, controlsCard);
    this._renderedToolbarKey = toolbarKey;
  }
  _compactLimitCard(label, windowInfo, emptyLabel = "Unavailable") {
    const card = document.createElement("div");
    card.className = "mini-limit";
    const head = document.createElement("div");
    head.className = "mini-limit-head";
    const fill = document.createElement("span");
    fill.className = "mini-limit-fill";
    const bar = document.createElement("span");
    bar.className = "mini-limit-bar";
    bar.append(fill);
    if (!windowInfo) {
      fill.style.setProperty("--limit-width", "0%");
      fill.style.setProperty("--limit-color", "var(--muted-color)");
      head.append(this._textElement("span", "mini-limit-name", label), this._textElement("span", "limit-value", "--"));
      card.append(head, bar, this._textElement("span", "limit-subline", emptyLabel));
      return card;
    }
    const remaining = typeof windowInfo.remaining_percent === "number" ? Math.max(0, Math.min(100, windowInfo.remaining_percent)) : 0;
    const limitColor = remaining <= 15 ? "var(--danger-color)" : remaining <= 35 ? "var(--brand-amber)" : "var(--brand-emerald)";
    fill.style.setProperty("--limit-width", `${remaining.toFixed(0)}%`);
    fill.style.setProperty("--limit-color", limitColor);
    head.append(
      this._textElement("span", "mini-limit-name", label),
      this._textElement("span", "limit-value", this._formatPercent(windowInfo.remaining_percent))
    );
    card.append(head, bar, this._textElement("span", "limit-subline", this._formatReset(windowInfo.resets_at)));
    return card;
  }
  _limitsFootnote(limits) {
    if (!limits) {
      return "No limit snapshot yet.";
    }
    if (limits.blocked && limits.message) {
      return limits.message;
    }
    const normalizedPlan = normalizePlanType(limits.plan_type);
    const plan = normalizedPlan === "Unknown" ? "Codex" : normalizedPlan;
    const updated = limits.updated_at ? this._timeAgo(limits.updated_at) : "now";
    return `${plan} usage snapshot ${updated}`;
  }
  _renderStatusBanner() {
    const banner = this.shadowRoot.getElementById("status-banner");
    const state = this._statusBannerState();
    if (!state || state.key === this._dismissedBannerKey) {
      banner.className = "status-banner";
      banner.replaceChildren();
      return;
    }
    banner.className = `status-banner visible ${state.tone}`;
    const actions = state.actions || [];
    banner.replaceChildren();
    const content = document.createElement("div");
    content.className = "banner-content";
    content.append(this._textElement("span", "banner-message", state.message));
    if (actions.length) {
      const actionContainer = document.createElement("div");
      actionContainer.className = "banner-actions";
      for (const action of actions) {
        const button = this._actionButton(`banner-action${action.primary ? " primary" : ""}`, action.action);
        button.textContent = action.label;
        if (this._authActionPending && AUTH_ACTION_IDS.has(action.action)) {
          button.disabled = true;
        }
        actionContainer.append(button);
      }
      content.append(actionContainer);
    }
    const dismiss = this._actionButton("banner-dismiss", "dismiss-banner", "Dismiss");
    dismiss.textContent = "x";
    banner.append(content, dismiss);
  }
  _statusBannerState() {
    const auth = this._status?.auth;
    if (auth?.auth_required || ["expired", "login_failed", "login_running", "login_starting", "unsupported"].includes(auth?.state)) {
      if (this._isLegacyConnection()) {
        return {
          key: "legacy:account-actions",
          tone: "error",
          message: "Move this connection to the private Home Assistant App to manage ChatGPT sign-in.",
          actions: []
        };
      }
      const message = this._authBannerMessage(auth);
      const actions = this._authViewModel().actions.map((action) => ({
        action: action.id,
        label: action.label,
        primary: action.primary
      }));
      return {
        key: `auth:${auth?.state || "unknown"}:${message}:${auth?.user_code || ""}`,
        tone: "error",
        message,
        actions
      };
    }
    const limits = this._status?.limits;
    if (limits?.blocked) {
      const message = "Codex usage is currently unavailable for this ChatGPT account.";
      return {
        key: `limits:${message}`,
        tone: "error",
        message
      };
    }
    if (this._activeThread?.last_error) {
      if (this._isResolvedAuthError(this._activeThread.last_error)) {
        return null;
      }
      return {
        key: `thread:${this._selectedThreadId}:${this._activeThread.last_error}`,
        tone: "error",
        message: "The latest Codex run did not complete. Refresh the chat or try again."
      };
    }
    const diagnosticsError = this._status?.diagnostics?.last_error;
    if (diagnosticsError && !this._activeThread) {
      if (this._isResolvedAuthError(diagnosticsError)) {
        return null;
      }
      return {
        key: `diagnostics:${diagnosticsError}`,
        tone: "error",
        message: "The Codex service needs attention. Check the App status in Home Assistant and retry."
      };
    }
    return null;
  }
  _authBannerMessage(auth) {
    const state = auth?.state;
    if (["login_starting", "login_running"].includes(state)) {
      return "ChatGPT device sign-in is waiting for the one-time code to be approved. You can finish on your phone.";
    }
    if (state === "unsupported") {
      return "Only ChatGPT account sign-in is supported. Sign out, then connect the correct account.";
    }
    if (state === "login_failed") {
      return "ChatGPT device sign-in did not complete. Try again from Home Assistant.";
    }
    if (state === "expired") {
      return "Your ChatGPT sign-in expired. Sign in again to continue.";
    }
    return "Sign in with ChatGPT to use Codex through Home Assistant.";
  }
  _isResolvedAuthError(message) {
    if (!this._authLooksRecovered()) {
      return false;
    }
    const lowered = String(message || "").toLowerCase();
    return lowered.includes("codex login expired") || lowered.includes("401 unauthorized") || lowered.includes("refresh token");
  }
  _authLooksRecovered() {
    const auth = this._status?.auth;
    if (auth?.auth_required) {
      return false;
    }
    if (auth?.state === "ok") {
      return true;
    }
    return Boolean(this._status?.account?.available && this._status?.limits?.available);
  }
  _dismissStatusBanner() {
    const state = this._statusBannerState();
    this._dismissedBannerKey = state?.key || "";
    this._render();
  }
  _renderAttachmentChips() {
    const container = this.shadowRoot.getElementById("attachment-chip-list");
    const attachments = this._activeThread?.attachments || [];
    container.replaceChildren();
    if (!attachments.length) {
      return;
    }
    const visible = attachments.slice(-6);
    for (const attachment of visible) {
      const chip = document.createElement("span");
      chip.className = "attachment-chip";
      chip.append(
        this._textElement("strong", "", attachment?.filename || "File"),
        this._textElement("span", "", attachment?.relative_path || attachment?.mime_type || "")
      );
      container.append(chip);
    }
    if (attachments.length > visible.length) {
      container.append(
        this._textElement("span", "attachment-chip", `+${attachments.length - visible.length} more`)
      );
    }
  }
  _renderMessages() {
    const messageList = this.shadowRoot.getElementById("message-list");
    if (!this._selectedThreadId) {
      this._renderedThreadId = null;
      this._renderedSequence = 0;
      messageList.replaceChildren(this._mainEmptyState());
      return;
    }
    const shouldRebuild = this._forceMessageRebuild || this._renderedThreadId !== this._selectedThreadId;
    if (shouldRebuild) {
      this._renderedThreadId = this._selectedThreadId;
      this._renderedSequence = 0;
      this._forceMessageRebuild = false;
      messageList.replaceChildren();
    }
    const shouldStick = shouldRebuild || messageList.scrollHeight - messageList.clientHeight - messageList.scrollTop < 80;
    const eventsToRender = this._renderedSequence === 0 ? this._events : this._events.filter((item) => item.sequence > this._renderedSequence);
    if (!eventsToRender.length && !messageList.childElementCount) {
      this._renderEmptyState(messageList, "Chat is ready", "Send the first prompt when you are ready.");
      return;
    }
    if (eventsToRender.length && messageList.querySelector(".empty-state")) {
      messageList.replaceChildren();
    }
    for (const event of eventsToRender) {
      const node = this._renderEvent(event);
      if (!node) {
        this._renderedSequence = event.sequence;
        continue;
      }
      messageList.append(node);
      this._renderedSequence = event.sequence;
    }
    if (shouldStick) {
      this._scrollMessagesToBottom();
    }
  }
  _scrollMessagesToBottom() {
    const messageList = this.shadowRoot.getElementById("message-list");
    messageList.scrollTop = messageList.scrollHeight;
    window.requestAnimationFrame(() => {
      messageList.scrollTop = messageList.scrollHeight;
    });
  }
  _renderEvent(event) {
    const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
    if (event.event_type === "message.created") {
      return this._renderMessage(
        "user",
        payload.text,
        event.sequence,
        false,
        payload.queued ? "Queued steer" : ""
      );
    }
    if (event.event_type === "message.completed") {
      return this._renderMessage("assistant", payload.text, event.sequence, true);
    }
    if (event.event_type === "run.started") {
      return this._textElement("div", "event-row", "Run started");
    }
    if (event.event_type === "run.completed") {
      return this._textElement("div", "event-row", "Run completed");
    }
    if (event.event_type === "run.queued") {
      return this._textElement("div", "event-row", "Steer queued");
    }
    if (event.event_type === "run.dequeued") {
      return this._textElement("div", "event-row", "Steer applied");
    }
    if (event.event_type === "run.queue_cleared") {
      return this._textElement("div", "event-row", "Steer queue cleared");
    }
    if (event.event_type === "run.failed") {
      return this._textElement("div", "event-row", `Run failed: ${payload.error || "Unknown error"}`);
    }
    if (event.event_type === "run.cancelled") {
      return this._textElement("div", "event-row", "Run cancelled");
    }
    if (event.event_type === "attachment.added") {
      return this._textElement("div", "event-row", `Uploaded ${payload.relative_path || payload.filename || "file"}`);
    }
    if (event.event_type === "artifact.added") {
      return this._textElement(
        "div",
        "event-row",
        `Artifact ready: ${payload.relative_path || payload.filename || "artifact"}`
      );
    }
    if (event.event_type === "thread.updated") {
      return this._textElement("div", "event-row", "Chat settings updated");
    }
    if (event.event_type === "thread.archived") {
      return this._textElement("div", "event-row", "Chat archived");
    }
    if (event.event_type === "thread.restored") {
      return this._textElement("div", "event-row", "Chat restored");
    }
    return null;
  }
  _renderMessage(role, text, key, canCopy, label = "") {
    const article = document.createElement("article");
    article.className = `message ${role === "user" ? "user" : "assistant"}`;
    article.dataset.sequence = String(key);
    const avatar = this._textElement("span", "avatar", "");
    this._appendTrustedIcon(avatar, role === "user" ? icons.user : icons.bot);
    article.append(avatar);
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (canCopy || label) {
      const head = document.createElement("div");
      head.className = "message-head";
      head.append(this._textElement("span", "row-meta", canCopy ? "Assistant" : label));
      if (canCopy) {
        const copyButton = this._actionButton("copy-button", "copy-message", "Copy response");
        copyButton.dataset.sequence = String(key);
        this._appendTrustedIcon(copyButton, icons.copy);
        copyButton.append(this._textElement("span", "", "Copy"));
        head.append(copyButton);
      }
      bubble.append(head);
    }
    this._renderMessageBody(bubble, String(text ?? ""));
    article.append(bubble);
    return article;
  }
  _renderMessageBody(container, text) {
    const fencePattern = /```([^\n`]*)\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let renderedPart = false;
    let match;
    while ((match = fencePattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        container.append(this._textElement("pre", "bubble-text", text.slice(lastIndex, match.index)));
      }
      const language = (match[1] || "code").trim() || "code";
      const codeBlock = document.createElement("div");
      codeBlock.className = "code-block";
      const codeHead = document.createElement("div");
      codeHead.className = "code-head";
      codeHead.append(this._textElement("span", "", language));
      const copyButton = this._actionButton("copy-button", "copy-code-block", "Copy code");
      this._appendTrustedIcon(copyButton, icons.copy);
      copyButton.append(this._textElement("span", "", "Copy code"));
      codeHead.append(copyButton);
      codeBlock.append(codeHead, this._textElement("pre", "code-text", match[2] || ""));
      container.append(codeBlock);
      renderedPart = true;
      lastIndex = fencePattern.lastIndex;
    }
    if (lastIndex < text.length || !renderedPart) {
      container.append(this._textElement("pre", "bubble-text", text.slice(lastIndex)));
    }
  }
  _renderProgress() {
    const container = this.shadowRoot.getElementById("progress-list");
    const items = this._progressItems();
    container.replaceChildren();
    if (!items.length) {
      container.append(this._textElement("div", "empty-note", "No progress yet."));
      return;
    }
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "progress-row";
      const state = ["active", "complete", "error"].includes(item.state) ? item.state : "active";
      row.setAttribute("role", "listitem");
      row.setAttribute("aria-label", `${item.title}${item.meta ? `: ${item.meta}` : ""}. ${state}.`);
      const dot = this._textElement("span", `progress-dot ${state}`, "");
      dot.setAttribute("aria-hidden", "true");
      row.append(dot);
      const titleBlock = document.createElement("div");
      titleBlock.className = "title-block";
      titleBlock.append(this._textElement("span", "thread-name", item.title));
      if (item.meta) {
        titleBlock.append(this._textElement("span", "row-meta", item.meta));
      }
      row.append(titleBlock);
      container.append(row);
    }
  }
  _progressItems() {
    const items = [];
    if (this._status) {
      items.push({
        title: "Home Assistant connected",
        meta: this._isSupervisorConnection() ? "Private App connection" : "Older connection",
        state: "complete"
      });
    }
    if (this._activeThread) {
      items.push({
        title: this._activeThread.status === "running" ? "Run in progress" : "Chat selected",
        meta: this._activeThread.title,
        state: this._activeThread.status === "running" ? "active" : this._activeThread.status === "error" ? "error" : "complete"
      });
    }
    if (this._pendingUploads) {
      items.push({
        title: "Uploading files",
        meta: this._uploadProgressText(),
        state: "active"
      });
    } else if ((this._activeThread?.attachments || []).length) {
      items.push({
        title: "Attachments available",
        meta: `${this._activeThread.attachments.length} uploaded`,
        state: "complete"
      });
    }
    const notable = this._events.filter(
      (event) => [
        "run.failed",
        "run.completed",
        "run.queued",
        "run.dequeued",
        "run.queue_cleared",
        "artifact.added",
        "thread.updated",
        "thread.archived",
        "thread.restored"
      ].includes(event.event_type)
    ).slice(-4).reverse().map((event) => this._progressItemFromEvent(event));
    return [...items, ...notable].slice(0, 8);
  }
  _progressItemFromEvent(event) {
    const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
    if (event.event_type === "run.failed") {
      return {
        title: "Run failed",
        meta: "Open the chat and retry when ready.",
        state: "error"
      };
    }
    if (event.event_type === "run.completed") {
      return {
        title: "Run completed",
        meta: this._timeAgo(event.timestamp),
        state: "complete"
      };
    }
    if (event.event_type === "run.queued") {
      return {
        title: "Steer queued",
        meta: `${payload.pending_count || 1} pending`,
        state: "active"
      };
    }
    if (event.event_type === "run.dequeued") {
      return {
        title: "Steer applied",
        meta: this._timeAgo(event.timestamp),
        state: "active"
      };
    }
    if (event.event_type === "run.queue_cleared") {
      return {
        title: "Steer queue cleared",
        meta: payload.reason || "Run stopped",
        state: "error"
      };
    }
    if (event.event_type === "artifact.added") {
      return {
        title: "Artifact ready",
        meta: payload.relative_path || payload.filename || "artifact",
        state: "complete"
      };
    }
    if (event.event_type === "thread.updated") {
      return {
        title: "Chat settings updated",
        meta: this._timeAgo(event.timestamp),
        state: "complete"
      };
    }
    if (event.event_type === "thread.archived") {
      return {
        title: "Chat archived",
        meta: this._timeAgo(event.timestamp),
        state: "complete"
      };
    }
    return {
      title: "Chat restored",
      meta: this._timeAgo(event.timestamp),
      state: "complete"
    };
  }
  _renderArtifacts() {
    const container = this.shadowRoot.getElementById("artifact-list");
    this._syncSelectedArtifact();
    container.replaceChildren();
    if (!this._artifacts.length) {
      container.append(this._textElement("div", "empty-note", "No files yet."));
      return;
    }
    for (const artifact of this._artifacts) {
      const active = artifact.artifact_id === this._selectedArtifactId;
      const row = document.createElement("div");
      row.className = `file-row${active ? " active" : ""}`;
      const select = this._actionButton(
        `file-select${active ? " active" : ""}`,
        "select-artifact",
        `Preview ${artifact.filename || "artifact"}`
      );
      select.dataset.artifactId = String(artifact.artifact_id || "");
      const main = document.createElement("div");
      main.className = "file-main";
      const size = artifact.size_bytes ? ` / ${this._formatBytes(artifact.size_bytes)}` : "";
      main.append(
        this._textElement("span", "file-name", artifact.relative_path || artifact.filename || "Artifact"),
        this._textElement("span", "row-meta", `${artifact.mime_type || "application/octet-stream"}${size}`)
      );
      select.append(main);
      const download = this._actionButton(
        "download-button small",
        "download-artifact",
        `Download ${artifact.filename || "artifact"}`
      );
      download.dataset.artifactId = String(artifact.artifact_id || "");
      this._appendTrustedIcon(download, icons.download);
      row.append(select, download);
      container.append(row);
    }
  }
  _renderArtifactPreview() {
    const container = this.shadowRoot.getElementById("artifact-preview");
    container.replaceChildren();
    if (!this._selectedArtifactId) {
      const empty = document.createElement("div");
      empty.className = "preview-empty";
      empty.append(this._textElement("div", "", "Select an artifact to preview it here."));
      container.append(empty);
      return;
    }
    if (!this._artifactPreview || this._artifactPreview.artifactId !== this._selectedArtifactId) {
      const loading = document.createElement("div");
      loading.className = "preview-empty";
      loading.append(this._textElement("div", "", "Loading preview..."));
      container.append(loading);
      return;
    }
    const preview = this._artifactPreview;
    if (preview.kind === "text" || preview.kind === "image") {
      const previewElement = createPreviewElement(document, preview, { blobUrl: preview.url });
      if (previewElement) {
        container.append(previewElement);
        return;
      }
    }
    const binary = document.createElement("div");
    binary.className = "preview-binary";
    binary.append(
      this._textElement("div", "", preview.filename || "Artifact preview unavailable"),
      this._textElement("div", "empty-note", preview.notice || preview.contentType || "Binary file")
    );
    container.append(binary);
  }
  _renderContext() {
    const container = this.shadowRoot.getElementById("context-list");
    const thread = this._activeThread;
    const project = this._activeProject();
    const account = this._status?.account;
    const rows = [
      ["ChatGPT", this._authViewModel().signedIn ? "Connected" : "Not connected"],
      ["Account plan", normalizePlanType(account?.plan_type)],
      ["Workspace", this._workspaceLabel(thread?.workspace_path || project?.root_path)],
      ["Context", project?.kind === "direct" ? "Direct chats" : project?.name || "Not selected"],
      ["Mode", thread?.mode || "full-auto"],
      ["Model", thread?.effective_model || project?.default_model || this._defaultModel()],
      ["Thinking", thread?.effective_thinking_level || project?.default_thinking_level || "medium"],
      ["Uploads", String(thread?.attachments?.length || 0)],
      ["Queued steer", String(thread?.pending_prompts?.length || 0)],
      ["Files", String(this._artifacts.length)]
    ];
    this._renderKeyValueRows(container, rows, "context-row");
  }
  _renderDiagnostics() {
    const container = this.shadowRoot.getElementById("diagnostics-list");
    const model = this._runtimeViewModel();
    const rows = model.items.map((item) => [
      item.label,
      [item.version, item.state === "ready" ? "Ready" : "Attention"].filter(Boolean).join(" · ")
    ]);
    this._renderKeyValueRows(container, rows, "diagnostics-row");
    if (model.notice) {
      container.append(this._textElement("div", "runtime-notice", model.notice));
    }
  }
  async _loadStatus() {
    this._mergeStatus(await this._callWS("get_status"));
  }
  async _loadProjects() {
    this._projects = await this._callWS("list_projects");
    if (this._selectedProjectId && !this._projects.some((project) => project.project_id === this._selectedProjectId)) {
      this._selectedProjectId = null;
    }
    if (!this._selectedProjectId) {
      this._selectedProjectId = this._directProject()?.project_id || this._projects[0]?.project_id || null;
    }
  }
  async _loadThreads({ reportRefreshError = true, preserveThread = null } = {}) {
    const listedThreads = await this._callWS("list_threads", {
      include_archived: true
    });
    this._threads = preserveThread && !listedThreads.some(
      (thread) => thread.thread_id === preserveThread.thread_id
    ) ? [preserveThread, ...listedThreads] : listedThreads;
    if (this._selectedThreadId && !this._threads.some((thread) => thread.thread_id === this._selectedThreadId)) {
      this._setSelectedThreadId(null);
    }
    if (!this._selectedThreadId) {
      const firstActive = this._threads.find((thread) => this._threadIsPrimaryActive(thread));
      this._setSelectedThreadId(firstActive?.thread_id || null);
    }
    if (!this._selectedProjectId && this._threads.length) {
      this._selectedProjectId = this._threads[0].project_id;
    }
    if (this._selectedThreadId) {
      const threadId = this._selectedThreadId;
      const selectionEpoch = this._threadSelectionEpoch;
      await this._refreshSelectedThreadAndStartPolling(threadId, selectionEpoch, {
        reportError: reportRefreshError
      });
    }
  }
  async _refreshActiveThread({
    reportError = true,
    errorSource = null,
    expectedErrorRevision = null
  } = {}) {
    const threadId = this._selectedThreadId;
    const selectionEpoch = this._threadSelectionEpoch;
    const refreshEpoch = ++this._threadSnapshotEpoch;
    const isCurrent = () => refreshEpoch === this._threadSnapshotEpoch && this._threadSelectionIsCurrent(threadId, selectionEpoch);
    if (!threadId) {
      this._stopEventSubscription();
      this._clearInteractionExpiryTimer();
      this._activeThread = null;
      this._resetEventState();
      this._artifacts = [];
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      this._pendingInteractions = [];
      this._interactionMutations.clear();
      this._interactionAnswers.clear();
      this._promptMutation = null;
      this._render();
      return true;
    }
    try {
      const [thread, events, artifacts, status, interactions] = await Promise.all([
        this._callWS("get_thread", { thread_id: threadId }),
        this._callWS("get_events", { thread_id: threadId, after: 0 }),
        this._callWS("list_artifacts", { thread_id: threadId }),
        this._callWS("get_status"),
        this._listPendingInteractions(threadId)
      ]);
      if (!isCurrent()) {
        return false;
      }
      this._activeThread = thread;
      this._selectedProjectId = thread.project_id;
      const replay = acceptEvents(
        createEventStreamState(),
        parseEvents(events).filter((event) => !event.thread_id || event.thread_id === threadId)
      );
      this._eventStream = replay.state;
      this._events = replay.state.events;
      this._sequence = replay.state.cursor;
      this._artifacts = artifacts;
      this._replacePendingInteractions(interactions);
      this._mergeStatus(status);
      this._threadRefreshGraceUntil = 0;
      this._forceMessageRebuild = true;
      this._syncThreadListStatus();
      this._syncSelectedArtifact();
      if (this._promptMutationForThread(threadId)) {
        this._settlePromptMutationFromEvents();
      }
      this._clearError(errorSource === null ? {} : { source: errorSource });
      this._render();
      this._startEventSubscription();
      return true;
    } catch (error) {
      if (isCurrent() && reportError && (expectedErrorRevision === null || this._errorRevision === expectedErrorRevision) && (errorSource === null || this._canSetBackgroundError(errorSource))) {
        this._setError(error, errorSource === null ? {} : { source: errorSource });
      }
      return false;
    }
  }
  async _retryError() {
    if (this._isLoading || !this._errorRetryable) {
      return;
    }
    this._clearError();
    this._render();
    if (!this._config) {
      await this._bootstrap();
      return;
    }
    if (this._selectedThreadId) {
      await this._refreshActiveThread();
      return;
    }
    try {
      await Promise.all([this._loadStatus(), this._loadProjects()]);
      await this._loadThreads();
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  _openProjectFormForCreate() {
    const wasCreateMode = this._projectFormMode === "create";
    const wasVisible = this._showProjectForm;
    this._projectFormMode = "create";
    this._editingProjectId = null;
    this._showProjectForm = !(wasVisible && wasCreateMode);
    this._showThreadForm = false;
    const defaultModel = this._defaultModel();
    this._projectForm = {
      name: "",
      rootPath: "",
      defaultModel,
      defaultThinkingLevel: this._defaultThinkingLevel(defaultModel)
    };
    this._folderDraft = "";
    this._browseState = null;
    this._render();
  }
  _openProjectFormForEdit(projectId) {
    if (this._isLegacyConnection()) {
      return;
    }
    const project = this._projects.find((item) => item.project_id === projectId);
    if (!project || project.kind !== "project") {
      return;
    }
    this._projectFormMode = "edit";
    this._editingProjectId = projectId;
    this._showProjectForm = true;
    this._showThreadForm = false;
    this._projectForm = {
      name: project.name,
      rootPath: this._normalizedWorkspacePath(project.root_path) || "",
      defaultModel: project.default_model,
      defaultThinkingLevel: project.default_thinking_level
    };
    this._folderDraft = "";
    this._browseState = null;
    this._selectedProjectId = projectId;
    this._render();
  }
  _closeProjectForm() {
    this._showProjectForm = false;
    this._folderDraft = "";
    this._browseState = null;
    this._render();
  }
  _openThreadFormForProject(projectId) {
    this._showThreadForm = true;
    this._showProjectForm = false;
    this._threadForm = {
      title: "",
      mode: "full-auto",
      projectId
    };
    this._selectedProjectId = projectId || this._directProject()?.project_id || this._selectedProjectId;
    this._render();
    queueMicrotask(() => this.shadowRoot.getElementById("thread-title-input")?.focus());
  }
  _toggleSection(section) {
    if (!section) {
      return;
    }
    this._collapsedSections[section] = !this._collapsedSections[section];
    this._render();
  }
  _toggleProjectCollapse(projectId) {
    if (!projectId) {
      return;
    }
    this._collapsedProjects[projectId] = !this._collapsedProjects[projectId];
    this._render();
  }
  _selectProject(projectId) {
    this._selectedProjectId = projectId;
    const project = this._projects.find((item) => item.project_id === projectId) || null;
    const visibleThread = this._threads.find(
      (thread) => thread.project_id === projectId && this._threadMatchesQuery(thread) && (project?.archived_at ? true : !thread.archived_at)
    );
    if (visibleThread && visibleThread.thread_id !== this._selectedThreadId) {
      this._selectThread(visibleThread.thread_id);
      return;
    }
    if (!visibleThread) {
      this._setSelectedThreadId(null);
      this._stopEventSubscription();
      this._retireThreadInteractionState(null);
      this._activeThread = null;
      this._resetEventState();
      this._artifacts = [];
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      this._forceMessageRebuild = true;
    }
    this._render();
  }
  async _selectThread(threadId) {
    if (!threadId) {
      return;
    }
    this._stopEventSubscription();
    this._retireThreadInteractionState(threadId);
    const selectionEpoch = this._setSelectedThreadId(threadId, { force: true });
    this._resetEventState();
    this._activeThread = null;
    this._artifacts = [];
    this._selectedArtifactId = null;
    this._revokePreviewUrl();
    this._artifactPreview = null;
    this._forceMessageRebuild = true;
    await this._refreshSelectedThreadAndStartPolling(threadId, selectionEpoch);
  }
  _setSelectedThreadId(threadId, { force = false } = {}) {
    const nextThreadId = typeof threadId === "string" && threadId ? threadId : null;
    if (force || nextThreadId !== this._selectedThreadId) {
      this._stopPolling();
      this._threadSelectionEpoch += 1;
      this._threadSnapshotEpoch += 1;
      this._threadRefreshGraceUntil = 0;
    }
    this._selectedThreadId = nextThreadId;
    return this._threadSelectionEpoch;
  }
  _threadSelectionIsCurrent(threadId, selectionEpoch) {
    return threadId === this._selectedThreadId && selectionEpoch === this._threadSelectionEpoch;
  }
  async _refreshSelectedThreadAndStartPolling(threadId, selectionEpoch, options = {}) {
    const refreshed = await this._refreshActiveThread(options);
    if (this._threadSelectionIsCurrent(threadId, selectionEpoch)) {
      this._startPolling();
    }
    return refreshed;
  }
  _retireThreadInteractionState(nextThreadId) {
    this._clearInteractionExpiryTimer();
    this._pendingInteractions = [];
    this._interactionMutations.clear();
    this._interactionAnswers.clear();
    this._announcedInteractionIds.clear();
    if (this._promptMutation?.threadId) {
      this._promptMutations.set(this._promptMutation.threadId, this._promptMutation);
    }
    this._promptMutation = this._promptMutationForThread(nextThreadId);
    this._draft = this._promptMutation?.state === "retryable" ? this._promptMutation.prompt : this._draftForThread(nextThreadId);
  }
  async _saveProject() {
    try {
      const isEditMode = this._projectFormMode === "edit" && this._editingProjectId;
      if (!this._projectForm.name.trim() || isEditMode && !this._projectForm.rootPath.trim()) {
        return;
      }
      let project;
      if (isEditMode) {
        project = await this._callWS("update_project", {
          project_id: this._editingProjectId,
          name: this._projectForm.name.trim(),
          root_path: this._projectForm.rootPath.trim(),
          default_model: this._projectForm.defaultModel,
          default_thinking_level: this._projectForm.defaultThinkingLevel
        });
      } else {
        project = await this._callWS("create_project", {
          name: this._projectForm.name.trim()
        });
      }
      this._selectedProjectId = project.project_id;
      this._showProjectForm = false;
      this._clearError();
      await this._loadProjects();
      await this._loadThreads();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _browseProjectPath(path) {
    try {
      this._browseState = await this._callWS("browse_paths", { path });
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  _selectBrowseEntry(path) {
    this._projectForm.rootPath = path;
    this._browseProjectPath(path);
  }
  async _createFolder() {
    try {
      const parentPath = this._browseState?.path || this._projectForm.rootPath;
      if (!parentPath || !this._folderDraft.trim()) {
        return;
      }
      const created = await this._callWS("create_folder", {
        parent_path: parentPath,
        folder_name: this._folderDraft.trim()
      });
      this._projectForm.rootPath = created.path;
      this._folderDraft = "";
      await this._browseProjectPath(parentPath);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  async _createThread() {
    try {
      const title = this._threadForm.title.trim();
      if (!title) {
        return;
      }
      const payload = {
        title,
        mode: this._threadForm.mode
      };
      if (this._threadForm.projectId) {
        payload.project_id = this._threadForm.projectId;
      }
      const thread = await this._callWS("create_thread", payload);
      this._threadForm.title = "";
      this._showThreadForm = false;
      this._adoptCreatedThread(thread);
      this._clearError();
      try {
        await this._loadThreads({ reportRefreshError: false, preserveThread: thread });
      } catch {
        this._startEventSubscription();
        this._startPolling();
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  _adoptCreatedThread(thread) {
    this._stopEventSubscription();
    this._setSelectedThreadId(thread.thread_id);
    this._selectedProjectId = thread.project_id;
    this._retireThreadInteractionState(thread.thread_id);
    this._threads = [
      thread,
      ...this._threads.filter((candidate) => candidate.thread_id !== thread.thread_id)
    ];
    this._activeThread = thread;
    this._resetEventState();
    this._artifacts = [];
    this._selectedArtifactId = null;
    this._revokePreviewUrl();
    this._artifactPreview = null;
    this._forceMessageRebuild = true;
    this._threadRefreshGraceUntil = Date.now() + CREATED_THREAD_REFRESH_GRACE_MS;
    this._render();
    this._startEventSubscription();
    this._startPolling();
  }
  async _updateThreadSettings(updates) {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      this._activeThread = await this._callWS("update_thread", {
        thread_id: this._selectedThreadId,
        ...updates
      });
      this._syncThreadListStatus();
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _sendPrompt() {
    const promptInput = this.shadowRoot.getElementById("prompt-input");
    const threadId = this._selectedThreadId;
    const existing = this._promptMutationForThread(threadId);
    if (existing && ["sending", "reconciling"].includes(existing.state)) {
      return;
    }
    const prompt = existing?.state === "retryable" ? existing.prompt : promptInput.value.trim();
    if (!prompt || !threadId) {
      return;
    }
    const mutation = existing || {
      threadId,
      prompt,
      clientRequestId: this._createClientRequestId("prompt"),
      state: "sending"
    };
    mutation.state = "sending";
    this._promptMutations.set(threadId, mutation);
    this._promptMutation = mutation;
    this._draft = prompt;
    this._setDraftForThread(threadId, prompt);
    this._render();
    try {
      await this._callWS("send_prompt", {
        thread_id: threadId,
        prompt,
        client_request_id: mutation.clientRequestId
      });
      if (this._promptMutation === mutation) {
        this._promptMutation = null;
      }
      if (this._promptMutations.get(threadId) === mutation) {
        this._promptMutations.delete(threadId);
      }
      if (threadId === this._selectedThreadId) {
        promptInput.value = "";
        this._draft = "";
        this._setDraftForThread(threadId, "");
        this._clearError();
        await this._refreshActiveThread();
        this._render();
      }
    } catch {
      if (this._promptMutations.get(threadId) !== mutation) {
        return;
      }
      mutation.state = "reconciling";
      if (threadId === this._selectedThreadId) {
        this._render();
      }
      try {
        await this._refreshActiveThread();
      } catch {
      }
      if (this._promptMutations.get(threadId) !== mutation) {
        return;
      }
      if (this._promptEventObserved(mutation.clientRequestId)) {
        this._settlePromptMutation(mutation.clientRequestId);
        return;
      }
      mutation.state = "retryable";
      if (threadId === this._selectedThreadId) {
        this._assignError("The Home Assistant response was interrupted. Retry safely with the same request ID.");
        this._render();
      }
    }
  }
  _promptEventObserved(clientRequestId) {
    return this._events.some(
      (event) => event.event_type === "message.created" && event.payload?.client_request_id === clientRequestId
    );
  }
  _settlePromptMutationFromEvents() {
    const mutation = this._promptMutationForThread(this._selectedThreadId);
    return Boolean(
      mutation && this._promptEventObserved(mutation.clientRequestId) && this._settlePromptMutation(mutation.clientRequestId)
    );
  }
  _settlePromptMutation(clientRequestId) {
    const mutation = this._promptMutationForThread(this._selectedThreadId);
    if (!mutation || mutation.clientRequestId !== clientRequestId) {
      return false;
    }
    if (this._promptMutations.get(mutation.threadId) === mutation) {
      this._promptMutations.delete(mutation.threadId);
    }
    if (this._promptMutation === mutation) {
      this._promptMutation = null;
    }
    if (mutation.threadId === this._selectedThreadId) {
      const promptInput = this.shadowRoot.getElementById("prompt-input");
      promptInput.value = "";
      this._draft = "";
      this._setDraftForThread(mutation.threadId, "");
      this._clearError();
      this._render();
    }
    return true;
  }
  _promptMutationForThread(threadId) {
    if (!threadId) {
      return null;
    }
    return this._promptMutations.get(threadId) || (this._promptMutation?.threadId === threadId ? this._promptMutation : null);
  }
  _draftForThread(threadId) {
    return threadId ? this._drafts.get(threadId) || "" : "";
  }
  _setDraftForThread(threadId, draft) {
    if (!threadId) {
      return;
    }
    if (draft) {
      this._drafts.set(threadId, draft);
    } else {
      this._drafts.delete(threadId);
    }
  }
  async _cancelRun() {
    if (!this._selectedThreadId || this._activeThread?.status !== "running") {
      return;
    }
    try {
      await this._callWS("cancel_run", { thread_id: this._selectedThreadId });
      this._clearError();
      await this._refreshActiveThread();
    } catch (error) {
      this._setError(error);
    }
  }
  async _startAuthLogin() {
    if (this._authActionPending || this._isLegacyConnection()) {
      return;
    }
    this._authActionPending = true;
    this._renderAuthSurface();
    this._renderStatusBanner();
    try {
      const auth = await this._callWS("start_auth_login");
      this._applyAuthStatus(auth);
      this._confirmSignOut = false;
      this._dismissedBannerKey = "";
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    } finally {
      this._authActionPending = false;
      this._render();
    }
  }
  async _cancelAuthLogin() {
    if (this._authActionPending || this._isLegacyConnection()) {
      return;
    }
    this._authActionPending = true;
    this._renderAuthSurface();
    this._renderStatusBanner();
    try {
      const auth = await this._callWS("cancel_auth_login");
      this._applyAuthStatus(auth);
      this._confirmSignOut = false;
      this._dismissedBannerKey = "";
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    } finally {
      this._authActionPending = false;
      this._render();
    }
  }
  async _logoutAuth() {
    if (this._authActionPending || this._isLegacyConnection()) {
      return;
    }
    if (!this._confirmSignOut) {
      this._confirmSignOut = true;
      this._renderAuthSurface();
      return;
    }
    this._authActionPending = true;
    this._renderAuthSurface();
    this._renderStatusBanner();
    try {
      const auth = await this._callWS("logout_auth");
      this._applyAuthStatus(auth);
      this._status = {
        ...this._status || {},
        account: { available: false, auth_mode: null, plan_type: null }
      };
      this._confirmSignOut = false;
      this._dismissedBannerKey = "";
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    } finally {
      this._authActionPending = false;
      this._render();
    }
  }
  _applyAuthStatus(auth) {
    const newest = this._selectNewestAuthStatus(auth, this._status?.auth);
    const next = this._normalizedAuthStatus(newest);
    this._status = {
      ...this._status || {},
      auth: next
    };
    this._syncAuthPolling();
  }
  _normalizedAuthStatus(auth) {
    const next = auth && typeof auth === "object" && !Array.isArray(auth) ? { ...auth } : { state: "unknown" };
    if (!["login_starting", "login_running"].includes(next.state)) {
      next.user_code = null;
      next.verification_uri = null;
      next.login_url = null;
      next.output_tail = [];
    }
    return next;
  }
  _selectNewestAuthStatus(primary, secondary) {
    const first = primary && typeof primary === "object" && !Array.isArray(primary) ? primary : null;
    const second = secondary && typeof secondary === "object" && !Array.isArray(secondary) ? secondary : null;
    if (!first) return second;
    if (!second) return first;
    const firstRevision = Number.isSafeInteger(first.revision) && first.revision >= 0 ? first.revision : -1;
    const secondRevision = Number.isSafeInteger(second.revision) && second.revision >= 0 ? second.revision : -1;
    return secondRevision > firstRevision ? second : first;
  }
  _mergeStatus(status) {
    if (!status || typeof status !== "object" || Array.isArray(status)) {
      return;
    }
    const newestAuth = this._selectNewestAuthStatus(status.auth, this._status?.auth);
    this._status = {
      ...status,
      auth: this._normalizedAuthStatus(newestAuth)
    };
    this._syncAuthPolling();
  }
  async _refreshAuthStatus({ silent = false, pollGeneration = null } = {}) {
    try {
      const auth = await this._callWS("get_auth_status");
      if (pollGeneration !== null && pollGeneration !== this._authPollGeneration) {
        return;
      }
      const status = await this._callWS("get_status");
      if (pollGeneration !== null && pollGeneration !== this._authPollGeneration) {
        return;
      }
      const newestAuth = this._selectNewestAuthStatus(auth, status?.auth);
      this._mergeStatus({
        ...status || this._status || {},
        auth: newestAuth
      });
      if (!this._authViewModel().signedIn) {
        this._confirmSignOut = false;
      }
      if (!silent) {
        this._dismissedBannerKey = "";
        this._clearError();
      }
      this._render();
    } catch (error) {
      if (!silent) {
        this._setError(error);
      }
    }
  }
  _authLoginIsPending() {
    return ["login_starting", "login_running", "login_completing"].includes(
      this._status?.auth?.state
    );
  }
  _syncAuthPolling() {
    if (!this.isConnected || !this._hass || this._isLegacyConnection() || !this._authLoginIsPending()) {
      this._stopAuthPolling();
      return;
    }
    if (this._authPollTimer || this._authPollInFlight) {
      return;
    }
    const generation = this._authPollGeneration;
    this._authPollTimer = window.setTimeout(async () => {
      this._authPollTimer = null;
      if (generation !== this._authPollGeneration || !this.isConnected || !this._authLoginIsPending()) {
        return;
      }
      this._authPollInFlight = true;
      try {
        await this._refreshAuthStatus({ silent: true, pollGeneration: generation });
      } finally {
        if (generation === this._authPollGeneration) {
          this._authPollInFlight = false;
          this._syncAuthPolling();
        }
      }
    }, AUTH_POLL_INTERVAL_MS);
  }
  _stopAuthPolling() {
    this._authPollGeneration += 1;
    if (this._authPollTimer) {
      window.clearTimeout(this._authPollTimer);
      this._authPollTimer = null;
    }
    this._authPollInFlight = false;
  }
  async _copyAuthCode() {
    const code = this._authViewModel().code;
    if (!code) {
      return;
    }
    try {
      await this._writeClipboardText(code);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  _safeAuthVerificationUrl() {
    if (!this._authViewModel().canOpen) {
      return null;
    }
    const candidate = this._status?.auth?.verification_uri || this._status?.auth?.login_url;
    if (typeof candidate !== "string") {
      return null;
    }
    try {
      const url = new URL(candidate);
      if (url.protocol !== "https:" || !AUTH_VERIFICATION_HOSTS.has(url.hostname.toLowerCase()) || !["", "443"].includes(url.port) || !url.pathname || url.pathname === "/" || url.username || url.password || url.search || url.hash) {
        return null;
      }
      return url.href;
    } catch {
      return null;
    }
  }
  _openChatGptSignIn() {
    const url = this._safeAuthVerificationUrl();
    if (!url) {
      this._setError("The ChatGPT sign-in page is unavailable. Copy the code and continue on another device.");
      return;
    }
    const opened = window.open(url, "_blank", "noopener,noreferrer");
    if (opened) {
      opened.opener = null;
    }
  }
  async _retryAppConnection() {
    this._stopSystemEventSubscription();
    this._config = null;
    this._status = null;
    this._dismissedBannerKey = "";
    this._clearError();
    await this._bootstrap();
  }
  async _uploadFiles(files, { useRelativePaths }) {
    if (!this._selectedThreadId || !files.length || this._pendingUploads) {
      return;
    }
    const threadId = this._selectedThreadId;
    const totalBytes = files.reduce((total, file) => total + (file.size || 0), 0);
    if (useRelativePaths && (files.length > 75 || totalBytes > 100 * 1024 * 1024) && !window.confirm(
      `Upload ${files.length} files (${this._formatBytes(totalBytes)}) into this chat? Large VBA/codebase folders can take a while.`
    )) {
      return;
    }
    try {
      const token = this._accessToken();
      const abortController = new AbortController();
      this._uploadAbortController = abortController;
      this._pendingUploads = files.length;
      this._uploadProgress = {
        completed: 0,
        total: files.length,
        current: "",
        currentPercent: 0,
        totalBytes
      };
      this._render();
      for (const file of files) {
        const relativePath = useRelativePaths ? file.webkitRelativePath || file.relativePath || file.name : null;
        this._uploadProgress.current = relativePath || file.name;
        this._uploadProgress.currentPercent = 0;
        await this._uploadSingleFile(file, {
          relativePath,
          token,
          threadId,
          signal: abortController.signal
        });
        this._pendingUploads -= 1;
        this._uploadProgress.completed += 1;
        this._uploadProgress.currentPercent = 100;
        this._render();
      }
      this._clearError();
      await this._refreshActiveThread();
    } catch (error) {
      this._setError(error);
    } finally {
      this._uploadAbortController = null;
      this._pendingUploads = 0;
      this._uploadProgress = null;
      this._render();
    }
  }
  _uploadSingleFile(file, { relativePath, token, threadId, signal }) {
    return uploadResumableFile({
      file,
      threadId,
      relativePath,
      accessToken: token,
      signal,
      onProgress: ({ completedBytes, totalBytes }) => {
        if (!this._uploadProgress) {
          return;
        }
        this._uploadProgress.currentPercent = totalBytes ? Math.min(100, Math.round(completedBytes / totalBytes * 100)) : 100;
        this._render();
      }
    });
  }
  _uploadProgressText() {
    if (!this._uploadProgress) {
      return `Uploading ${this._pendingUploads} file${this._pendingUploads === 1 ? "" : "s"}`;
    }
    const { completed, total, current, currentPercent, totalBytes } = this._uploadProgress;
    const sizeLabel = totalBytes ? ` - ${this._formatBytes(totalBytes)}` : "";
    return `Uploading ${completed + 1}/${total} - ${currentPercent}% - ${current}${sizeLabel}`;
  }
  async _archiveThread(threadId) {
    if (!threadId) {
      return;
    }
    try {
      const archived = await this._callWS("archive_thread", { thread_id: threadId });
      this._threads = this._threads.map((thread) => thread.thread_id === threadId ? archived : thread);
      if (this._selectedThreadId === threadId) {
        const replacement = this._threads.find((thread) => !thread.archived_at && thread.thread_id !== threadId);
        const selectionEpoch = this._setSelectedThreadId(replacement?.thread_id || null);
        if (this._selectedThreadId) {
          await this._refreshSelectedThreadAndStartPolling(this._selectedThreadId, selectionEpoch);
        } else {
          this._stopEventSubscription();
          this._activeThread = null;
          this._resetEventState();
          this._artifacts = [];
          this._selectedArtifactId = null;
          this._revokePreviewUrl();
          this._artifactPreview = null;
          this._forceMessageRebuild = true;
        }
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _archiveProject(projectId) {
    if (!projectId) {
      return;
    }
    try {
      const archived = await this._callWS("archive_project", { project_id: projectId });
      this._projects = this._projects.map((project) => project.project_id === projectId ? archived : project);
      this._clearSelectionForProject(projectId, { preferProjectId: this._directProject()?.project_id || null });
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _restoreProject(projectId) {
    if (!projectId) {
      return;
    }
    try {
      const restored = await this._callWS("restore_project", { project_id: projectId });
      this._projects = this._projects.map((project) => project.project_id === projectId ? restored : project);
      this._selectedProjectId = projectId;
      const replacement = this._threads.find((thread) => thread.project_id === projectId && !thread.archived_at) || null;
      if (replacement) {
        await this._selectThread(replacement.thread_id);
      } else {
        this._render();
      }
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  async _deleteProject(projectId) {
    if (!projectId || !window.confirm("Delete this project and its chat records? Workspace files will be left in place.")) {
      return;
    }
    try {
      await this._callWS("delete_project", { project_id: projectId });
      const removedThreadIds = new Set(
        this._threads.filter((thread) => thread.project_id === projectId).map((thread) => thread.thread_id)
      );
      this._projects = this._projects.filter((project) => project.project_id !== projectId);
      this._threads = this._threads.filter((thread) => thread.project_id !== projectId);
      this._clearSelectionForProject(projectId, {
        preferProjectId: this._directProject()?.project_id || this._projects[0]?.project_id || null
      });
      if (removedThreadIds.has(this._selectedThreadId)) {
        this._setSelectedThreadId(null);
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _restoreThread(threadId) {
    if (!threadId) {
      return;
    }
    try {
      const restored = await this._callWS("restore_thread", { thread_id: threadId });
      this._threads = this._threads.map((thread) => thread.thread_id === threadId ? restored : thread);
      const selectionEpoch = this._setSelectedThreadId(threadId);
      this._selectedProjectId = restored.project_id;
      await this._refreshSelectedThreadAndStartPolling(threadId, selectionEpoch);
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _deleteThread(threadId) {
    if (!threadId || !window.confirm("Delete this chat? Project files will be left in place.")) {
      return;
    }
    try {
      await this._callWS("delete_thread", { thread_id: threadId });
      this._threads = this._threads.filter((thread) => thread.thread_id !== threadId);
      if (this._selectedThreadId === threadId) {
        const replacement = this._threads.find((thread) => !thread.archived_at) || null;
        const selectionEpoch = this._setSelectedThreadId(replacement?.thread_id || null);
        this._selectedProjectId = replacement?.project_id || this._directProject()?.project_id || null;
        if (replacement) {
          await this._refreshSelectedThreadAndStartPolling(replacement.thread_id, selectionEpoch);
        } else {
          this._stopEventSubscription();
          this._activeThread = null;
          this._resetEventState();
          this._artifacts = [];
          this._selectedArtifactId = null;
          this._revokePreviewUrl();
          this._artifactPreview = null;
          this._forceMessageRebuild = true;
        }
      }
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _createWorkspaceArchive() {
    if (!this._selectedThreadId) {
      return;
    }
    try {
      const artifact = await this._callWS("create_workspace_archive", {
        thread_id: this._selectedThreadId
      });
      this._artifacts = await this._callWS("list_artifacts", { thread_id: this._selectedThreadId });
      this._selectedArtifactId = artifact.artifact_id;
      this._artifactPreview = null;
      await this._loadArtifactPreview(artifact.artifact_id);
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  async _selectArtifact(artifactId) {
    if (!artifactId || artifactId === this._selectedArtifactId) {
      return;
    }
    this._selectedArtifactId = artifactId;
    this._artifactPreview = null;
    this._render();
    await this._loadArtifactPreview(artifactId);
  }
  async _loadArtifactPreview(artifactId) {
    if (!this._selectedThreadId || !artifactId) {
      return;
    }
    const artifact = this._artifacts.find((item) => item.artifact_id === artifactId);
    if (!artifact) {
      return;
    }
    const previewToken = ++this._previewToken;
    const advertisedDescriptor = previewDescriptor(artifact, { type: artifact.mime_type });
    if (advertisedDescriptor.kind === "binary") {
      this._revokePreviewUrl();
      this._artifactPreview = advertisedDescriptor;
      this._render();
      return;
    }
    const sizeState = artifactPreviewSizeState(artifact);
    if (sizeState !== "within-limit") {
      this._revokePreviewUrl();
      this._artifactPreview = {
        ...advertisedDescriptor,
        kind: "binary",
        notice: previewUnavailableMessage(sizeState)
      };
      this._render();
      return;
    }
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const threadSegment = encodeURIComponent(this._selectedThreadId);
      const artifactSegment = encodeURIComponent(artifactId);
      const response = await fetch(`/api/codex_bridge/threads/${threadSegment}/artifacts/${artifactSegment}`, {
        headers
      });
      if (!response.ok) {
        throw new Error("Preview failed");
      }
      const blob = await response.blob();
      if (previewToken !== this._previewToken || artifactId !== this._selectedArtifactId) {
        return;
      }
      this._revokePreviewUrl();
      const descriptor = previewDescriptor(artifact, blob);
      if (descriptor.kind === "text") {
        descriptor.text = await blob.text();
      } else if (descriptor.kind === "image") {
        descriptor.url = URL.createObjectURL(blob);
      }
      this._artifactPreview = descriptor;
      this._clearError();
      this._render();
    } catch (error) {
      this._setError(error);
    }
  }
  _revokePreviewUrl() {
    if (this._artifactPreview?.url) {
      URL.revokeObjectURL(this._artifactPreview.url);
    }
  }
  async _downloadArtifact(artifactId) {
    if (!this._selectedThreadId || !artifactId) {
      return;
    }
    try {
      const token = this._accessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const threadSegment = encodeURIComponent(this._selectedThreadId);
      const artifactSegment = encodeURIComponent(artifactId);
      const response = await fetch(`/api/codex_bridge/threads/${threadSegment}/artifacts/${artifactSegment}`, {
        headers
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.message || "Download failed");
      }
      const blob = await response.blob();
      const contentDisposition = response.headers.get("Content-Disposition") || "";
      const filenameMatch = contentDisposition.match(/filename="(.+?)"/);
      const filename = sanitizeFilename(filenameMatch ? filenameMatch[1] : "codex-artifact", "codex-artifact");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  async _copyMessage(sequence) {
    const numericSequence = Number(sequence);
    const event = this._events.find((item) => item.sequence === numericSequence);
    const text = event?.payload?.text || "";
    if (!text) {
      return;
    }
    try {
      await this._writeClipboardText(text);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  async _copyCodeBlock(button) {
    const block = button.closest(".code-block");
    const text = block?.querySelector(".code-text")?.textContent || "";
    if (!text) {
      return;
    }
    try {
      await this._writeClipboardText(text);
      this._clearError();
    } catch (error) {
      this._setError(error);
    }
  }
  async _writeClipboardText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const helper = document.createElement("textarea");
    helper.value = text;
    document.body.appendChild(helper);
    helper.select();
    document.execCommand("copy");
    helper.remove();
  }
  _startPolling() {
    this._stopPolling();
    if (!this._selectedThreadId) {
      return;
    }
    this._pollActive = true;
    this._pollGeneration += 1;
    this._lastStatusRefreshAt = 0;
    this._scheduleNextPoll(250, this._pollGeneration);
  }
  _scheduleNextPoll(delay2 = this._pollDelay(), generation = this._pollGeneration) {
    if (!this._pollActive || !this._selectedThreadId) {
      return;
    }
    this._pollTimer = window.setTimeout(() => {
      if (generation !== this._pollGeneration) {
        return;
      }
      this._pollTimer = null;
      this._runPollTick(generation);
    }, delay2);
  }
  _pollDelay() {
    if (document.visibilityState === "hidden") {
      return 8e3;
    }
    if (Date.now() < this._threadRefreshGraceUntil) {
      return 1e3;
    }
    if (this._activeThread?.status === "running") {
      return 900;
    }
    if (this._error) {
      return 5e3;
    }
    return 3600;
  }
  async _runPollTick(generation = this._pollGeneration) {
    if (!this._pollActive || generation !== this._pollGeneration || !this._selectedThreadId) {
      return;
    }
    if (this._pollInFlight) {
      this._scheduleNextPoll(void 0, generation);
      return;
    }
    this._pollInFlight = true;
    const polledThreadId = this._selectedThreadId;
    const selectionEpoch = this._threadSelectionEpoch;
    const snapshotEpoch = ++this._threadSnapshotEpoch;
    const errorRevision = this._errorRevision;
    const isCurrent = () => this._pollActive && generation === this._pollGeneration && snapshotEpoch === this._threadSnapshotEpoch && this._threadSelectionIsCurrent(polledThreadId, selectionEpoch);
    try {
      this._pollTick += 1;
      const previousSequence = this._sequence;
      const previousStatus = this._activeThread?.status;
      const isRunning = previousStatus === "running";
      const now = Date.now();
      const statusInterval = isRunning ? 7e3 : 3e4;
      const shouldRefreshStatus = !this._lastStatusRefreshAt || now - this._lastStatusRefreshAt >= statusInterval;
      let hasInteractionEvents = false;
      const [events, status, thread] = await Promise.all([
        this._eventSubscriptionActive ? Promise.resolve([]) : this._callWS("get_events", {
          thread_id: polledThreadId,
          after: this._sequence
        }),
        shouldRefreshStatus ? this._callWS("get_status") : Promise.resolve(this._status),
        isRunning || shouldRefreshStatus ? this._callWS("get_thread", { thread_id: polledThreadId }) : Promise.resolve(null)
      ]);
      if (!isCurrent()) {
        return;
      }
      if (shouldRefreshStatus) {
        this._lastStatusRefreshAt = Date.now();
      }
      if (Array.isArray(events) && events.length) {
        const scopedEvents = events.filter(
          (event) => !event?.thread_id || event.thread_id === polledThreadId
        );
        const batch = acceptEvents(this._eventStream, scopedEvents);
        this._eventStream = batch.state;
        this._events = batch.state.events;
        this._sequence = batch.state.cursor;
        hasInteractionEvents = batch.accepted.some((event) => INTERACTION_EVENT_TYPES.has(event.event_type));
        this._settlePromptMutationFromEvents();
        if (batch.controls.includes("snapshot")) {
          this._stopEventSubscription();
          await this._refreshActiveThread({
            errorSource: "poll",
            expectedErrorRevision: errorRevision
          });
          return;
        }
        if (batch.controls.includes("error")) {
          this._stopEventSubscription();
          if (this._errorRevision === errorRevision && this._canSetBackgroundError("poll")) {
            this._setError(batch.state.error || "Bridge event stream failed", { source: "poll" });
          }
          return;
        }
      }
      if (status) {
        this._mergeStatus(status);
      }
      if (hasInteractionEvents) {
        const interactions = await this._listPendingInteractions(polledThreadId);
        if (!isCurrent()) {
          return;
        }
        this._replacePendingInteractions(interactions);
      }
      const hasNewEvents = this._sequence !== previousSequence;
      const shouldRefreshThread = Boolean(thread) || hasNewEvents;
      if (shouldRefreshThread) {
        const refreshedThread = thread || await this._callWS("get_thread", { thread_id: polledThreadId });
        if (!isCurrent()) {
          return;
        }
        this._activeThread = refreshedThread;
        this._syncThreadListStatus();
        if (this._activeThread.status !== "running" && (hasNewEvents || previousStatus === "running" || shouldRefreshStatus)) {
          const artifacts = await this._callWS("list_artifacts", { thread_id: polledThreadId });
          if (!isCurrent()) {
            return;
          }
          this._artifacts = artifacts;
          this._syncSelectedArtifact();
        }
        this._render();
      }
      this._threadRefreshGraceUntil = 0;
      if (this._clearError({ source: "poll" })) {
        this._render();
      }
    } catch (error) {
      if (isCurrent() && this._errorRevision === errorRevision && this._canSetBackgroundError("poll") && Date.now() >= this._threadRefreshGraceUntil) {
        this._setError(error, { source: "poll" });
      }
    } finally {
      if (generation === this._pollGeneration) {
        this._pollInFlight = false;
      }
      if (this._pollActive && generation === this._pollGeneration && this._selectedThreadId) {
        this._scheduleNextPoll(void 0, generation);
      }
    }
  }
  _stopPolling() {
    this._pollActive = false;
    this._pollGeneration += 1;
    if (this._pollTimer) {
      window.clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
    this._pollInFlight = false;
  }
  async _startSystemEventSubscription() {
    if (!this._isSupervisorConnection() || !this._hass?.connection?.subscribeMessage) {
      this._systemEventSubscriptionActive = false;
      return false;
    }
    if (this._systemEventSubscriptionActive) {
      return true;
    }
    if (this._systemEventSubscriptionPending) {
      return this._systemEventSubscriptionPending;
    }
    const generation = ++this._systemEventGeneration;
    const subscribe = async () => {
      try {
        const unsubscribe = await this._hass.connection.subscribeMessage(
          (event) => {
            if (generation === this._systemEventGeneration) {
              this._handleSystemEvent(event);
            }
          },
          {
            type: "codex_bridge/subscribe_events",
            after: this._systemEventCursor,
            scopes: [...SYSTEM_EVENT_SCOPES]
          }
        );
        if (generation !== this._systemEventGeneration) {
          unsubscribe();
          return false;
        }
        this._systemEventUnsubscribe = unsubscribe;
        this._systemEventSubscriptionActive = true;
        this._systemReconnectAttempt = 0;
        if (this._systemReconnectTimer) {
          window.clearTimeout(this._systemReconnectTimer);
          this._systemReconnectTimer = null;
        }
        return true;
      } catch {
        if (generation === this._systemEventGeneration) {
          this._systemEventSubscriptionActive = false;
          this._scheduleSystemReconnect();
        }
        return false;
      } finally {
        if (generation === this._systemEventGeneration) {
          this._systemEventSubscriptionPending = null;
        }
      }
    };
    this._systemEventSubscriptionPending = subscribe();
    return this._systemEventSubscriptionPending;
  }
  _stopSystemEventSubscription() {
    this._systemEventGeneration += 1;
    if (this._systemEventUnsubscribe) {
      this._systemEventUnsubscribe();
      this._systemEventUnsubscribe = null;
    }
    if (this._systemRefreshTimer) {
      window.clearTimeout(this._systemRefreshTimer);
      this._systemRefreshTimer = null;
    }
    if (this._systemReconnectTimer) {
      window.clearTimeout(this._systemReconnectTimer);
      this._systemReconnectTimer = null;
    }
    this._systemEventSubscriptionPending = null;
    this._systemEventSubscriptionActive = false;
    this._systemReconnectAttempt = 0;
  }
  _retireSystemEventSubscription() {
    this._systemEventGeneration += 1;
    if (this._systemEventUnsubscribe) {
      this._systemEventUnsubscribe();
      this._systemEventUnsubscribe = null;
    }
    this._systemEventSubscriptionPending = null;
    this._systemEventSubscriptionActive = false;
    this._scheduleSystemReconnect();
  }
  _scheduleSystemReconnect() {
    if (this._systemReconnectTimer || !this.isConnected || !this._isSupervisorConnection()) {
      return;
    }
    this._systemReconnectAttempt = Math.min(this._systemReconnectAttempt + 1, 7);
    const delay2 = Math.min(3e4, 500 * 2 ** (this._systemReconnectAttempt - 1));
    this._systemReconnectTimer = window.setTimeout(() => {
      this._systemReconnectTimer = null;
      this._startSystemEventSubscription();
    }, delay2);
  }
  _handleSystemEvent(envelope) {
    if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) {
      return;
    }
    if (envelope.type === "event") {
      const event = envelope.event;
      if (!event || typeof event !== "object" || Array.isArray(event)) {
        return;
      }
      const cursor = event.cursor;
      const scope = event.scope;
      const eventType = event.event_type;
      const payload = event.payload;
      if (!Number.isSafeInteger(cursor) || cursor <= this._systemEventCursor || !SYSTEM_EVENT_SCOPES.includes(scope) || typeof eventType !== "string" || !eventType.startsWith(`${scope}.`) || !payload || typeof payload !== "object" || Array.isArray(payload)) {
        return;
      }
      this._systemEventCursor = cursor;
      this._scheduleSystemRefresh();
      return;
    }
    if (envelope.type === "snapshot_required") {
      const cursor = envelope.cursor;
      if (Number.isSafeInteger(cursor) && cursor >= 0) {
        this._systemEventCursor = Math.max(this._systemEventCursor, cursor);
      }
      this._scheduleSystemRefresh();
      return;
    }
    if (envelope.type === "stream_status") {
      this._scheduleSystemRefresh();
      if (["authentication_failed", "failed", "protocol_error", "upstream_error", "stopped"].includes(envelope.state)) {
        this._retireSystemEventSubscription();
      }
      return;
    }
    if (envelope.type === "error") {
      this._scheduleSystemRefresh();
      this._retireSystemEventSubscription();
    }
  }
  _scheduleSystemRefresh() {
    if (this._systemRefreshTimer) {
      return;
    }
    this._systemRefreshTimer = window.setTimeout(() => {
      this._systemRefreshTimer = null;
      this._refreshAuthStatus();
    }, 40);
  }
  _startEventSubscription({ reconnecting = false } = {}) {
    this._stopEventSubscription({ preserveReconnectAttempt: reconnecting });
    if (!this._selectedThreadId || !this._hass?.connection?.subscribeMessage) {
      this._eventSubscriptionActive = false;
      return;
    }
    const threadId = this._selectedThreadId;
    const generation = this._eventSubscriptionGeneration;
    this._eventSubscriptionPending = this._hass.connection.subscribeMessage((event) => {
      if (generation === this._eventSubscriptionGeneration) {
        this._handleSubscribedEvent(threadId, event);
      }
    }, {
      type: "codex_bridge/subscribe_events",
      thread_id: threadId,
      after: this._sequence
    }).then((unsubscribe) => {
      if (generation !== this._eventSubscriptionGeneration || threadId !== this._selectedThreadId) {
        unsubscribe();
        return;
      }
      this._eventUnsubscribe = unsubscribe;
      this._eventSubscriptionActive = true;
      this._eventReconnectAttempt = 0;
      if (this._eventReconnectTimer) {
        window.clearTimeout(this._eventReconnectTimer);
        this._eventReconnectTimer = null;
      }
    }).catch(() => {
      if (generation === this._eventSubscriptionGeneration) {
        this._eventSubscriptionActive = false;
        this._scheduleEventReconnect(threadId);
      }
    }).finally(() => {
      if (generation === this._eventSubscriptionGeneration) {
        this._eventSubscriptionPending = null;
      }
    });
    return this._eventSubscriptionPending;
  }
  _stopEventSubscription({ preserveReconnectAttempt = false } = {}) {
    this._eventSubscriptionGeneration += 1;
    if (this._eventUnsubscribe) {
      this._eventUnsubscribe();
      this._eventUnsubscribe = null;
    }
    if (this._eventRefreshTimer) {
      window.clearTimeout(this._eventRefreshTimer);
      this._eventRefreshTimer = null;
    }
    if (!preserveReconnectAttempt && this._eventReconnectTimer) {
      window.clearTimeout(this._eventReconnectTimer);
      this._eventReconnectTimer = null;
    }
    this._eventSubscriptionPending = null;
    this._eventSubscriptionActive = false;
    if (!preserveReconnectAttempt) {
      this._eventReconnectAttempt = 0;
    }
  }
  _retireEventSubscription({ reconnect = true } = {}) {
    const threadId = this._selectedThreadId;
    this._eventSubscriptionGeneration += 1;
    if (this._eventUnsubscribe) {
      this._eventUnsubscribe();
      this._eventUnsubscribe = null;
    }
    this._eventSubscriptionPending = null;
    this._eventSubscriptionActive = false;
    if (reconnect && threadId) {
      this._scheduleEventReconnect(threadId);
    }
  }
  _scheduleEventReconnect(threadId = this._selectedThreadId) {
    if (this._eventReconnectTimer || !this.isConnected || !threadId || threadId !== this._selectedThreadId || !this._hass?.connection?.subscribeMessage) {
      return;
    }
    this._eventReconnectAttempt = Math.min(this._eventReconnectAttempt + 1, 7);
    const delay2 = Math.min(3e4, 500 * 2 ** (this._eventReconnectAttempt - 1));
    this._eventReconnectTimer = window.setTimeout(() => {
      this._eventReconnectTimer = null;
      if (threadId === this._selectedThreadId) {
        this._startEventSubscription({ reconnecting: true });
      }
    }, delay2);
  }
  _handleSubscribedEvent(threadId, event) {
    if (threadId !== this._selectedThreadId) {
      return;
    }
    if (event?.type === "stream_status") {
      if (["authentication_failed", "failed", "protocol_error", "upstream_error", "stopped"].includes(event.state)) {
        this._retireEventSubscription();
      }
      return;
    }
    if (event?.type === "snapshot_required") {
      this._retireEventSubscription({ reconnect: false });
      this._refreshActiveThread();
      return;
    }
    if (event?.type === "error") {
      this._retireEventSubscription();
      if (this._canSetBackgroundError("poll")) {
        this._setError("Bridge event stream failed", { source: "poll" });
      }
      return;
    }
    if (event?.thread_id && event.thread_id !== threadId) {
      return;
    }
    const result = acceptEvent(this._eventStream, event);
    if (!result.accepted) {
      return;
    }
    this._eventStream = result.state;
    this._sequence = result.state.cursor;
    if (result.control === "snapshot") {
      this._stopEventSubscription();
      this._refreshActiveThread();
      return;
    }
    if (result.control === "error") {
      this._retireEventSubscription();
      if (this._canSetBackgroundError("poll")) {
        this._setError(result.state.error || "Bridge event stream failed", { source: "poll" });
      }
      return;
    }
    const acceptedEvent = result.event;
    this._events = result.state.events;
    if (acceptedEvent.event_type === "message.created" && typeof acceptedEvent.payload?.client_request_id === "string") {
      this._settlePromptMutation(acceptedEvent.payload.client_request_id);
    }
    this._renderMessages();
    if ([
      "run.started",
      "run.completed",
      "run.failed",
      "run.cancelled",
      "run.queued",
      "run.dequeued",
      "run.queue_cleared",
      "artifact.added",
      "session.bound"
    ].includes(acceptedEvent.event_type) || INTERACTION_EVENT_TYPES.has(acceptedEvent.event_type)) {
      this._scheduleLiveRefresh(threadId);
    }
  }
  _resetEventState(cursor = 0) {
    this._eventStream = createEventStreamState({ cursor });
    this._events = [];
    this._sequence = this._eventStream.cursor;
  }
  _scheduleLiveRefresh(threadId) {
    if (this._eventRefreshTimer) {
      window.clearTimeout(this._eventRefreshTimer);
    }
    const selectionEpoch = this._threadSelectionEpoch;
    const refreshEpoch = ++this._threadSnapshotEpoch;
    const isCurrent = () => refreshEpoch === this._threadSnapshotEpoch && this._threadSelectionIsCurrent(threadId, selectionEpoch);
    this._eventRefreshTimer = window.setTimeout(async () => {
      this._eventRefreshTimer = null;
      if (!isCurrent()) {
        return;
      }
      try {
        const [thread, artifacts, status, interactions] = await Promise.all([
          this._callWS("get_thread", { thread_id: threadId }),
          this._callWS("list_artifacts", { thread_id: threadId }),
          this._callWS("get_status"),
          this._listPendingInteractions(threadId)
        ]);
        if (!isCurrent()) {
          return;
        }
        this._activeThread = thread;
        this._artifacts = artifacts;
        this._replacePendingInteractions(interactions);
        this._mergeStatus(status);
        this._syncThreadListStatus();
        this._syncSelectedArtifact();
        this._render();
      } catch (error) {
        if (isCurrent()) {
          this._setError(error);
        }
      }
    }, 250);
  }
  _syncThreadListStatus() {
    if (!this._activeThread) {
      return;
    }
    this._threads = this._threads.map(
      (thread) => thread.thread_id === this._activeThread.thread_id ? this._activeThread : thread
    );
  }
  _syncSelectedArtifact() {
    if (!this._artifacts.length) {
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      return;
    }
    const stillExists = this._artifacts.some((artifact) => artifact.artifact_id === this._selectedArtifactId);
    if (stillExists) {
      return;
    }
    const previewCandidate = this._artifacts.find((artifact) => isAutoPreviewCandidate(artifact)) || this._artifacts.find((artifact) => artifactPreviewSizeState(artifact) === "within-limit");
    if (!previewCandidate) {
      this._selectedArtifactId = null;
      this._previewToken += 1;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      return;
    }
    this._selectedArtifactId = previewCandidate.artifact_id;
    this._artifactPreview = null;
    this._loadArtifactPreview(this._selectedArtifactId);
  }
  _activeProject() {
    if (this._activeThread) {
      return this._projects.find((project) => project.project_id === this._activeThread.project_id) || null;
    }
    return this._projects.find((project) => project.project_id === this._selectedProjectId) || null;
  }
  _workspaceLabel(value, fallback = "Not selected") {
    if (typeof value !== "string" || !value.trim()) {
      return fallback;
    }
    if (!this._isSupervisorConnection()) {
      return this._isLegacyConnection() ? "External workspace" : fallback;
    }
    const normalized = this._normalizedWorkspacePath(value);
    if (!normalized) {
      return "Private App workspace";
    }
    if (normalized === ".") {
      return "App workspace root";
    }
    return normalized;
  }
  _normalizedWorkspacePath(value) {
    if (typeof value !== "string" || !value.trim()) {
      return null;
    }
    const normalized = value.trim().replaceAll("\\", "/");
    if (normalized === ".") {
      return normalized;
    }
    const segments = normalized.split("/");
    const hasControlCharacter = [...normalized].some((character) => {
      const codePoint = character.codePointAt(0);
      return codePoint < 32 || codePoint === 127;
    });
    if (hasControlCharacter || normalized.startsWith("/") || /^[A-Za-z]:/u.test(normalized) || normalized.includes("://") || segments.some((segment) => !segment || segment === "." || segment === "..")) {
      return null;
    }
    return normalized;
  }
  _directProject() {
    return this._projects.find((project) => project.kind === "direct") || null;
  }
  _threadIsPrimaryActive(thread) {
    if (thread.archived_at) {
      return false;
    }
    const project = this._projects.find((item) => item.project_id === thread.project_id) || null;
    return !project?.archived_at;
  }
  _directThreads(includeArchived) {
    return this._threads.filter(
      (thread) => thread.project_kind === "direct" && (includeArchived || !thread.archived_at) && this._threadMatchesQuery(thread)
    );
  }
  _projectThreads(projectId, includeArchived) {
    return this._threads.filter(
      (thread) => thread.project_id === projectId && (includeArchived || !thread.archived_at) && this._threadMatchesQuery(thread)
    );
  }
  _projectIsVisible(project) {
    if (project.kind === "direct") {
      return false;
    }
    if (project.archived_at) {
      return false;
    }
    return this._projectMatchesQuery(project);
  }
  _projectMatchesQuery(project) {
    const query = this._searchQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const haystack = `${project.name} ${project.root_path}`.toLowerCase();
    if (haystack.includes(query)) {
      return true;
    }
    return this._threads.some(
      (thread) => thread.project_id === project.project_id && !thread.archived_at && this._threadMatchesQuery(thread)
    );
  }
  _clearSelectionForProject(projectId, { preferProjectId = null } = {}) {
    const selectedThread = this._threads.find((thread) => thread.thread_id === this._selectedThreadId) || null;
    if (selectedThread?.project_id !== projectId && this._selectedProjectId !== projectId) {
      return;
    }
    const replacement = this._threads.find(
      (thread) => this._threadIsPrimaryActive(thread) && (!preferProjectId || thread.project_id === preferProjectId)
    ) || this._threads.find((thread) => this._threadIsPrimaryActive(thread)) || null;
    const selectionEpoch = this._setSelectedThreadId(replacement?.thread_id || null);
    this._selectedProjectId = replacement?.project_id || preferProjectId || this._directProject()?.project_id || null;
    this._retireThreadInteractionState(this._selectedThreadId);
    if (replacement) {
      this._stopEventSubscription();
      this._activeThread = replacement;
      this._resetEventState();
      this._artifacts = [];
      this._selectedArtifactId = null;
      this._revokePreviewUrl();
      this._artifactPreview = null;
      this._forceMessageRebuild = true;
      void this._refreshSelectedThreadAndStartPolling(replacement.thread_id, selectionEpoch);
      return;
    }
    this._stopPolling();
    this._stopEventSubscription();
    this._activeThread = null;
    this._resetEventState();
    this._artifacts = [];
    this._selectedArtifactId = null;
    this._revokePreviewUrl();
    this._artifactPreview = null;
    this._forceMessageRebuild = true;
  }
  _threadMatchesQuery(thread) {
    const query = this._searchQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const haystack = `${thread.title} ${thread.workspace_path} ${thread.effective_model} ${thread.effective_thinking_level}`.toLowerCase();
    return haystack.includes(query);
  }
  _limitState() {
    return this._status?.limits || null;
  }
  _accountLabel(account) {
    if (this._authViewModel().signedIn) {
      const plan = normalizePlanType(account?.plan_type);
      return plan === "Unknown" ? "ChatGPT connected" : `ChatGPT ${plan}`;
    }
    return account?.auth_mode && account.auth_mode !== "chatgpt" ? "Account needs attention" : "ChatGPT not connected";
  }
  _accountTitle(account) {
    if (!this._authViewModel().signedIn) {
      return "Manage ChatGPT sign-in in Home Assistant";
    }
    const plan = normalizePlanType(account?.plan_type);
    return plan === "Unknown" ? "Connected with ChatGPT" : `Connected with the ChatGPT ${plan} plan`;
  }
  _formatPercent(value) {
    if (typeof value !== "number") {
      return "--";
    }
    return `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;
  }
  _formatReset(epochSeconds) {
    if (!epochSeconds) {
      return "unknown";
    }
    try {
      return new Intl.DateTimeFormat(void 0, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit"
      }).format(new Date(epochSeconds * 1e3));
    } catch {
      return "unknown";
    }
  }
  _formatBytes(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "";
    }
    if (value < 1024) {
      return `${value} B`;
    }
    const units = ["KB", "MB", "GB"];
    let size = value / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`;
  }
  _formatDuration(seconds) {
    if (typeof seconds !== "number" || Number.isNaN(seconds)) {
      return "Unknown";
    }
    if (seconds < 60) {
      return `${Math.max(0, Math.round(seconds))}s`;
    }
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return `${minutes}m`;
    }
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return `${hours}h ${remainingMinutes}m`;
  }
  _timeAgo(timestamp) {
    if (!timestamp) {
      return "";
    }
    const value = new Date(timestamp).getTime();
    if (Number.isNaN(value)) {
      return "";
    }
    const deltaMinutes = Math.max(0, Math.round((Date.now() - value) / 6e4));
    if (deltaMinutes < 1) {
      return "now";
    }
    if (deltaMinutes < 60) {
      return `${deltaMinutes}m`;
    }
    const deltaHours = Math.round(deltaMinutes / 60);
    if (deltaHours < 24) {
      return `${deltaHours}h`;
    }
    const deltaDays = Math.round(deltaHours / 24);
    if (deltaDays < 7) {
      return `${deltaDays}d`;
    }
    const deltaWeeks = Math.round(deltaDays / 7);
    if (deltaWeeks < 5) {
      return `${deltaWeeks}w`;
    }
    return new Intl.DateTimeFormat(void 0, { month: "short", day: "numeric" }).format(new Date(value));
  }
  _modelRecords() {
    const catalogModels = this._status?.model_catalog?.models;
    if (Array.isArray(catalogModels) && catalogModels.length) {
      return catalogModels.filter((model) => typeof model?.model === "string" && model.model);
    }
    const legacyModels = Array.isArray(this._status?.models) && this._status.models.length ? this._status.models : ["gpt-5.5"];
    return legacyModels.map((model) => ({ model, display_name: model, catalogued: true }));
  }
  _availableModels() {
    return this._modelRecords().map((record) => record.model);
  }
  _defaultModel() {
    return this._status?.model_catalog?.default_model || this._availableModels()[0] || "gpt-5.5";
  }
  _thinkingLevelsForModel(model, selectedValue = null) {
    const record = this._modelRecords().find((item) => item.model === model);
    const structuredCatalog = this._status?.model_catalog;
    let advertised;
    if (Array.isArray(record?.thinking_levels) && record.thinking_levels.length) {
      advertised = record.thinking_levels;
    } else if (structuredCatalog && Array.isArray(structuredCatalog.models)) {
      advertised = selectedValue ? [selectedValue] : ["medium"];
    } else if (Array.isArray(this._status?.thinking_levels) && this._status.thinking_levels.length) {
      advertised = this._status.thinking_levels;
    } else {
      advertised = ["medium"];
    }
    return selectedValue && !advertised.includes(selectedValue) ? [selectedValue, ...advertised] : advertised;
  }
  _defaultThinkingLevel(model = this._defaultModel()) {
    const record = this._modelRecords().find((item) => item.model === model);
    const supportedLevels = this._thinkingLevelsForModel(model);
    const catalog = this._status?.model_catalog;
    if (model === catalog?.default_model && catalog.default_thinking_level && supportedLevels.includes(catalog.default_thinking_level)) {
      return catalog.default_thinking_level;
    }
    if (record?.default_thinking_level && supportedLevels.includes(record.default_thinking_level)) {
      return record.default_thinking_level;
    }
    if (supportedLevels.includes("medium")) {
      return "medium";
    }
    return supportedLevels[0] || "medium";
  }
  _appendModelOptions(select, selectedValue) {
    const records = [...this._modelRecords()];
    if (selectedValue && !records.some((record) => record.model === selectedValue)) {
      records.unshift({
        model: selectedValue,
        display_name: selectedValue,
        catalogued: false
      });
    }
    for (const record of records) {
      const suffix = record.catalogued === false ? " (configured)" : "";
      this._appendOption(select, record.model, `${record.display_name || record.model}${suffix}`, record.model === selectedValue);
    }
  }
  _appendThinkingOptions(select, selectedValue, model = this._defaultModel()) {
    for (const level of this._thinkingLevelsForModel(model, selectedValue)) {
      this._appendOption(select, level, this._titleCase(level), level === selectedValue);
    }
  }
  _populateThreadThinkingSelect(select, selectedValue, model, inheritedValue = null, levels = this._thinkingLevelsForModel(model, selectedValue)) {
    select.replaceChildren();
    this._appendOption(
      select,
      "",
      inheritedValue ? `Inherit (${inheritedValue})` : "Inherit default",
      !selectedValue
    );
    for (const level of levels) {
      this._appendOption(select, level, this._titleCase(level), level === selectedValue);
    }
  }
  _appendOption(select, value, label, selected = false) {
    const option = document.createElement("option");
    option.value = String(value ?? "");
    option.textContent = String(label ?? "");
    option.selected = selected;
    select.append(option);
  }
  _titleCase(value) {
    return String(value).split("-").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
  }
  _assignError(error, { retryable = null, source = "" } = {}) {
    this._error = this._safeUiError(error);
    this._errorRetryable = retryable === null ? this._isRetryableTransportError(error) : Boolean(retryable);
    this._errorSource = source;
    this._errorRevision += 1;
  }
  _setError(error, options = {}) {
    this._assignError(error, options);
    this._render();
  }
  _canSetBackgroundError(source) {
    return !this._error || this._errorSource === source;
  }
  _isRetryableTransportError(error) {
    if (typeof error?.retryable === "boolean") {
      return error.retryable;
    }
    const candidate = typeof error === "string" ? error : error?.body?.message || error?.message || "";
    const normalized = String(candidate).trim().toLowerCase();
    return [
      "bridge request failed",
      "bridge event stream failed",
      "bridge connection failed",
      "bridge connection lost",
      "network request failed",
      "network error",
      "connection reset",
      "connection refused",
      "connection timed out",
      "request timed out",
      "fetch failed"
    ].some((message) => normalized.includes(message));
  }
  _safeUiError(error) {
    const candidate = typeof error === "string" ? error : error?.body?.message || error?.message || "The Codex request did not complete.";
    const withoutControlCharacters = Array.from(String(candidate), (character) => {
      const code = character.codePointAt(0);
      return code <= 8 || code === 11 || code === 12 || code >= 14 && code <= 31 || code === 127 ? " " : character;
    }).join("");
    const safe = withoutControlCharacters.replace(/https?:\/\/[^\s<>"']+/giu, "[private address]").replace(/(?:[A-Za-z]:\\|\\\\)[^\s<>"']+/gu, "[private path]").replace(/\/(?:data|config|share|addon_configs|home|root|Users)(?:\/[^\s<>"']*)?/gu, "[private path]").replace(/(^|[\s([{:])\/(?!\/)[^\s<>"']+/gu, "$1[private path]").replace(/\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/-]+=*/giu, "[private credential]").replace(/\b(token|api[_ -]?key|password|secret)\s*[:=]\s*[^\s,;]+/giu, "$1=[private credential]").replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/giu, "[private account]").replace(/\s+/gu, " ").trim();
    return (safe || "The Codex request did not complete.").slice(0, 240);
  }
  _clearError({ source = null } = {}) {
    if (source !== null && this._errorSource !== source) {
      return false;
    }
    const changed = Boolean(this._error);
    this._error = "";
    this._errorRetryable = false;
    this._errorSource = "";
    this._errorRevision += 1;
    return changed;
  }
  _textElement(tagName, className, value) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    element.textContent = String(value ?? "");
    return element;
  }
  _actionButton(className, action, accessibleLabel) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.dataset.action = action;
    if (accessibleLabel) {
      button.title = accessibleLabel;
      button.setAttribute("aria-label", accessibleLabel);
    }
    return button;
  }
  _input(className, id, placeholder, value, accessibleLabel = placeholder) {
    const input = document.createElement("input");
    input.className = className;
    input.id = id;
    input.type = "text";
    input.placeholder = placeholder;
    input.value = String(value ?? "");
    input.setAttribute("aria-label", accessibleLabel);
    return input;
  }
  _select(className, id, accessibleLabel) {
    const select = document.createElement("select");
    select.className = className;
    select.id = id;
    if (accessibleLabel) {
      select.setAttribute("aria-label", accessibleLabel);
    }
    return select;
  }
  _setTrustedButtonContent(button, iconMarkup, label = "") {
    button.replaceChildren();
    this._appendTrustedIcon(button, iconMarkup);
    if (label) {
      button.append(this._textElement("span", "", label));
    }
  }
  _sectionTitleLine(chevron, iconMarkup, label) {
    const line = document.createElement("div");
    line.className = "section-title-line";
    if (chevron) {
      this._appendTrustedIcon(line, chevron);
    }
    this._appendTrustedIcon(line, iconMarkup);
    line.append(this._textElement("span", "section-name", label));
    return line;
  }
  _emptyStateNode(title, note) {
    const state = document.createElement("div");
    state.className = "empty-state";
    const body = document.createElement("div");
    body.append(this._textElement("div", "title", title), this._textElement("div", "empty-note", note));
    state.append(body);
    return state;
  }
  _mainEmptyState() {
    const state = document.createElement("div");
    state.className = "empty-state empty-state-main";
    const body = document.createElement("div");
    body.className = "empty-state-body";
    const mark = this._textElement("span", "empty-state-mark", "");
    this._appendTrustedIcon(mark, icons.brand);
    body.append(
      mark,
      this._textElement("div", "empty-state-kicker", "Codex Bridge"),
      this._textElement("div", "title", "Start a new chat"),
      this._textElement("div", "empty-note", "Create a direct chat to work with Codex, or choose a project chat from the workspace rail.")
    );
    const action = this._actionButton("empty-state-cta", "new-direct-chat", "Create a new direct chat");
    this._appendTrustedIcon(action, icons.chat);
    action.append(this._textElement("span", "", "New chat"));
    body.append(action);
    state.append(body);
    return state;
  }
  _projectChatListId(projectId) {
    return `project-chat-list-${encodeURIComponent(String(projectId || "project"))}`;
  }
  _toolbarControl(label, thread, renderControl) {
    const control = document.createElement("div");
    control.className = "mini-control";
    control.append(this._textElement("span", "setting-label", label));
    if (!thread) {
      control.append(this._textElement("span", "setting-foot", "Select a chat."));
      return control;
    }
    const [select, effectiveLabel] = renderControl();
    control.append(select, this._textElement("span", "setting-foot", effectiveLabel));
    return control;
  }
  _appendTrustedIcon(container, iconMarkup) {
    const iconTemplate = document.createElement("template");
    iconTemplate.innerHTML = iconMarkup;
    container.append(iconTemplate.content.cloneNode(true));
  }
  _renderEmptyState(container, title, note) {
    container.replaceChildren(this._emptyStateNode(title, note));
  }
  _renderKeyValueRows(container, rows, rowClassName) {
    const fragment = document.createDocumentFragment();
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      row.className = rowClassName;
      row.append(
        this._textElement("span", "label-text", label),
        this._textElement("span", "row-meta", value)
      );
      fragment.append(row);
    }
    container.replaceChildren(fragment);
  }
};
if (!customElements.get("codex-bridge-panel")) {
  customElements.define("codex-bridge-panel", CodexBridgePanel);
}
