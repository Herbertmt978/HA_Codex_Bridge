/**
 * Deterministic same-origin transport profile harness.
 *
 * `--server` is a deliberately tiny fake HA upstream. It records an accepted
 * prompt before dropping its first response, so the client must retry with the
 * same client_request_id. Its WebSocket endpoint speaks the Home Assistant
 * auth_required/auth/auth_ok and command/result/event protocol, then closes the
 * first stream after an event to exercise reconnect handling.
 * `--client` reaches that upstream only through nginx and proves that relative
 * HTTP/WebSocket paths reconnect without processing the prompt twice.
 */

import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import http from "node:http";

const HTTP_PROMPT_PATH = "/api/prompt";
const HTTP_STATE_PATH = "/debug/state";
const WEBSOCKET_PATH = "/api/websocket";
const MAX_REQUEST_BYTES = 16 * 1024;
const REMOTE_THREAD_ID = "thr_transport";
const UPLOAD_CHUNK_BYTES = Number(process.env.TRANSPORT_UPLOAD_CHUNK_BYTES ?? 8 * 1024 * 1024);
assert.equal(UPLOAD_CHUNK_BYTES, 8 * 1024 * 1024, "transport contract fixes v1 chunks at 8 MiB");
const UPLOAD_BYTES = Buffer.alloc(UPLOAD_CHUNK_BYTES, 0x61);
const UPLOAD_SHA256 = createHash("sha256").update(UPLOAD_BYTES).digest("hex");
const ARTIFACT_BYTES = Buffer.from("resume-through-the-ha-proxy-path");
const ARTIFACT_ETAG = createHash("sha256").update(ARTIFACT_BYTES).digest("hex");
const REMOTE_API_PATH = `/api/codex_bridge/threads/${REMOTE_THREAD_ID}`;
const UPLOADS_PATH = `${REMOTE_API_PATH}/uploads`;
const ARTIFACT_PATH = `${REMOTE_API_PATH}/artifacts/art_transport`;

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_REQUEST_BYTES) {
        reject(new Error("request_too_large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(new Error("request_not_json"));
      }
    });
    request.on("error", reject);
  });
}

