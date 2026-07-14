from collections.abc import Awaitable, Callable
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer


@pytest_asyncio.fixture
async def bridge_server_factory(socket_enabled: None):
    """Start short-lived Bridge test servers and close them with the test loop."""

    servers: list[TestServer] = []

    async def _create(
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> TestServer:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return server

    yield _create

    for server in servers:
        await server.close()
