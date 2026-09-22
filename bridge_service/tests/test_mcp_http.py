import httpx
import pytest

from codex_bridge_service.mcp_http import McpHttpBoundary


@pytest.mark.asyncio
async def test_mcp_boundary_limits_chunked_body_and_sends_no_store():
    calls = []
    async def app(scope, receive, send):
        calls.append((await receive())["body"])
        await send({"type":"http.response.start", "status":200, "headers":[]})
        await send({"type":"http.response.body", "body":b'{}'})
    async def chunks():
        yield b'"synthetic-secret"'
        yield b'x' * (24 * 1024)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=McpHttpBoundary(app)), base_url="http://bridge") as client:
        rejected = await client.post("/mcp/servers", content=chunks())
        assert rejected.status_code == 413
        assert rejected.headers["cache-control"] == "no-store"
        assert "synthetic-secret" not in rejected.text
        assert not calls
        accepted = await client.put("/mcp/servers/test/credential", json={"test":True})
        assert accepted.status_code == 200
        assert accepted.headers["cache-control"] == "no-store"
        assert calls == [b'{"test":true}']
