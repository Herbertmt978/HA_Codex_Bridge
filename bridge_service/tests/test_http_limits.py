import asyncio

from codex_bridge_service.http_limits import AttachmentIngressMiddleware


def _scope(
    *headers: tuple[bytes, bytes],
    method: str = "POST",
    path: str = "/threads/thr_test/attachments",
):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
    }


def test_attachment_ingress_authenticates_before_reading_request_body() -> None:
    invoked = False
    receive_called = False
    sent = []

    async def inner(_scope, _receive, _send):
        nonlocal invoked
        invoked = True

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"payload", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = AttachmentIngressMiddleware(
        inner,
        expected_token="secret",
        max_body_bytes=10,
    )
    asyncio.run(middleware(_scope(), receive, send))

    assert invoked is False
    assert receive_called is False
    assert sent[0]["status"] == 401


def test_attachment_ingress_rejects_declared_oversize_before_body_read() -> None:
    invoked = False
    receive_called = False
    sent = []

    async def inner(_scope, _receive, _send):
        nonlocal invoked
        invoked = True

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = AttachmentIngressMiddleware(
        inner,
        expected_token="secret",
        max_body_bytes=5,
    )
    asyncio.run(
        middleware(
            _scope(
                (b"authorization", b"Bearer secret"),
                (b"content-length", b"6"),
            ),
            receive,
            send,
        )
    )

    assert invoked is False
    assert receive_called is False
    assert sent[0]["status"] == 413


def test_attachment_ingress_counts_chunked_body_before_multipart_parser() -> None:
    chunks = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    sent = []

    async def inner(_scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": ()})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = AttachmentIngressMiddleware(
        inner,
        expected_token="secret",
        max_body_bytes=5,
    )
    asyncio.run(
        middleware(
            _scope((b"authorization", b"Bearer secret")),
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 413


def test_resumable_chunk_ingress_uses_the_chunk_ceiling_before_reading() -> None:
    invoked = False
    receive_called = False
    sent = []

    async def inner(_scope, _receive, _send):
        nonlocal invoked
        invoked = True

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = AttachmentIngressMiddleware(
        inner,
        expected_token="secret",
        max_body_bytes=100,
        max_chunk_body_bytes=5,
    )
    asyncio.run(
        middleware(
            _scope(
                (b"authorization", b"Bearer secret"),
                (b"content-length", b"6"),
                method="PUT",
                path="/threads/thr_test/uploads/upl_test/chunks/0",
            ),
            receive,
            send,
        )
    )

    assert invoked is False
    assert receive_called is False
    assert sent[0]["status"] == 413