function readUploadChunk(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > UPLOAD_CHUNK_BYTES) {
        reject(new Error("chunk_too_large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

function uploadSessionPayload(session) {
  return {
    upload_id: session.upload_id,
    thread_id: REMOTE_THREAD_ID,
    ...session.manifest,
    chunk_size: UPLOAD_CHUNK_BYTES,
    total_chunks: 1,
    received_indices: [...session.chunks.keys()].sort((left, right) => left - right),
    next_offset: session.chunks.has(0) ? session.manifest.size_bytes : 0,
    status: session.status,
  };
}

function parseByteRange(value) {
  const match = /^bytes=(\d+)-(\d*)$/.exec(value ?? "");
  if (!match) return null;
  const start = Number(match[1]);
  const end = match[2] ? Number(match[2]) : ARTIFACT_BYTES.length - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end) return null;
  return { start, end: Math.min(end, ARTIFACT_BYTES.length - 1) };
}

function websocketFrame(payload) {
  const bytes = Buffer.from(JSON.stringify(payload));
  assert.ok(bytes.length <= 65_535, "test WebSocket payload must fit one frame");
  if (bytes.length <= 125) {
    return Buffer.concat([Buffer.from([0x81, bytes.length]), bytes]);
  }
  const header = Buffer.alloc(4);
  header[0] = 0x81;
  header[1] = 126;
  header.writeUInt16BE(bytes.length, 2);
  return Buffer.concat([header, bytes]);
}

function consumeWebsocketFrames(buffer, onFrame) {
  let offset = 0;
  while (buffer.length - offset >= 2) {
    const first = buffer[offset];
    const second = buffer[offset + 1];
    const opcode = first & 0x0f;
    const masked = (second & 0x80) !== 0;
    let length = second & 0x7f;
    let headerLength = 2;
    if (length === 126) {
      if (buffer.length - offset < 4) break;
      length = buffer.readUInt16BE(offset + 2);
      headerLength = 4;
    } else if (length === 127) {
      throw new Error("test WebSocket server does not accept 64-bit frames");
    }
    const maskLength = masked ? 4 : 0;
    const frameLength = headerLength + maskLength + length;
    if (buffer.length - offset < frameLength) break;
    let payloadStart = offset + headerLength;
    let mask;
    if (masked) {
      mask = buffer.subarray(payloadStart, payloadStart + 4);
      payloadStart += 4;
    }
    const payload = Buffer.from(buffer.subarray(payloadStart, payloadStart + length));
    if (masked) {
      for (let index = 0; index < payload.length; index += 1) {
        payload[index] ^= mask[index % 4];
      }
    }
    if (opcode === 0x1) {
      onFrame(JSON.parse(payload.toString("utf8")));
    } else if (opcode === 0x8) {
      onFrame({ type: "__close__" });
    }
    offset += frameLength;
  }
  return buffer.subarray(offset);
}

function validRequestId(value) {
  return typeof value === "string" && /^[A-Za-z0-9._:-]{1,256}$/.test(value);
}

function ingressHeaders(request) {
  return {
    host: request.headers.host ?? "",
    forwarded_host: request.headers["x-forwarded-host"] ?? "",
    forwarded_proto: request.headers["x-forwarded-proto"] ?? "",
    profile: request.headers["x-transport-profile"] ?? "",
    cf_ray: request.headers["cf-ray"] ?? "",
  };
}

function createUpstream() {
  const prompts = new Map();
  const requests = [];
  const uploads = new Map();
  const uploadChunkAttempts = [];
  const artifactRequests = [];
  let artifactDrops = 0;
  let websocketConnections = 0;
  const websocketAfterCursors = [];
  const websocketCommands = [];

  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://upstream.invalid");
    if (request.method === "GET" && url.pathname === "/health") {
      json(response, 200, { ok: true });
      return;
    }
    if (request.method === "GET" && url.pathname === HTTP_STATE_PATH) {
      json(response, 200, {
        prompt_attempts: requests.length,
        processed_prompts: prompts.size,
        requests,
        uploads: [...uploads.values()].map((session) => uploadSessionPayload(session)),
        upload_chunk_attempts: uploadChunkAttempts,
        artifact_requests: artifactRequests,
        websocket_connections: websocketConnections,
        websocket_after_cursors: websocketAfterCursors,
        websocket_commands: websocketCommands,
      });
      return;
    }

    if (url.pathname.startsWith(`${REMOTE_API_PATH}/uploads`)) {
      const match = new RegExp(`^${REMOTE_API_PATH}/uploads(?:/([^/]+)(?:/(chunks/\\d+|complete))?)?$`).exec(url.pathname);
      if (match === null) {
        json(response, 404, { code: "not_found" });
        return;
      }
      const [, uploadId, action] = match;
      if (request.method === "POST" && uploadId === undefined) {
        let manifest;
        try {
          manifest = await readJson(request);
        } catch (error) {
          json(response, 400, { code: error.message });
          return;
        }
        if (
          typeof manifest?.filename !== "string" ||
          typeof manifest?.size_bytes !== "number" ||
          manifest.size_bytes < 1 ||
          typeof manifest?.sha256 !== "string"
        ) {
          json(response, 400, { code: "request_invalid" });
          return;
        }
        const created = {
          upload_id: `upl_transport_${uploads.size + 1}`,
          manifest,
          chunks: new Map(),
          status: "active",
          lost_response: false,
        };
        uploads.set(created.upload_id, created);
        json(response, 201, uploadSessionPayload(created));
        return;
      }

      const upload = uploads.get(uploadId);
      if (upload === undefined) {
        json(response, 404, { code: "not_found" });
        return;
      }
      if (request.method === "GET" && action === undefined) {
        json(response, 200, uploadSessionPayload(upload));
        return;
      }
      if (request.method === "DELETE" && action === undefined) {
        upload.status = "cancelled";
        json(response, 200, uploadSessionPayload(upload));
        return;
      }
      if (request.method === "POST" && action === "complete") {
        if (upload.status !== "active" || !upload.chunks.has(0)) {
          json(response, 409, { code: "upload_incomplete" });
          return;
        }
        upload.status = "completed";
        json(response, 201, { attachment_id: "att_transport", sha256: upload.manifest.sha256 });
        return;
      }
      if (request.method === "PUT" && action === "chunks/0") {
        let body;
        try {
          body = await readUploadChunk(request);
        } catch (error) {
          json(response, 400, { code: error.message });
          return;
        }
        const digest = createHash("sha256").update(body).digest("hex");
        const valid =
          upload.status === "active" &&
          request.headers["upload-offset"] === "0" &&
          request.headers["x-chunk-sha256"] === upload.manifest.sha256 &&
          body.length === upload.manifest.size_bytes &&
          digest === upload.manifest.sha256;
        if (!valid) {
          json(response, 400, { code: "request_invalid" });
          return;
        }
        const replayed = upload.chunks.has(0);
        upload.chunks.set(0, digest);
        uploadChunkAttempts.push({
          upload_id: upload.upload_id,
          index: 0,
          replayed,
          path: url.pathname,
          ...ingressHeaders(request),
        });
        if (!replayed && !upload.lost_response) {
          upload.lost_response = true;
          // The binary chunk is committed before the proxy loses its response.
          request.socket.destroy();
          return;
        }
        json(response, 200, uploadSessionPayload(upload));
        return;
      }
      json(response, 404, { code: "not_found" });
      return;
    }

    if (request.method === "GET" && url.pathname === ARTIFACT_PATH) {
      const requested = parseByteRange(request.headers.range);
      artifactRequests.push({
        path: url.pathname,
        range: request.headers.range ?? "",
        if_range: request.headers["if-range"] ?? "",
        ...ingressHeaders(request),
      });
      if (requested === null || requested.start >= ARTIFACT_BYTES.length) {
        response.writeHead(416, {
          "accept-ranges": "bytes",
          "content-range": `bytes */${ARTIFACT_BYTES.length}`,
          etag: `"${ARTIFACT_ETAG}"`,
          "content-length": "0",
        });
        response.end();
        return;
      }
      if (request.headers["if-range"] !== `"${ARTIFACT_ETAG}"`) {
        json(response, 400, { code: "if_range_invalid" });
        return;
      }
      const bytes = ARTIFACT_BYTES.subarray(requested.start, requested.end + 1);
      response.writeHead(206, {
        "accept-ranges": "bytes",
        "content-type": "application/octet-stream",
        "content-range": `bytes ${requested.start}-${requested.end}/${ARTIFACT_BYTES.length}`,
        etag: `"${ARTIFACT_ETAG}"`,
        "content-length": String(bytes.length),
      });
      if (artifactDrops === 0 && requested.start === 0) {
        artifactDrops += 1;
        response.write(bytes.subarray(0, Math.ceil(bytes.length / 2)));
        setTimeout(() => request.socket.destroy(), 25);
        return;
      }
      response.end(bytes);
      return;
    }
    if (request.method !== "POST" || url.pathname !== HTTP_PROMPT_PATH) {
      json(response, 404, { code: "not_found" });
      return;
    }

    let payload;
    try {
      payload = await readJson(request);
    } catch (error) {
      json(response, 400, { code: error.message });
      return;
    }
    if (
      !validRequestId(payload?.client_request_id) ||
      typeof payload?.prompt !== "string" ||
      payload.prompt.length === 0
    ) {
      json(response, 400, { code: "invalid_prompt" });
      return;
    }

    const requestId = payload.client_request_id;
    const fingerprint = JSON.stringify({ prompt: payload.prompt, thread_id: payload.thread_id });
    const prior = prompts.get(requestId);
    if (prior && prior.fingerprint !== fingerprint) {
      json(response, 409, { code: "request_id_conflict" });
      return;
    }
    const replayed = Boolean(prior);
    if (!prior) {
      prompts.set(requestId, { fingerprint, dropped: false });
    }
    requests.push({
      path: url.pathname,
      client_request_id: requestId,
      ...ingressHeaders(request),
    });

    const record = prompts.get(requestId);
    if (!replayed && !record.dropped) {
      record.dropped = true;
      // The operation is already committed. Deliberately lose its response.
      // nginx returns a gateway error or fetch rejects; both are valid loss modes.
      request.socket.destroy();
      return;
    }
    json(response, 200, { accepted: true, replayed: true, client_request_id: requestId });
  });

  server.on("upgrade", (request, socket) => {
    const url = new URL(request.url ?? "/", "http://upstream.invalid");
    const key = request.headers["sec-websocket-key"];
    if (url.pathname !== WEBSOCKET_PATH || url.search || typeof key !== "string") {
      socket.destroy();
      return;
    }
    const accept = createHash("sha1")
      .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write(
      [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        `Sec-WebSocket-Accept: ${accept}`,
        "",
        "",
      ].join("\r\n"),
    );
    websocketConnections += 1;
    const connectionNumber = websocketConnections;
    const cursor = connectionNumber === 1 ? 7 : 8;
    let authenticated = false;
    let buffer = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      try {
        buffer = Buffer.concat([buffer, chunk]);
        buffer = consumeWebsocketFrames(buffer, (message) => {
          if (message?.type === "__close__") {
            socket.end();
            return;
          }
          if (!authenticated) {
            if (message?.type !== "auth" || message.access_token !== "transport-test-token") {
              socket.write(websocketFrame({ type: "auth_invalid", message: "invalid access token" }));
              socket.end();
              return;
            }
            authenticated = true;
            socket.write(websocketFrame({ type: "auth_ok", ha_version: "2026.7.0" }));
            return;
          }
          if (
            message?.type !== "codex_bridge/subscribe_events" ||
            !Number.isSafeInteger(message.id) ||
            !Number.isSafeInteger(message.after) ||
            message.after < 0
          ) {
            socket.write(
              websocketFrame({
                id: message?.id ?? 0,
                type: "result",
                success: false,
                error: { code: "invalid_format", message: "invalid subscribe_events command" },
              }),
            );
            socket.end();
            return;
          }
          websocketAfterCursors.push(message.after);
          websocketCommands.push({
            id: message.id,
            type: message.type,
            after: message.after,
            path: url.pathname,
            ...ingressHeaders(request),
          });
          socket.write(
            websocketFrame({
              id: message.id,
              type: "result",
              success: true,
              result: { subscription_id: message.id, api_version: 1 },
            }),
          );
          socket.write(
            websocketFrame({
              id: message.id,
              type: "event",
              event: {
                type: "event",
                event: {
                  sequence: cursor,
                  event_id: `transport-${cursor}`,
                  event_type: "bridge.connected",
                  payload: { after: message.after },
                },
              },
            }),
          );
          // Both connections close, so the test observes a real reconnect rather than
          // merely a second open socket.
          setTimeout(() => socket.end(), 25);
        });
      } catch {
        socket.destroy();
      }
    });
    socket.write(websocketFrame({ type: "auth_required", ha_version: "2026.7.0" }));
  });

  return server;
}

