"""Authenticated and bounded Home Assistant HTTP transport tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import psutil
import pytest

from homeassistant.setup import async_setup_component

from custom_components.codex_bridge.bridge_api import (
    BridgeApiClient,
    BridgeApiConflictError,
    BridgeApiEndpointError,
    BridgeApiPayloadTooLargeError,
    BridgeApiRedirectError,
    BridgeApiTimeoutError,
)
from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.http import async_register_http_views
from custom_components.codex_bridge.http_streaming import (
    DOWNLOAD_STREAM_CHUNK_BYTES,
    UPLOAD_CHUNK_MAX_BYTES,
    HttpStreamingError,
    parse_upload_chunk_request,
    safe_download_headers,
)


THREAD_ID = "thr_safe"
UPLOAD_ID = "upl_safe"
ARTIFACT_ID = "art_safe"
DIGEST = hashlib.sha256(b"payload").hexdigest()
TOKEN = "bridge-token-0123456789abcdef0123456789"
FIXTURES = Path(__file__).parents[2] / "fixtures"


class _FakeStream:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str],
        blocks: tuple[bytes, ...] = (),
    ) -> None:
        self.status = status
        self.headers = headers
        self.blocks = blocks
        self.requested_chunk_sizes: list[int] = []

    async def iter_chunked(self, chunk_bytes: int):
        self.requested_chunk_sizes.append(chunk_bytes)
        for block in self.blocks:
            yield block


async def _install_runtime(hass, client, *, api_version: int = 1) -> None:
    assert await async_setup_component(hass, "http", {})
    hass.data.setdefault(DOMAIN, {})[DATA_ENTRIES] = {
        "entry": SimpleNamespace(client=client, api_version=api_version)
    }
    async_register_http_views(hass)


def _upload_payload(**updates) -> dict:
    payload = {
        "filename": "notes.txt",
        "mime_type": "text/plain",
        "relative_path": "docs/notes.txt",
        "size_bytes": 7,
        "sha256": DIGEST,
    }
    payload.update(updates)
    return payload


def _session_payload(*, status: str = "active") -> dict:
    return {
        "upload_id": UPLOAD_ID,
        "thread_id": THREAD_ID,
        **_upload_payload(),
        "chunk_size": UPLOAD_CHUNK_MAX_BYTES,
        "total_chunks": 1,
        "received_indices": [],
        "next_offset": 0,
        "status": status,
    }


async def test_all_v1_upload_controls_require_an_authenticated_ha_admin(
    hass,
    hass_client,
    hass_client_no_auth,
    hass_read_only_access_token,
) -> None:
    bridge = SimpleNamespace(
        async_create_upload=AsyncMock(return_value=_session_payload()),
        async_get_upload=AsyncMock(return_value=_session_payload()),
        async_upload_chunk=AsyncMock(return_value=_session_payload()),
        async_complete_upload=AsyncMock(return_value={"attachment_id": "att_safe"}),
        async_cancel_upload=AsyncMock(return_value=_session_payload(status="cancelled")),
    )
    await _install_runtime(hass, bridge)
    anonymous = await hass_client_no_auth()
    read_only = await hass_client(hass_read_only_access_token)
    requests = (
        ("post", f"/api/codex_bridge/threads/{THREAD_ID}/uploads", {"json": _upload_payload()}),
        ("get", f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}", {}),
        (
            "put",
            f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/chunks/0",
            {
                "data": b"payload",
                "headers": {
                    "Upload-Offset": "0",
                    "X-Chunk-SHA256": DIGEST,
                },
            },
        ),
        ("post", f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/complete", {}),
        ("delete", f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}", {}),
    )

    for method, path, kwargs in requests:
        assert (await getattr(anonymous, method)(path, **kwargs)).status == 401
        assert (await getattr(read_only, method)(path, **kwargs)).status in {401, 403}

    assert bridge.async_create_upload.await_count == 0
    assert bridge.async_get_upload.await_count == 0
    assert bridge.async_upload_chunk.await_count == 0
    assert bridge.async_complete_upload.await_count == 0
    assert bridge.async_cancel_upload.await_count == 0


async def test_v1_upload_session_create_status_complete_and_cancel(
    hass,
    hass_client,
) -> None:
    bridge = SimpleNamespace(
        async_create_upload=AsyncMock(return_value=_session_payload()),
        async_get_upload=AsyncMock(return_value=_session_payload()),
        async_complete_upload=AsyncMock(
            return_value={"attachment_id": "att_safe", "sha256": DIGEST}
        ),
        async_cancel_upload=AsyncMock(return_value=_session_payload(status="cancelled")),
    )
    await _install_runtime(hass, bridge)
    client = await hass_client()

    created = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads",
        json=_upload_payload(),
    )
    status = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}"
    )
    completed = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/complete"
    )
    cancelled = await client.delete(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}"
    )

    assert created.status == 201
    assert await created.json() == _session_payload()
    assert status.status == 200
    assert completed.status == 201
    assert cancelled.status == 200
    bridge.async_create_upload.assert_awaited_once_with(
        THREAD_ID,
        filename="notes.txt",
        mime_type="text/plain",
        relative_path="docs/notes.txt",
        size_bytes=7,
        sha256=DIGEST,
    )
    bridge.async_get_upload.assert_awaited_once_with(THREAD_ID, UPLOAD_ID)
    bridge.async_complete_upload.assert_awaited_once_with(THREAD_ID, UPLOAD_ID)
    bridge.async_cancel_upload.assert_awaited_once_with(THREAD_ID, UPLOAD_ID)


async def test_v1_chunk_body_is_streamed_to_bridge_in_bounded_blocks(
    hass,
    hass_client,
) -> None:
    observed: list[bytes] = []

    async def upload_chunk(
        thread_id,
        upload_id,
        index,
        *,
        offset,
        content_length,
        sha256,
        content,
    ):
        assert (thread_id, upload_id, index) == (THREAD_ID, UPLOAD_ID, 0)
        assert (offset, content_length, sha256) == (0, 7, DIGEST)
        async for block in content:
            observed.append(block)
        return _session_payload()

    bridge = SimpleNamespace(async_upload_chunk=upload_chunk)
    await _install_runtime(hass, bridge)
    client = await hass_client()
    response = await client.put(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/chunks/0",
        data=b"payload",
        headers={"Upload-Offset": "0", "X-Chunk-SHA256": DIGEST},
    )

    assert response.status == 200
    assert b"".join(observed) == b"payload"
    assert observed
    assert max(map(len, observed)) <= DOWNLOAD_STREAM_CHUNK_BYTES


async def test_v1_artifact_range_is_streamed_with_backpressure_and_safe_headers(
    hass,
    hass_client,
) -> None:
    stream = _FakeStream(
        status=206,
        headers={
            "Content-Type": "text/html",
            "Content-Length": "7",
            "Content-Range": "bytes 2-8/10",
            "Content-Disposition": (
                "attachment; filename=\"notes.txt\"; "
                "filename*=UTF-8''notes.txt"
            ),
            "ETag": f'"{DIGEST}"',
            "Connection": "keep-alive",
            "Set-Cookie": "private=secret",
        },
        blocks=(b"pay", b"load"),
    )
    observed: dict[str, object] = {}
    closed = asyncio.Event()

    @asynccontextmanager
    async def stream_artifact(
        thread_id,
        artifact_id,
        *,
        range_header,
        if_range,
    ):
        observed.update(
            thread_id=thread_id,
            artifact_id=artifact_id,
            range_header=range_header,
            if_range=if_range,
        )
        try:
            yield stream
        finally:
            closed.set()

    bridge = SimpleNamespace(async_stream_artifact=stream_artifact)
    await _install_runtime(hass, bridge)
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}",
        headers={"Range": "bytes=2-8", "If-Range": f'"{DIGEST}"'},
    )

    assert response.status == 206
    assert await response.read() == b"payload"
    assert observed == {
        "thread_id": THREAD_ID,
        "artifact_id": ARTIFACT_ID,
        "range_header": "bytes=2-8",
        "if_range": f'"{DIGEST}"',
    }
    assert stream.requested_chunk_sizes == [DOWNLOAD_STREAM_CHUNK_BYTES]
    assert closed.is_set()
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Range"] == "bytes 2-8/10"
    assert "Set-Cookie" not in response.headers
    assert "secret" not in repr(response.headers)


async def test_v1_unsatisfied_range_preserves_416_metadata_without_a_body(
    hass,
    hass_client,
) -> None:
    stream = _FakeStream(
        status=416,
        headers={
            "Content-Range": "bytes */10",
            "Content-Disposition": (
                "attachment; filename=\"notes.txt\"; "
                "filename*=UTF-8''notes.txt"
            ),
            "ETag": f'"{DIGEST}"',
        },
        blocks=(b"must not be forwarded",),
    )

    @asynccontextmanager
    async def stream_artifact(*_args, **_kwargs):
        yield stream

    await _install_runtime(
        hass, SimpleNamespace(async_stream_artifact=stream_artifact)
    )
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}",
        headers={"Range": "bytes=99-100"},
    )

    assert response.status == 416
    assert await response.read() == b""
    assert response.headers["Content-Range"] == "bytes */10"
    assert response.headers["Content-Length"] == "0"
    assert stream.requested_chunk_sizes == []


@pytest.mark.parametrize(
    "if_range",
    [
        '"stale"',
        f'W/"{DIGEST}"',
        "Wed, 21 Oct 2015 07:28:00 GMT",
    ],
)
async def test_if_range_variants_are_forwarded_unchanged_and_full_200_is_preserved(
    hass,
    hass_client,
    if_range,
) -> None:
    observed: dict[str, str | None] = {}
    stream = _FakeStream(
        status=200,
        headers={
            "Content-Length": "7",
            "Content-Disposition": (
                "attachment; filename=\"notes.txt\"; "
                "filename*=UTF-8''notes.txt"
            ),
            "ETag": f'"{DIGEST}"',
        },
        blocks=(b"payload",),
    )

    @asynccontextmanager
    async def stream_artifact(
        *_args,
        range_header,
        if_range,
        **_kwargs,
    ):
        observed["range"] = range_header
        observed["if_range"] = if_range
        yield stream

    await _install_runtime(
        hass, SimpleNamespace(async_stream_artifact=stream_artifact)
    )
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}",
        headers={"Range": "bytes=2-5", "If-Range": if_range},
    )

    assert response.status == 200
    assert await response.read() == b"payload"
    assert observed == {"range": "bytes=2-5", "if_range": if_range}


async def test_artifact_timeout_before_headers_returns_a_safe_gateway_timeout(
    hass,
    hass_client,
) -> None:
    @asynccontextmanager
    async def stream_artifact(*_args, **_kwargs):
        raise BridgeApiTimeoutError()
        yield  # pragma: no cover - make this an async generator

    await _install_runtime(
        hass, SimpleNamespace(async_stream_artifact=stream_artifact)
    )
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}"
    )

    assert response.status == 504
    assert await response.json() == {
        "code": "bridge_timeout",
        "message": "Codex Bridge request failed",
    }


async def test_artifact_failure_after_headers_aborts_the_partial_response_and_closes_upstream(
    hass,
    hass_client,
) -> None:
    closed = asyncio.Event()

    class FailingStream(_FakeStream):
        async def iter_chunked(self, chunk_bytes: int):
            self.requested_chunk_sizes.append(chunk_bytes)
            yield b"pay"
            raise BridgeApiTimeoutError()

    stream = FailingStream(
        status=200,
        headers={
            "Content-Length": "7",
            "Content-Disposition": (
                "attachment; filename=\"notes.txt\"; "
                "filename*=UTF-8''notes.txt"
            ),
            "ETag": f'"{DIGEST}"',
        },
    )

    @asynccontextmanager
    async def stream_artifact(*_args, **_kwargs):
        try:
            yield stream
        finally:
            closed.set()

    await _install_runtime(
        hass, SimpleNamespace(async_stream_artifact=stream_artifact)
    )
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}"
    )

    with pytest.raises(aiohttp.ClientPayloadError):
        await response.read()
    await asyncio.wait_for(closed.wait(), timeout=2)
    assert stream.requested_chunk_sizes == [DOWNLOAD_STREAM_CHUNK_BYTES]


async def test_invalid_range_header_is_rejected_before_bridge_access(
    hass,
    hass_client,
) -> None:
    bridge = SimpleNamespace(async_stream_artifact=AsyncMock())
    await _install_runtime(hass, bridge)
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}",
        headers={"Range": "bytes=" + ("1" * 300)},
    )

    assert response.status == 400
    bridge.async_stream_artifact.assert_not_called()


@pytest.mark.parametrize(
    ("index", "headers", "expected_status"),
    [
        (
            "-1",
            {"Upload-Offset": "0", "Content-Length": "1", "X-Chunk-SHA256": DIGEST},
            400,
        ),
        (
            "0",
            {"Upload-Offset": "-1", "Content-Length": "1", "X-Chunk-SHA256": DIGEST},
            400,
        ),
        (
            "0",
            {
                "Upload-Offset": "0",
                "Content-Length": str(UPLOAD_CHUNK_MAX_BYTES + 1),
                "X-Chunk-SHA256": DIGEST,
            },
            413,
        ),
        (
            "0",
            {"Upload-Offset": "0", "Content-Length": "1", "X-Chunk-SHA256": "A" * 64},
            400,
        ),
    ],
)
def test_invalid_chunk_metadata_is_rejected_before_body_iteration(
    index, headers, expected_status
) -> None:
    request = SimpleNamespace(headers=headers)

    with pytest.raises(HttpStreamingError) as error:
        parse_upload_chunk_request(request, index)

    assert error.value.status == expected_status


async def test_bridge_client_forwards_exact_v1_upload_contract_without_browser_auth(
    bridge_server_factory,
) -> None:
    paths: list[tuple[str, str]] = []
    bodies: list[bytes] = []
    manifests: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/ready":
            return web.json_response(
                json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
            )
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["X-Codex-Bridge-Api"] == "1"
        assert request.headers.get("X-Browser-Authorization") is None
        paths.append((request.method, request.path))
        if request.method == "POST" and request.path.endswith("/uploads"):
            manifests.append(await request.json())
            return web.json_response(_session_payload(), status=201)
        if "/chunks/" in request.path:
            assert request.headers["Upload-Offset"] == "0"
            assert request.headers["X-Chunk-SHA256"] == DIGEST
            assert request.content_length == 7
            received = bytearray()
            async for block in request.content.iter_chunked(2):
                received.extend(block)
            bodies.append(bytes(received))
            return web.json_response(_session_payload())
        if request.path.endswith("/complete"):
            return web.json_response({"attachment_id": "att_safe"}, status=201)
        if request.method == "DELETE":
            return web.json_response(_session_payload(status="cancelled"))
        return web.json_response(_session_payload())

    async def body():
        yield b"pay"
        yield b"load"

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        bridge = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await bridge.async_ready()
        await bridge.async_create_upload(THREAD_ID, **_upload_payload())
        await bridge.async_get_upload(THREAD_ID, UPLOAD_ID)
        await bridge.async_upload_chunk(
            THREAD_ID,
            UPLOAD_ID,
            0,
            offset=0,
            content_length=7,
            sha256=DIGEST,
            content=body(),
        )
        await bridge.async_complete_upload(THREAD_ID, UPLOAD_ID)
        await bridge.async_cancel_upload(THREAD_ID, UPLOAD_ID)

    assert manifests == [_upload_payload()]
    assert bodies == [b"payload"]
    assert paths == [
        ("POST", f"/threads/{THREAD_ID}/uploads"),
        ("GET", f"/threads/{THREAD_ID}/uploads/{UPLOAD_ID}"),
        ("PUT", f"/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/chunks/0"),
        ("POST", f"/threads/{THREAD_ID}/uploads/{UPLOAD_ID}/complete"),
        ("DELETE", f"/threads/{THREAD_ID}/uploads/{UPLOAD_ID}"),
    ]


async def test_bridge_client_preserves_206_and_416_and_refuses_redirects(
    bridge_server_factory,
) -> None:
    statuses = iter((206, 416, 302))

    async def handler(request: web.Request) -> web.StreamResponse:
        if request.path == "/ready":
            return web.json_response(
                json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
            )
        status = next(statuses)
        if status == 302:
            raise web.HTTPFound("http://redirect.invalid/private")
        headers = (
            {
                "Content-Length": "4",
                "Content-Range": "bytes 0-3/10",
            }
            if status == 206
            else {"Content-Range": "bytes */10"}
        )
        return web.Response(status=status, body=b"data" if status == 206 else b"", headers=headers)

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        bridge = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await bridge.async_ready()
        async with bridge.async_stream_artifact(
            THREAD_ID, ARTIFACT_ID, range_header="bytes=0-3"
        ) as partial:
            assert partial.status == 206
            assert await partial.read_chunk(4) == b"data"
        async with bridge.async_stream_artifact(
            THREAD_ID, ARTIFACT_ID, range_header="bytes=99-100"
        ) as unsatisfied:
            assert unsatisfied.status == 416
            assert unsatisfied.headers["Content-Range"] == "bytes */10"
        with pytest.raises(BridgeApiRedirectError):
            async with bridge.async_stream_artifact(THREAD_ID, ARTIFACT_ID):
                pass


async def test_bridge_client_rejects_upload_path_and_digest_injection_before_network() -> None:
    class UnexpectedSession:
        async def request(self, *_args, **_kwargs):
            raise AssertionError("network must not be reached")

    bridge = BridgeApiClient(
        UnexpectedSession(), "http://127.0.0.1:8766", TOKEN
    )
    bridge._api_version = 1

    with pytest.raises(BridgeApiEndpointError):
        await bridge.async_get_upload("../status", UPLOAD_ID)
    with pytest.raises(BridgeApiEndpointError):
        await bridge.async_upload_chunk(
            THREAD_ID,
            UPLOAD_ID,
            0,
            offset=0,
            content_length=7,
            sha256="A" * 64,
            content=(),
        )


async def test_artifact_metadata_list_is_bounded_before_json_decoding(
    bridge_server_factory,
    monkeypatch,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path == "/ready":
            return web.json_response(
                json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
            )
        return web.Response(body=b"[" + (b"x" * 64))

    monkeypatch.setattr(
        "custom_components.codex_bridge.bridge_api._ARTIFACT_LIST_MAX_BYTES", 64
    )
    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        bridge = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await bridge.async_ready()
        with pytest.raises(BridgeApiPayloadTooLargeError):
            await bridge.async_list_artifacts(THREAD_ID)


async def test_repeated_chunk_retry_streams_and_returns_the_same_session(
    hass,
    hass_client,
) -> None:
    calls: list[bytes] = []

    async def upload_chunk(*_args, content, **_kwargs):
        body = bytearray()
        async for block in content:
            body.extend(block)
        calls.append(bytes(body))
        return _session_payload()

    await _install_runtime(hass, SimpleNamespace(async_upload_chunk=upload_chunk))
    client = await hass_client()
    path = (
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads/"
        f"{UPLOAD_ID}/chunks/0"
    )
    headers = {"Upload-Offset": "0", "X-Chunk-SHA256": DIGEST}

    first = await client.put(path, data=b"payload", headers=headers)
    retry = await client.put(path, data=b"payload", headers=headers)

    assert first.status == retry.status == 200
    assert await first.json() == await retry.json() == _session_payload()
    assert calls == [b"payload", b"payload"]


async def test_oversized_upload_manifest_is_rejected_before_bridge_access(
    hass,
    hass_client,
) -> None:
    bridge = SimpleNamespace(async_create_upload=AsyncMock())
    await _install_runtime(hass, bridge)
    client = await hass_client()
    body = b'{"padding":"' + (b"x" * (64 * 1024)) + b'"}'

    response = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 413
    assert (await response.json())["code"] == "payload_too_large"
    bridge.async_create_upload.assert_not_awaited()


async def test_cancelled_bridge_chunk_upload_propagates_and_closes_its_body(
    bridge_server_factory,
) -> None:
    body_started = asyncio.Event()
    body_closed = asyncio.Event()
    server_received = asyncio.Event()
    release_body = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/ready":
            return web.json_response(
                json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
            )
        try:
            async for _block in request.content.iter_chunked(1024):
                server_received.set()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return web.json_response(_session_payload())

    async def body():
        try:
            yield b"x" * 1024
            body_started.set()
            await release_body.wait()
            yield b"x" * (7 * 1024 - 1024)
        finally:
            body_closed.set()

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        bridge = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await bridge.async_ready()
        task = asyncio.create_task(
            bridge.async_upload_chunk(
                THREAD_ID,
                UPLOAD_ID,
                0,
                offset=0,
                content_length=7 * 1024,
                sha256=DIGEST,
                content=body(),
            )
        )
        await asyncio.wait_for(body_started.wait(), timeout=2)
        await asyncio.wait_for(server_received.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(body_closed.wait(), timeout=2)

    assert task.cancelled()


async def test_100_mib_upload_smoke_stays_bounded_through_the_ha_server(
    hass,
    hass_client,
    monkeypatch,
    tmp_path,
) -> None:
    total_bytes = 100 * 1024 * 1024
    block = b"z" * DOWNLOAD_STREAM_CHUNK_BYTES
    observed_bytes = 0
    observed_blocks = 0
    process = psutil.Process()
    rss_baseline = process.memory_info().rss
    rss_peak = rss_baseline
    sampling = True

    async def upload_chunk(*_args, content_length, content, **_kwargs):
        nonlocal observed_bytes, observed_blocks
        received = 0
        async for item in content:
            received += len(item)
            observed_bytes += len(item)
            observed_blocks += 1
        assert received == content_length
        return _session_payload()

    async def body(length: int):
        remaining = length
        while remaining:
            item = block if remaining >= len(block) else block[:remaining]
            remaining -= len(item)
            yield item

    async def sample_rss() -> None:
        nonlocal rss_peak
        while sampling:
            rss_peak = max(rss_peak, process.memory_info().rss)
            await asyncio.sleep(0.005)

    await _install_runtime(hass, SimpleNamespace(async_upload_chunk=upload_chunk))
    client = await hass_client()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    sampler = asyncio.create_task(sample_rss())
    try:
        offset = 0
        index = 0
        while offset < total_bytes:
            length = min(UPLOAD_CHUNK_MAX_BYTES, total_bytes - offset)
            digest = hashlib.sha256()
            remaining = length
            while remaining:
                item = block if remaining >= len(block) else block[:remaining]
                digest.update(item)
                remaining -= len(item)
            response = await client.put(
                (
                    f"/api/codex_bridge/threads/{THREAD_ID}/uploads/"
                    f"{UPLOAD_ID}/chunks/{index}"
                ),
                data=body(length),
                headers={
                    "Content-Length": str(length),
                    "Upload-Offset": str(offset),
                    "X-Chunk-SHA256": digest.hexdigest(),
                },
            )
            assert response.status == 200
            await response.read()
            offset += length
            index += 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        sampling = False
        await sampler
        tracemalloc.stop()

    assert observed_bytes == total_bytes
    assert observed_blocks > total_bytes // (2 * DOWNLOAD_STREAM_CHUNK_BYTES)
    assert peak - baseline_current < 24 * 1024 * 1024
    assert rss_peak - rss_baseline < 64 * 1024 * 1024
    assert list(tmp_path.iterdir()) == []


async def test_legacy_multipart_compatibility_proxies_the_original_body_without_tempfiles(
    hass,
    hass_client,
) -> None:
    observed: dict[str, object] = {}

    async def stream_legacy_attachment(
        thread_id,
        *,
        content_type,
        content_length,
        content,
    ):
        body = bytearray()
        async for block in content:
            body.extend(block)
        observed.update(
            thread_id=thread_id,
            content_type=content_type,
            content_length=content_length,
            body=bytes(body),
        )
        return {"attachment_id": "att_legacy"}

    await _install_runtime(
        hass,
        SimpleNamespace(async_stream_legacy_attachment=stream_legacy_attachment),
        api_version=0,
    )
    client = await hass_client()
    form = aiohttp.FormData()
    form.add_field(
        "file",
        b"legacy payload",
        filename="notes.txt",
        content_type="text/plain",
    )
    form.add_field("relative_path", "docs/notes.txt")
    response = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/attachments", data=form
    )

    assert response.status == 201
    assert observed["thread_id"] == THREAD_ID
    assert str(observed["content_type"]).startswith("multipart/form-data; boundary=")
    assert observed["content_length"] == len(observed["body"])
    assert b"legacy payload" in observed["body"]
    assert b"docs/notes.txt" in observed["body"]
    source = (
        Path(__file__).parents[3]
        / "custom_components"
        / "codex_bridge"
        / "http.py"
    ).read_text(encoding="utf-8")
    assert "tempfile" not in source
    assert "NamedTemporaryFile" not in source


async def test_legacy_artifact_compatibility_streams_without_the_buffered_client(
    hass,
    hass_client,
) -> None:
    stream = _FakeStream(
        status=200,
        headers={"Content-Length": "7", "Content-Type": "text/plain"},
        blocks=(b"pay", b"load"),
    )
    closed = asyncio.Event()

    @asynccontextmanager
    async def stream_legacy_artifact(*_args, **_kwargs):
        try:
            yield stream
        finally:
            closed.set()

    buffered = AsyncMock(side_effect=AssertionError("buffered path must not run"))
    bridge = SimpleNamespace(
        async_list_artifacts=AsyncMock(
            return_value=[{"artifact_id": ARTIFACT_ID, "filename": "notes.txt"}]
        ),
        async_stream_legacy_artifact=stream_legacy_artifact,
        async_download_artifact=buffered,
    )
    await _install_runtime(hass, bridge, api_version=0)
    client = await hass_client()
    response = await client.get(
        f"/api/codex_bridge/threads/{THREAD_ID}/artifacts/{ARTIFACT_ID}"
    )

    assert response.status == 200
    assert await response.read() == b"payload"
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert stream.requested_chunk_sizes == [DOWNLOAD_STREAM_CHUNK_BYTES]
    assert closed.is_set()
    buffered.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (BridgeApiConflictError(), 409, "conflict"),
        (BridgeApiPayloadTooLargeError(), 413, "payload_too_large"),
    ],
)
async def test_upload_failures_are_safe_and_keep_typed_statuses(
    hass,
    hass_client,
    error,
    status,
    code,
) -> None:
    bridge = SimpleNamespace(async_create_upload=AsyncMock(side_effect=error))
    await _install_runtime(hass, bridge)
    client = await hass_client()

    response = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads",
        json=_upload_payload(),
    )

    assert response.status == status
    assert await response.json() == {
        "code": code,
        "message": "Codex Bridge request failed",
    }


@pytest.mark.parametrize(
    "payload",
    [
        _upload_payload(unexpected="value"),
        _upload_payload(sha256="A" * 64),
        _upload_payload(size_bytes=0),
        _upload_payload(filename="bad\r\nname.txt"),
    ],
)
async def test_upload_metadata_is_rejected_before_bridge_access(
    hass,
    hass_client,
    payload,
) -> None:
    bridge = SimpleNamespace(async_create_upload=AsyncMock())
    await _install_runtime(hass, bridge)
    client = await hass_client()

    response = await client.post(
        f"/api/codex_bridge/threads/{THREAD_ID}/uploads", json=payload
    )

    assert response.status == 400
    bridge.async_create_upload.assert_not_awaited()


def test_download_headers_are_allowlisted_and_hardened() -> None:
    headers = safe_download_headers(
        206,
        {
            "Content-Type": "text/html",
            "Content-Length": "4",
            "Content-Range": "bytes 2-5/10",
            "Content-Disposition": "attachment; filename=\"notes.txt\"; filename*=UTF-8''notes.txt",
            "ETag": f'"{DIGEST}"',
            "Connection": "X-Private",
            "X-Private": "secret",
            "Transfer-Encoding": "chunked",
            "Set-Cookie": "private=secret",
        },
    )

    assert headers == {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store, no-transform",
        "Content-Disposition": "attachment; filename=\"notes.txt\"; filename*=UTF-8''notes.txt",
        "Content-Length": "4",
        "Content-Range": "bytes 2-5/10",
        "Content-Type": "application/octet-stream",
        "ETag": f'"{DIGEST}"',
        "X-Content-Type-Options": "nosniff",
    }
    assert not (
        {"Connection", "Transfer-Encoding", "Set-Cookie", "X-Private"}
        & set(headers)
    )


@pytest.mark.parametrize(
    "disposition",
    [
        'inline; filename="page.html"',
        'attachment; filename="bad\r\nX-Evil: yes"',
        'attachment; filename="../../secret"',
        "attachment; filename*=UTF-8''%FF",
    ],
)
def test_malicious_download_dispositions_fall_back_safely(disposition) -> None:
    headers = safe_download_headers(
        200,
        {"Content-Disposition": disposition, "Content-Length": "0"},
    )

    assert headers["Content-Disposition"] == 'attachment; filename="download"'
    assert "\r" not in "".join(headers.values())
    assert "\n" not in "".join(headers.values())


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (200, {}),
        (206, {"Content-Length": "4"}),
        (206, {"Content-Length": "4", "Content-Range": "bytes */10"}),
        (416, {"Content-Range": "bytes 2-5/10"}),
        (200, {"Content-Length": "-1"}),
    ],
)
def test_invalid_upstream_range_metadata_fails_closed(status, headers) -> None:
    with pytest.raises(HttpStreamingError) as error:
        safe_download_headers(status, headers)

    assert error.value.status == 502
