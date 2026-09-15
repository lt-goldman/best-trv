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

from .const import (
    CARD_FILENAME,
    CARD_URL,
    CONF_ENTRY_TYPE,
    CONF_SCHEDULE_TEMPLATE_ENTRY_ID,
    CONF_TEMPLATE_PARENT_ENTRY_ID,
    DEFAULT_ENTRY_TYPE,
    DOMAIN,
    PLATFORMS_BY_ENTRY_TYPE,
)


def _platforms_for(entry: ConfigEntry) -> list[str]:
    entry_type = entry.data.get(CONF_ENTRY_TYPE, DEFAULT_ENTRY_TYPE)
    return PLATFORMS_BY_ENTRY_TYPE[entry_type]


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
    await hass.config_entries.async_forward_entry_setups(entry, _platforms_for(entry))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, _platforms_for(entry))


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-parent whatever pointed at a removed template to its own parent.

    Covers both "delete a middle template" (its children - other templates
    or rooms - move up one level) and "delete a root template" (they end up
    with no parent/template at all, i.e. the field is simply removed) with
    the exact same loop: root's "parent" is just absent, so re-parenting to
    it naturally produces "no template", which is already today's
    "empty schedule -> nothing happens" behaviour. No special case needed.

    A removed *room* has nothing pointing at it (only templates can be a
    parent, or a room's `schedule_template_entry_id` target), so this is a
    no-op for room entries.

    A pointer may live in either `data` (set at creation) or `options` (set
    later via an options flow) - `options` always wins when both are
    present, matching every other read in this integration (e.g.
    `BestTRVOptionsFlow._current_options`). Re-parenting writes through
    `options`, the same place every other post-creation change already
    lands, regardless of where the old value happened to live.
    """
    removed_parent = entry.options.get(
        CONF_TEMPLATE_PARENT_ENTRY_ID, entry.data.get(CONF_TEMPLATE_PARENT_ENTRY_ID)
    )

    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id:
            continue

        current = {**other.data, **other.options}
        keys_to_repoint = [
            key
            for key in (CONF_TEMPLATE_PARENT_ENTRY_ID, CONF_SCHEDULE_TEMPLATE_ENTRY_ID)
            if current.get(key) == entry.entry_id
        ]
        if not keys_to_repoint:
            continue

        new_options = dict(current)
        for key in keys_to_repoint:
            if removed_parent:
                new_options[key] = removed_parent
            else:
                # Root's "parent" is absent, not None - a stored `null`
                # would still satisfy a plain `.get(key)` truthiness check
                # the same way, but leaving it out is what every other
                # "cleared" pointer in this integration does (see
                # config_flow.py's _NO_PARENT handling), so a raw options
                # dump doesn't show a stray null for something that's
                # simply unset. Caught by repro_async_remove_entry.py
                # before this fix: naively storing None here passed every
                # behavioural check but left that stray null behind.
                new_options.pop(key, None)
        hass.config_entries.async_update_entry(other, options=new_options)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