async function startServer() {
  const port = Number(process.env.TRANSPORT_PORT ?? 8081);
  const server = createUpstream();
  await new Promise((resolve) => server.listen(port, "0.0.0.0", resolve));
  const stop = () => server.close(() => process.exit(0));
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

function endpoint(origin, path) {
  assert.ok(path.startsWith("/"), "transport paths must remain origin-relative");
  const url = new URL(path, origin);
  assert.equal(url.origin, new URL(origin).origin, "path must stay on the HA origin");
  return url;
}

async function waitForProxy(origin) {
  const health = endpoint(origin, "/health");
  let lastError;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await fetch(health);
      if (response.ok) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw lastError ?? new Error("proxy did not become ready");
}

async function websocketMessages(origin, after) {
  const url = endpoint(origin, WEBSOCKET_PATH);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return new Promise((resolve, reject) => {
    const messages = [];
    const socket = new WebSocket(url);
    const timeout = setTimeout(() => {
      socket.close();
      reject(new Error("WebSocket test timed out"));
    }, 5_000);
    socket.addEventListener("open", () => {
      // Home Assistant requires authentication before accepting commands. This
      // token is intentionally synthetic and only exists inside the test harness.
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "auth_required") {
        socket.send(JSON.stringify({ type: "auth", access_token: "transport-test-token" }));
      } else if (message.type === "auth_ok") {
        socket.send(
          JSON.stringify({
            id: 1,
            type: "codex_bridge/subscribe_events",
            after,
            scopes: ["auth", "runtime", "thread"],
          }),
        );
      } else if (message.type === "event") {
        messages.push(message);
      } else if (message.type === "result") {
        assert.equal(message.success, true, "subscribe_events must be accepted");
        assert.equal(message.id, 1);
      } else {
        reject(new Error(`unexpected HA WebSocket message: ${message.type}`));
      }
    });
    socket.addEventListener("error", () => {
      clearTimeout(timeout);
      reject(new Error("WebSocket proxy connection failed"));
    });
    socket.addEventListener("close", () => {
      clearTimeout(timeout);
      resolve(messages);
    });
  });
}

