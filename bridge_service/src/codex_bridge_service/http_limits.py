from __future__ import annotations

import hmac
import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _AttachmentBodyTooLarge(Exception):
    pass


class AttachmentIngressMiddleware:
    """Authenticate and byte-bound legacy multipart before Starlette parses it."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        expected_token: str,
        max_body_bytes: int,
    ) -> None:
        if not expected_token:
            raise ValueError("expected token must not be blank")
        if type(max_body_bytes) is not int or max_body_bytes <= 0:
            raise ValueError("request body limit must be positive")
        self.app = app
        self._expected_authorization = f"Bearer {expected_token}".encode("utf-8")
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_attachment_upload(scope):
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", ())
        authorization = [
            value
            for name, value in headers
            if name.lower() == b"authorization"
        ]
        if len(authorization) != 1 or not hmac.compare_digest(
            authorization[0],
            self._expected_authorization,
        ):
            await self._send_json(send, 401, {"detail": "unauthorized"})
            return

        content_lengths = [
            value
            for name, value in headers
            if name.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await self._too_large(send)
            return
        if content_lengths:
            try:
                declared = int(content_lengths[0].decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await self._too_large(send)
                return
            if declared < 0 or declared > self._max_body_bytes:
                await self._too_large(send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if not isinstance(body, bytes):
                    raise _AttachmentBodyTooLarge()
                received += len(body)
                if received > self._max_body_bytes:
                    raise _AttachmentBodyTooLarge()
            return message

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _AttachmentBodyTooLarge:
            if not response_started:
                await self._too_large(send)

    @staticmethod
    def _is_attachment_upload(scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        path = scope.get("path")
        if not isinstance(path, str):
            return False
        parts = path.split("/")
        return (
            len(parts) == 4
            and parts[0] == ""
            and parts[1] == "threads"
            and bool(parts[2])
            and parts[3] == "attachments"
        )

    async def _too_large(self, send: Send) -> None:
        await self._send_json(
            send,
            413,
            {
                "detail": {
                    "code": "quota_exceeded",
                    "resource": "upload_request",
                    "retryable": False,
                }
            },
        )

    @staticmethod
    async def _send_json(send: Send, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": (
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"private, no-store"),
                ),
            }
        )
        await send({"type": "http.response.body", "body": body})
