"""Configuration-flow coverage for the Supervisor-native Codex Bridge."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiIncompatibleError,
)
from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_UUID,
    CONF_WEB_SEARCH_MODE,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DOMAIN,
)
from custom_components.codex_bridge.config_flow import (
    CodexBridgeConfigFlow,
    CodexBridgeOptionsFlow,
)


TOKEN = "a" * 48
UUID = "0123456789abcdef0123456789abcdef"
REPOSITORY_ROOT = Path(__file__).parents[3]


def _flow(hass, source: str) -> CodexBridgeConfigFlow:
    flow = CodexBridgeConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test-flow"
    flow.context = {"source": source}
    return flow


def _discovery(*, host: str = "172.30.32.5", token: str = TOKEN) -> HassioServiceInfo:
    return HassioServiceInfo(
        config={
            "source": "attacker",
            "service": "attacker",
            "slug": "attacker",
            "uuid": "f" * 32,
            "publication_id": "attacker-publication",
            "host": host,
            "port": 8766,
            "token": token,
            "api": {"minimum": 1, "maximum": 1},
        },
        name="Codex Bridge App",
        slug="local_codex_bridge",
        uuid=UUID,
    )


@pytest.mark.usefixtures("socket_enabled")
async def test_hassio_discovery_uses_wrapper_identity_and_creates_safe_entry(hass):
    """Supervisor wrapper identity must override untrusted App payload fields."""
    client = AsyncMock()
    client.async_ready.return_value = object()
    client.negotiated_api_version = 1
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        flow = _flow(hass, "hassio")
        discovered = await flow.async_step_hassio(_discovery())
        result = await flow.async_step_hassio_confirm({})

    assert discovered["type"] is FlowResultType.FORM
    assert discovered["step_id"] == "hassio_confirm"
    assert not discovered["data_schema"].schema
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Codex Bridge App"
    assert result["data"] == {
        CONF_BRIDGE_URL: "http://172.30.32.5:8766",
        CONF_BRIDGE_TOKEN: TOKEN,
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
        CONF_DISCOVERY_UUID: UUID,
    }
    assert client.async_ready.await_count == 2
    assert client.async_ready.await_args.kwargs["discovery"].uuid == UUID


async def test_supervisor_options_use_live_by_default_and_only_accept_live_or_off(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Codex Bridge App",
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR},
        options={},
    )
    entry.add_to_hass(hass)
    flow = CodexBridgeOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id

    form = await flow.async_step_init()
    assert form["type"] is FlowResultType.FORM
    assert form["data_schema"]({}) == {CONF_WEB_SEARCH_MODE: "live"}
    result = await flow.async_step_init({CONF_WEB_SEARCH_MODE: "disabled"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_WEB_SEARCH_MODE: "disabled"}


async def test_supervisor_options_remain_available_before_login_capability_recovery(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Codex Bridge App",
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR},
    )
    entry.add_to_hass(hass)
    flow = CodexBridgeOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_WEB_SEARCH_MODE: "live"}


async def test_external_legacy_entry_has_no_native_web_search_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="External Codex Bridge",
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_EXTERNAL_LEGACY},
    )
    entry.add_to_hass(hass)
    flow = CodexBridgeOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "supervisor_only"


@pytest.mark.parametrize(
    ("host", "port", "token"),
    [
        ("8.8.8.8", 8766, TOKEN),
        ("127.0.0.1", 8766, TOKEN),
        ("local-codex-bridge", 8766, TOKEN),
        ("169.254.1.1", 8766, TOKEN),
        ("172.30.32.5", 70000, TOKEN),
        ("172.30.32.5", 8766, "short"),
    ],
)
async def test_hassio_discovery_rejects_invalid_endpoint_without_creating_entry(
    hass, host, port, token
):
    discovery = _discovery(host=host, token=token)
    discovery.config["port"] = port

    result = await _flow(hass, "hassio").async_step_hassio(discovery)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"


async def test_hassio_discovery_rejects_an_unsupported_api_range(hass):
    discovery = _discovery()
    discovery.config["api"] = {"minimum": 2, "maximum": 2}

    result = await _flow(hass, "hassio").async_step_hassio(discovery)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "incompatible_api"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (BridgeApiAuthError(), "invalid_auth"),
        (BridgeApiIncompatibleError(), "incompatible_api"),
    ],
)
async def test_hassio_discovery_requires_authenticated_compatible_ready(
    hass, error, reason
):
    client = AsyncMock()
    client.async_ready.side_effect = error
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        result = await _flow(hass, "hassio").async_step_hassio(_discovery())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_hassio_discovery_connection_failure_shows_confirm_and_retries(
    hass,
):
    client = AsyncMock()
    client.async_ready.side_effect = [
        BridgeApiConnectionError(),
        BridgeApiConnectionError(),
        None,
    ]
    client.negotiated_api_version = 1
    flow = _flow(hass, "hassio")
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        discovered = await flow.async_step_hassio(_discovery())
        retry_failed = await flow.async_step_hassio_confirm({})
        assert not hass.config_entries.async_entries(DOMAIN)
        result = await flow.async_step_hassio_confirm({})

    assert discovered["type"] is FlowResultType.FORM
    assert discovered["step_id"] == "hassio_confirm"
    assert discovered["errors"] == {"base": "cannot_connect"}
    assert retry_failed["type"] is FlowResultType.FORM
    assert retry_failed["errors"] == {"base": "cannot_connect"}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BRIDGE_TOKEN] == TOKEN
    assert client.async_ready.await_count == 3


async def test_hassio_connection_failure_does_not_update_existing_entry_until_retry(
    hass,
):
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Codex Bridge App",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "e" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: UUID,
        },
        source="hassio",
        unique_id=UUID,
    )
    entry.add_to_hass(hass)
    rotated_token = "b" * 48
    client = AsyncMock()
    client.async_ready.side_effect = [BridgeApiConnectionError(), None]
    client.negotiated_api_version = 1
    flow = _flow(hass, "hassio")
    with (
        patch(
            "custom_components.codex_bridge.config_flow.BridgeApiClient",
            return_value=client,
        ),
        patch.object(flow, "async_update_reload_and_abort") as update_reload,
    ):
        discovered = await flow.async_step_hassio(_discovery(token=rotated_token))
        assert discovered["type"] is FlowResultType.FORM
        assert update_reload.call_count == 0
        assert entry.data[CONF_BRIDGE_TOKEN] == "e" * 48
        result = await flow.async_step_hassio_confirm({})

    assert result is update_reload.return_value
    assert update_reload.call_args.args[0] is entry
    assert update_reload.call_args.kwargs["data"][CONF_BRIDGE_TOKEN] == rotated_token


async def test_hassio_confirmation_revalidates_cached_credentials(hass):
    client = AsyncMock()
    client.async_ready.side_effect = [None, BridgeApiAuthError()]
    client.negotiated_api_version = 1
    flow = _flow(hass, "hassio")
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        discovered = await flow.async_step_hassio(_discovery())
        result = await flow.async_step_hassio_confirm({})

    assert discovered["type"] is FlowResultType.FORM
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_auth"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_user_flow_guides_to_app_and_keeps_external_advanced(hass):
    flow = _flow(hass, "user")
    result = await flow.async_step_user()
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["app", "external"]

    app = await flow.async_step_app()
    assert app["type"] is FlowResultType.FORM
    app = await flow.async_step_app({})
    assert app["type"] is FlowResultType.FORM
    assert app["errors"]["base"] == "app_not_discovered"


async def test_external_flow_accepts_only_explicitly_negotiated_v0(hass):
    flow = _flow(hass, "user")
    external = await flow.async_step_external()
    assert external["type"] is FlowResultType.FORM
    client = AsyncMock()
    client.negotiated_api_version = 1
    client.async_ready.return_value = object()
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        incompatible = await flow.async_step_external(
            {
                CONF_BRIDGE_URL: "http://127.0.0.1:8766",
                CONF_BRIDGE_TOKEN: TOKEN,
            }
        )
    assert incompatible["errors"]["base"] == "incompatible_api"

    client.negotiated_api_version = 0
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        result = await flow.async_step_external(
            {
                CONF_BRIDGE_URL: "http://127.0.0.1:8766",
                CONF_BRIDGE_TOKEN: TOKEN,
            }
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION_TYPE] == CONNECTION_TYPE_EXTERNAL_LEGACY
    assert result["data"].get(CONF_DISCOVERY_UUID) is None


async def test_discovered_app_replaces_external_only_after_confirmation(hass):
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="External Codex Bridge",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "e" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_EXTERNAL_LEGACY,
        },
        source="user",
        unique_id=f"{DOMAIN}:external",
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_ready.return_value = object()
    client.negotiated_api_version = 1
    flow = _flow(hass, "hassio")
    with (
        patch(
            "custom_components.codex_bridge.config_flow.BridgeApiClient",
            return_value=client,
        ),
        patch.object(flow, "async_update_reload_and_abort") as update_reload,
    ):
        discovered = await flow.async_step_hassio(_discovery())
        assert discovered["type"] is FlowResultType.FORM
        assert update_reload.call_count == 0
        result = await flow.async_step_hassio_confirm({})

    assert result is update_reload.return_value
    assert client.async_ready.await_count == 2
    assert update_reload.call_args.args[0] is entry
    assert update_reload.call_args.kwargs["unique_id"] == UUID
    assert (
        update_reload.call_args.kwargs["data"][CONF_CONNECTION_TYPE]
        == CONNECTION_TYPE_SUPERVISOR
    )


async def test_discovery_does_not_replace_a_different_supervisor_instance(hass):
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Another Codex Bridge App",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "d" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: "f" * 32,
        },
        source="hassio",
        unique_id="f" * 32,
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_ready.return_value = object()
    client.negotiated_api_version = 1
    with patch(
        "custom_components.codex_bridge.config_flow.BridgeApiClient",
        return_value=client,
    ):
        result = await _flow(hass, "hassio").async_step_hassio(_discovery())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_external_flow_refuses_a_second_configured_connection(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Codex Bridge App",
        data={},
        unique_id=UUID,
    )
    entry.add_to_hass(hass)

    result = await _flow(hass, "user").async_step_external()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_hassio_rotation_updates_and_reloads_without_exposing_token(hass, caplog):
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Codex Bridge App",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: TOKEN,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: UUID,
        },
        source="hassio",
        unique_id=UUID,
    )
    entry.add_to_hass(hass)
    rotated_token = "b" * 48
    client = AsyncMock()
    client.async_ready.return_value = object()
    client.negotiated_api_version = 1
    flow = _flow(hass, "hassio")
    with (
        patch(
            "custom_components.codex_bridge.config_flow.BridgeApiClient",
            return_value=client,
        ),
        patch.object(flow, "async_update_reload_and_abort") as update_reload,
    ):
        result = await flow.async_step_hassio(_discovery(token=rotated_token))

    assert result is update_reload.return_value
    assert update_reload.call_args.kwargs["data"][CONF_BRIDGE_TOKEN] == rotated_token
    assert update_reload.call_args.args[0] is entry
    assert update_reload.call_args.kwargs["reload_even_if_entry_is_unchanged"] is False
    assert TOKEN not in caplog.text
    assert rotated_token not in caplog.text


def test_manifest_and_english_localisation_describe_the_supervisor_flow() -> None:
    integration_root = REPOSITORY_ROOT / "custom_components" / DOMAIN
    manifest = json.loads((integration_root / "manifest.json").read_text("utf-8"))
    strings = json.loads((integration_root / "strings.json").read_text("utf-8"))
    translation = json.loads(
        (integration_root / "translations" / "en.json").read_text("utf-8")
    )

    assert manifest["after_dependencies"] == ["hassio"]
    assert strings == translation
    assert "hassio_confirm" in strings["config"]["step"]
    assert strings["config"]["step"]["user"]["menu_options"] == {
        "app": "Home Assistant App (recommended)",
        "external": "External Bridge (advanced)",
    }