async function reconnectingWebsocketMessages(origin) {
  const batches = [];
  let cursor = 0;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const messages = await websocketMessages(origin, cursor);
    assert.equal(messages.length, 1, "each deterministic stream must emit one cursor-bearing frame");
    batches.push(messages[0]);
    cursor = messages[0].event?.event?.sequence;
    assert.ok(Number.isSafeInteger(cursor), "HA event must carry a trusted sequence cursor");
  }
  return batches;
}

function uploadManifest(filename, bytes, sha256) {
  return {
    filename,
    mime_type: "application/octet-stream",
    relative_path: `evidence/${filename}`,
    size_bytes: bytes.length,
    sha256,
  };
}

async function remotePathRecovery(origin) {
  const requestPaths = [];
  const sameOriginFetch = async (path, init) => {
    assert.ok(path.startsWith("/"), "browser request paths must remain relative");
    const url = endpoint(origin, path);
    requestPaths.push(path);
    return fetch(url, init);
  };

  const created = await sameOriginFetch(UPLOADS_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(uploadManifest("recovery.bin", UPLOAD_BYTES, UPLOAD_SHA256)),
  });
  assert.equal(created.status, 201, "v1 upload creation must cross the proxy");
  const upload = await created.json();
  const uploadPath = `${UPLOADS_PATH}/${upload.upload_id}`;
  const chunkPath = `${uploadPath}/chunks/0`;
  const chunkRequest = {
    method: "PUT",
    headers: {
      "Upload-Offset": "0",
      "X-Chunk-SHA256": UPLOAD_SHA256,
      "content-type": "application/octet-stream",
    },
    body: UPLOAD_BYTES,
  };

  let lostChunkResponse = false;
  try {
    lostChunkResponse = (await sameOriginFetch(chunkPath, chunkRequest)).ok;
  } catch {
    // The synthetic HA path commits this chunk, then loses only its response.
  }
  assert.equal(lostChunkResponse, false, "the first committed chunk response must be lost");

  const afterLoss = await sameOriginFetch(uploadPath);
  assert.equal(afterLoss.status, 200, "upload status must survive the response loss");
  assert.deepEqual((await afterLoss.json()).received_indices, [0]);

  const retry = await sameOriginFetch(chunkPath, chunkRequest);
  assert.equal(retry.status, 200, "retrying the same chunk must be idempotent");
  assert.deepEqual((await retry.json()).received_indices, [0]);

  const completed = await sameOriginFetch(`${uploadPath}/complete`, { method: "POST" });
  assert.equal(completed.status, 201, "completed upload must use the v1 proxy path");

  const cancelledCreate = await sameOriginFetch(UPLOADS_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(uploadManifest("cancel.bin", Buffer.from("cancel"), createHash("sha256").update("cancel").digest("hex"))),
  });
  assert.equal(cancelledCreate.status, 201);
  const cancelledUpload = await cancelledCreate.json();
  const cancelled = await sameOriginFetch(`${UPLOADS_PATH}/${cancelledUpload.upload_id}`, {
    method: "DELETE",
  });
  assert.equal(cancelled.status, 200, "upload cancellation must use the v1 proxy path");
  assert.equal((await cancelled.json()).status, "cancelled");

  const firstRange = await sameOriginFetch(ARTIFACT_PATH, {
    headers: { Range: `bytes=0-${ARTIFACT_BYTES.length - 1}`, "If-Range": `"${ARTIFACT_ETAG}"` },
  });
  assert.equal(firstRange.status, 206, "artifact range must be served through the proxy");
  assert.equal(firstRange.headers.get("etag"), `"${ARTIFACT_ETAG}"`);
  const reader = firstRange.body.getReader();
  const partial = [];
  let interrupted = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      partial.push(Buffer.from(value));
    }
  } catch {
    interrupted = true;
  }
  const received = Buffer.concat(partial);
  assert.equal(interrupted, true, "the first artifact response must disconnect mid-download");
  assert.ok(received.length > 0 && received.length < ARTIFACT_BYTES.length);

  const resumed = await sameOriginFetch(ARTIFACT_PATH, {
    headers: {
      Range: `bytes=${received.length}-${ARTIFACT_BYTES.length - 1}`,
      "If-Range": `"${ARTIFACT_ETAG}"`,
    },
  });
  assert.equal(resumed.status, 206, "artifact reconnect must resume with a range request");
  assert.deepEqual(Buffer.concat([received, Buffer.from(await resumed.arrayBuffer())]), ARTIFACT_BYTES);

  const unsatisfied = await sameOriginFetch(ARTIFACT_PATH, {
    headers: { Range: "bytes=999-1000", "If-Range": `"${ARTIFACT_ETAG}"` },
  });
  assert.equal(unsatisfied.status, 416, "an unsatisfied artifact range must stay typed");
  assert.equal(unsatisfied.headers.get("content-range"), `bytes */${ARTIFACT_BYTES.length}`);

  return { requestPaths, upload_id: upload.upload_id };
}

