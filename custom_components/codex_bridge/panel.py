from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import frontend, panel_custom
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from homeassistant.components.http.server import StaticPathConfig
else:
    # Every supported release re-exports it here; HA 2026.9 moved its definition.
    from homeassistant.components.http import StaticPathConfig

from .const import (
    PANEL_COMPONENT_NAME,
    PANEL_ICON,
    PANEL_MODULE_URL,
    PANEL_URL_PATH,
    STATIC_URL_BASE,
)


async def async_register_panel(hass: HomeAssistant, sidebar_title: str) -> None:
    frontend_path = Path(__file__).parent / "frontend"
    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL_BASE,
                    str(frontend_path),
                    False,
                )
            ]
        )
    except RuntimeError:
        pass

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_COMPONENT_NAME,
        frontend_url_path=PANEL_URL_PATH,
        module_url=PANEL_MODULE_URL,
        sidebar_title=sidebar_title,
        sidebar_icon=PANEL_ICON,
        config={"panel_path": PANEL_URL_PATH},
        require_admin=True,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
