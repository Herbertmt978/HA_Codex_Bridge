"""Community HA-MCP discovery uses only verified, private Supervisor metadata."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohasupervisor.models.addons import InstalledAddon, InstalledAddonComplete, Repository

from custom_components.codex_bridge.community_mcp_discovery import (
    CommunityMcpDiscoveryError,
    async_discover_community_mcp,
)


SECRET_PATH = "/private_synthetic_secret_path_123456"
APP_SLUG = "81f33d0f_ha_mcp"
REPOSITORY_SLUG = "81f33d0f"
REPOSITORY_URL = "https://github.com/homeassistant-ai/ha-mcp"


def _row(slug=APP_SLUG):
    return InstalledAddon.from_dict({
        "slug": slug,
        "name": "Home Assistant MCP Server",
        "repository": REPOSITORY_SLUG,
        "state": "started",
        "detached": False,
        "advanced": False,
        "available": True,
        "build": False,
        "description": "Synthetic test metadata",
        "homeassistant": None,
        "icon": False,
        "logo": False,
        "stage": "stable",
        "update_available": False,
        "url": None,
        "version_latest": "8.5.0",
        "version": "8.5.0",
    })


def _info(*, slug=APP_SLUG, host_network=True, **changes):
    values = {
        "slug": slug,
        "name": "Home Assistant MCP Server",
        "repository": REPOSITORY_SLUG,
        "detached": False,
        "advanced": False,
        "available": True,
        "build": False,
        "description": "Synthetic test metadata",
        "homeassistant": None,
        "icon": False,
        "logo": False,
        "stage": "stable",
        "update_available": False,
        "url": None,
        "version_latest": "8.5.0",
        "apparmor": "default",
        "auth_api": False,
        "docker_api": False,
        "full_access": False,
        "homeassistant_api": False,
        "host_network": host_network,
        "host_pid": False,
        "ingress": False,
        "long_description": None,
        "rating": 0,
        "signed": False,
        "hassio_api": False,
        "hassio_role": "homeassistant",
        "arch": [],
        "documentation": False,
        "state": "started",
        "options": {"secret_path": SECRET_PATH},
        "hostname": "ha-mcp",
        "ip_address": "172.30.33.4",
        "version": "8.5.0",
        "dns": [],
        "protected": False,
        "boot": "auto",
        "boot_config": "auto",
        "schema": None,
        "machine": [],
        "network": {"9583/tcp": 9583},
        "network_description": None,
        "host_ipc": False,
        "host_uts": False,
        "host_dbus": False,
        "privileged": [],
        "changelog": False,
        "stdin": False,
        "gpio": False,
        "usb": False,
        "uart": False,
        "kernel_modules": False,
        "devicetree": False,
        "udev": False,
        "video": False,
        "audio": False,
        "startup": "application",
        "services": [],
        "discovery": [],
        "translations": {},
        "webui": None,
        "ingress_entry": None,
        "ingress_url": None,
        "ingress_port": None,
        "ingress_panel": None,
        "audio_input": None,
        "audio_output": None,
        "auto_update": False,
        "watchdog": False,
        "devices": [],
        "system_managed": False,
        "system_managed_config_entry": None,
    }
    values.update(changes)
    return InstalledAddonComplete.from_dict(values)


def _repository(url=REPOSITORY_URL, slug=REPOSITORY_SLUG):
    return Repository.from_dict({
        "slug": slug,
        "name": "HA-MCP",
        "source": url,
        "url": url,
        "maintainer": "Synthetic test maintainer",
    })


@pytest.fixture
def discovery():
    addons = SimpleNamespace(list=AsyncMock(return_value=[_row()]), addon_info=AsyncMock(return_value=_info()))
    store = SimpleNamespace(repository_info=AsyncMock(return_value=_repository()))
    client = SimpleNamespace(addons=addons, store=store)
    hass = Mock()
    with (
        patch("custom_components.codex_bridge.community_mcp_discovery.get_supervisor_client", return_value=client),
        patch("custom_components.codex_bridge.community_mcp_discovery.get_url", return_value="http://ha.local:8123") as get_url,
    ):
        yield SimpleNamespace(hass=hass, addons=addons, store=store, get_url=get_url)


async def test_discovers_verified_host_network_app_without_exposing_secret(discovery):
    endpoint = await async_discover_community_mcp(discovery.hass)
    assert endpoint.slug == APP_SLUG
    assert endpoint.name == "ha-community-" + hashlib.sha256(endpoint.slug.encode()).hexdigest()[:12]
    assert endpoint.url == f"http://ha.local:9583{SECRET_PATH}"
    assert endpoint.public_destination == "http://ha.local:9583"
    assert endpoint.version == "8.5.0"
    assert len(endpoint.consent_revision) == 64
    assert SECRET_PATH not in repr(endpoint)
    discovery.get_url.assert_called_once_with(
        discovery.hass,
        prefer_external=False,
        allow_external=False,
        allow_cloud=False,
        allow_ip=True,
    )
    discovery.addons.addon_info.assert_awaited_once_with(endpoint.slug)
    discovery.store.repository_info.assert_awaited_once_with(REPOSITORY_SLUG)


@pytest.mark.parametrize(
    ("rows", "info", "code"),
    [
        ([], None, "not_installed"),
        ([_row(), _row("81f33d0f_ha_mcp_dev")], None, "ambiguous"),
        ([_row()], _info(state="stopped"), "stopped"),
        ([_row()], _info(repository="spoofed-repo-id"), "unsupported"),
        ([_row()], _info(name="Spoofed MCP"), "unsupported"),
        ([_row()], _info(slug="other_ha_mcp"), "unsupported"),
    ],
)
async def test_refuses_absent_stopped_ambiguous_and_spoofed_apps(discovery, rows, info, code):
    discovery.addons.list.return_value = rows
    if info is not None:
        discovery.addons.addon_info.return_value = info
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == code
    assert str(caught.value) in {
        "Install the community HA-MCP App before connecting it.",
        "More than one community HA-MCP App is installed. Remove the ambiguity before connecting.",
        "The community HA-MCP App is installed but is not running.",
        "The installed App metadata could not be verified.",
        "The community HA-MCP App has no usable private connection path.",
    }
    if code == "ambiguous":
        discovery.addons.addon_info.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "relative/path",
        "/private_secret?next=elsewhere",
        "/private_secret#fragment",
        "/private%2fsecret",
        "/private%5csecret",
        "/private/../secret",
        "/private/./secret",
        "/private\nsecret",
        "/private\\secret",
    ],
)
async def test_rejects_missing_or_ambiguous_secret_paths(discovery, path):
    discovery.addons.addon_info.return_value = _info(options={"secret_path": path})
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "secret_unavailable"
    assert str(caught.value) not in repr(path)


@pytest.mark.parametrize("base", [
    "http://127.0.0.1:8123",
    "http://169.254.1.2:8123",
    "http://8.8.8.8:8123",
    "http://user:pass@ha.local:8123",
    "http://ha.local:8123/?bad=1",
    "http://box.localhost:8123",
    "http://supervisor:8123",
    "http://hassio:8123",
])
async def test_host_network_rejects_unsafe_home_assistant_urls(discovery, base):
    discovery.get_url.return_value = base
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "endpoint_unavailable"


async def test_host_network_uses_private_ip_without_supervisor_container_ip(discovery):
    discovery.get_url.return_value = "http://192.168.10.4:8123"
    endpoint = await async_discover_community_mcp(discovery.hass)
    assert endpoint.url == f"http://192.168.10.4:9583{SECRET_PATH}"
    assert "172.30.33.4" not in endpoint.url


async def test_host_network_allows_private_split_dns_name(discovery):
    discovery.get_url.return_value = "http://ha.example.com:8123"
    endpoint = await async_discover_community_mcp(discovery.hass)
    assert endpoint.url == f"http://ha.example.com:9583{SECRET_PATH}"


@pytest.mark.parametrize("network", [None, {}, {"9583/tcp": None}, {"9583/tcp": 9590}])
async def test_host_network_requires_fixed_published_9583_port(discovery, network):
    discovery.addons.addon_info.return_value = _info(network=network)
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "endpoint_unavailable"


async def test_non_host_network_uses_canonical_app_hostname_and_relay_validates_dns(discovery):
    discovery.addons.addon_info.return_value = _info(host_network=False, hostname="homeassistant-ai-ha-mcp")
    endpoint = await async_discover_community_mcp(discovery.hass)
    assert endpoint.url == f"http://homeassistant-ai-ha-mcp:9583{SECRET_PATH}"
    discovery.get_url.assert_not_called()


@pytest.mark.parametrize("hostname", ["supervisor", "hassio", "ha.localhost"])
async def test_non_host_network_rejects_supervisor_and_loopback_aliases(discovery, hostname):
    discovery.addons.addon_info.return_value = _info(host_network=False, hostname=hostname)
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "endpoint_unavailable"


async def test_non_host_network_requires_canonical_private_ip_or_hostname(discovery):
    discovery.addons.addon_info.return_value = _info(
        host_network=False,
        hostname=None,
        ip_address="8.8.8.8",
    )
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "endpoint_unavailable"


async def test_repository_url_and_bounded_version_are_supported(discovery):
    discovery.addons.addon_info.return_value = _info(
        version="v8.5.0;secret",
    )
    discovery.store.repository_info.return_value = _repository(
        url="https://github.com/homeassistant-ai/ha-mcp.git",
    )
    endpoint = await async_discover_community_mcp(discovery.hass)
    assert endpoint.version is None


async def test_repository_id_is_resolved_through_supervisor_repository_api(discovery):
    await async_discover_community_mcp(discovery.hass)
    discovery.store.repository_info.assert_awaited_once_with(REPOSITORY_SLUG)


async def test_repository_slug_does_not_substitute_for_its_verified_source_url(discovery):
    discovery.store.repository_info.return_value = _repository(
        url="https://evil.example/homeassistant-ai/ha-mcp",
    )
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "unsupported"


async def test_each_call_refetches_supervisor_metadata(discovery):
    await async_discover_community_mcp(discovery.hass)
    await async_discover_community_mcp(discovery.hass)
    assert discovery.addons.list.await_count == 2
    assert discovery.addons.addon_info.await_count == 2
    assert discovery.store.repository_info.await_count == 2


async def test_upstream_failure_and_timeout_are_redacted(discovery):
    discovery.addons.list.side_effect = RuntimeError(f"upstream exposed {SECRET_PATH}")
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "unavailable"
    assert SECRET_PATH not in str(caught.value)

    discovery.addons.list.side_effect = None
    discovery.addons.list.return_value = [_row()]
    discovery.addons.addon_info.side_effect = asyncio.TimeoutError(SECRET_PATH)
    with pytest.raises(CommunityMcpDiscoveryError) as caught:
        await async_discover_community_mcp(discovery.hass)
    assert caught.value.code == "unavailable"
    assert SECRET_PATH not in repr(caught.value)
