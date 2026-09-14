"""Best TRV: reversible hydronic heating/cooling control.

A Home Assistant climate controller for setups where the same
radiator/convector/TRV is fed either hot or cold water by a heat pump
changeover, and the TRV's own firmware only understands heating logic.
See README.md for the mirrored-temperature approach and architecture.
"""
from __future__ import annotations

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILENAME, CARD_URL, DOMAIN, PLATFORMS


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the bundled schedule card as a self-registering Lovelace resource.

    Runs exactly once per Home Assistant process - unlike async_setup_entry
    below, which runs once per room/config entry, so registering the same
    static path there would risk a duplicate-registration error the moment
    a second room is configured.
    """
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                hass.config.path(f"custom_components/{DOMAIN}/www/{CARD_FILENAME}"),
                cache_headers=False,
            )
        ]
    )
    add_extra_js_url(hass, CARD_URL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