async function runClient() {
  const origin = process.env.PROXY_ORIGIN ?? "http://127.0.0.1:8080";
  const expectedIngress = {
    host: process.env.TRANSPORT_EXPECTED_HOST ?? "ha-lan.invalid",
    forwarded_host: process.env.TRANSPORT_EXPECTED_HOST ?? "ha-lan.invalid",
    forwarded_proto: process.env.TRANSPORT_EXPECTED_PROTO ?? "http",
    profile: process.env.TRANSPORT_EXPECTED_PROFILE ?? "lan",
    cf_ray: process.env.TRANSPORT_EXPECTED_CF_RAY ?? "",
  };
  await waitForProxy(origin);

  const promptEndpoint = endpoint(origin, HTTP_PROMPT_PATH);
  const requestId = `transport-${randomUUID()}`;
  const payload = {
    thread_id: "thr_transport",
    prompt: "Prove exactly-once recovery through the Home Assistant proxy.",
    client_request_id: requestId,
  };
  const request = {
    method: "POST",
    headers: {
      "content-type": "application/json",
      // Trusted proxies must overwrite, not append to, hostile client input.
      "x-forwarded-host": "attacker.invalid",
      "x-forwarded-proto": "file",
      "x-transport-profile": "attacker",
      "cf-ray": "attacker-ray",
    },
    body: JSON.stringify(payload),
  };

  let firstAccepted = false;
  try {
    firstAccepted = (await fetch(promptEndpoint, request)).ok;
  } catch {
    // A socket reset is the other expected representation of a lost response.
  }
  assert.equal(firstAccepted, false, "the first committed prompt response must be lost");

  const retry = await fetch(promptEndpoint, request);
  assert.equal(retry.status, 200);
  assert.deepEqual(await retry.json(), {
    accepted: true,
    replayed: true,
    client_request_id: requestId,
  });

  const remote = await remotePathRecovery(origin);

  const stream = await reconnectingWebsocketMessages(origin);
  assert.deepEqual(
    stream.map((message) => ({
      id: message.id,
      type: message.type,
      sequence: message.event?.event?.sequence,
      after: message.event?.event?.payload?.after,
    })),
    [
      { id: 1, type: "event", sequence: 7, after: 0 },
      { id: 1, type: "event", sequence: 8, after: 7 },
    ],
  );

  const state = await (await fetch(endpoint(origin, HTTP_STATE_PATH))).json();
  assert.equal(state.prompt_attempts, 2);
  assert.equal(state.processed_prompts, 1, "a retry must not create another turn");
  assert.equal(state.websocket_connections, 2, "the stream must reconnect after a drop");
  assert.deepEqual(
    state.websocket_after_cursors,
    [0, 7],
    "the automatic reconnect must send the last observed cursor in JSON",
  );
  assert.deepEqual(
    state.websocket_commands.map(({ id, type, after, path }) => ({ id, type, after, path })),
    [
      { id: 1, type: "codex_bridge/subscribe_events", after: 0, path: WEBSOCKET_PATH },
      { id: 1, type: "codex_bridge/subscribe_events", after: 7, path: WEBSOCKET_PATH },
    ],
    "the proxy must preserve HA command shape and origin-relative WebSocket path",
  );
  assert.deepEqual(
    state.requests.map((entry) => entry.client_request_id),
    [requestId, requestId],
    "the retry must preserve client_request_id",
  );
  assert.deepEqual(state.requests.map((entry) => entry.path), [HTTP_PROMPT_PATH, HTTP_PROMPT_PATH]);
  const ingressRecords = [
    ...state.requests,
    ...state.upload_chunk_attempts,
    ...state.artifact_requests,
    ...state.websocket_commands,
  ];
  assert.ok(ingressRecords.length > 0);
  for (const entry of ingressRecords) {
    assert.deepEqual(
      {
        host: entry.host,
        forwarded_host: entry.forwarded_host,
        forwarded_proto: entry.forwarded_proto,
        profile: entry.profile,
        cf_ray: entry.cf_ray,
      },
      expectedIngress,
      "each route must normalize spoofable ingress headers to its trusted profile",
    );
  }
  assert.deepEqual(
    state.upload_chunk_attempts.map(({ upload_id, index, replayed, path }) => ({
      upload_id,
      index,
      replayed,
      path,
    })),
    [
      {
        upload_id: remote.upload_id,
        index: 0,
        replayed: false,
        path: `${UPLOADS_PATH}/${remote.upload_id}/chunks/0`,
      },
      {
        upload_id: remote.upload_id,
        index: 0,
        replayed: true,
        path: `${UPLOADS_PATH}/${remote.upload_id}/chunks/0`,
      },
    ],
    "the response-lost chunk must be committed once and replayed once through the proxy",
  );
  assert.deepEqual(
    state.artifact_requests.map(({ path, range, if_range }) => ({
      path,
      range,
      if_range,
    })),
    [
      {
        path: ARTIFACT_PATH,
        range: `bytes=0-${ARTIFACT_BYTES.length - 1}`,
        if_range: `"${ARTIFACT_ETAG}"`,
      },
      {
        path: ARTIFACT_PATH,
        range: `bytes=${Math.ceil(ARTIFACT_BYTES.length / 2)}-${ARTIFACT_BYTES.length - 1}`,
        if_range: `"${ARTIFACT_ETAG}"`,
      },
      {
        path: ARTIFACT_PATH,
        range: "bytes=999-1000",
        if_range: `"${ARTIFACT_ETAG}"`,
      },
    ],
    "artifact resume and 416 checks must preserve Range and If-Range through the proxy",
  );
  assert.ok(
    remote.requestPaths.every((path) => path.startsWith("/api/codex_bridge/")),
    "the synthetic browser must use only relative Home Assistant API paths",
  );
  const browserEvidence = JSON.stringify({ remote, state });
  assert.equal(browserEvidence.includes("transport-test-token"), false, "browser evidence must redact tokens");
  assert.equal(/https?:\/\/[^\s]*(?:app|bridge|upstream)/i.test(browserEvidence), false, "browser evidence must not contain private App or Bridge URLs");
  assert.equal(/(?:access_token|authorization|cookie)/i.test(browserEvidence), false, "browser evidence must not contain credential fields");

  process.stdout.write(`transport ${expectedIngress.profile} profile passed\n`);
}

if (process.argv.includes("--server")) {
  await startServer();
} else if (process.argv.includes("--client")) {
  await runClient();
} else {
  throw new Error("Specify --server or --client");
}
