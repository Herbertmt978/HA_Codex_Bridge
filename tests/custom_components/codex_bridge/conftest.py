from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.const import DOMAIN


@pytest.fixture
def install_runtime(hass: HomeAssistant) -> Callable[[object], MockConfigEntry]:
    """Expose a stub runtime the way a loaded config entry does."""

    def _install(runtime: object) -> MockConfigEntry:
        entry = MockConfigEntry(domain=DOMAIN, title="Codex Bridge")
        entry.add_to_hass(hass)
        entry.runtime_data = runtime
        entry.mock_state(hass, ConfigEntryState.LOADED)
        return entry

    return _install


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
