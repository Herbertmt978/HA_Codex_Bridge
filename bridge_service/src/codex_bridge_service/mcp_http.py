"""Bound secret-bearing MCP requests before JSON parsing; never echo input."""

import asyncio


class McpHttpBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp/"):
            return await self.app(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]}
            await send(message)

        if scope["method"] not in {"POST", "PUT"}:
            return await self.app(scope, receive, no_store)
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 24 * 1024:
                        raise ValueError()
                    if not message.get("more_body", False):
                        break
        except (ValueError, TimeoutError):
            await no_store({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":{"code":"mcp_request_invalid","retryable":false}}'})
            return
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, no_store)
