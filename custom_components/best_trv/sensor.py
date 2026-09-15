"""Schedule template platform for Best TRV.

A "template" config entry (see `const.CONF_ENTRY_TYPE`) has no TRVs of its
own - it exists purely so a named, shareable schedule can be edited (via
the same dashboard card that edits a room's own schedule, see
`www/best-trv-schedule-card.js`) and inherited by one or more rooms, or by
other templates further down the chain. See
`controller.resolve_effective_schedule_raw` for how a chain of these is
merged into one effective schedule, and `climate.py` for where a room
walks that chain on every control tick.

Deliberately a `sensor`, not a `climate` entity: a template isn't a
thermostat, and putting it on the climate platform would make it show up
as a confusing, non-functional thermostat card on every auto-generated
area dashboard. A plain sensor - state is just its own name - carries the
`schedule` attribute the card needs without pretending to be a room.

Entity services here (`set_template_day`/`set_template_days`) are
deliberately named differently from the room platform's
`set_schedule_day`/`set_schedule_days` in climate.py, even though the
field shapes are identical: confirmed directly against the installed
`homeassistant` package (see plan) that `entity_platform.async_register_entity_service`
only shares a service across multiple config entries of the *same* entity
domain - a second, different entity domain (sensor vs. climate)
registering the same service name is silently ignored
(`EntityPlatform.async_register_entity_service` no-ops once
`hass.services.has_service(...)` is already true), so reusing the room's
service names here would leave template entities unreachable through them.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate import _SLOT_SCHEMA
from .const import CONF_SCHEDULE, CONF_TEMPLATE_PARENT_ENTRY_ID, DOMAIN
from .controller import DAY_KEYS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([BestTrvScheduleTemplate(hass, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_template_day",
        {vol.Required("day"): vol.In(DAY_KEYS), vol.Required("slots"): _SLOT_SCHEMA},
        "async_set_template_day",
    )
    platform.async_register_entity_service(
        "set_template_days",
        {vol.Required("days"): [vol.In(DAY_KEYS)], vol.Required("slots"): _SLOT_SCHEMA},
        "async_set_template_days",
    )


class BestTrvScheduleTemplate(SensorEntity):
    """A named, shareable schedule node - see module docstring."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Best TRV",
            model="Schedule template",
        )
        # Same raw/parsed split rationale as BestTRV in climate.py: this is
        # the one source both the card's "schedule" attribute and any
        # inheriting room/template's tree walk read from - no separate
        # "serialize back to a dict" path to drift out of sync.
        self._schedule_raw: dict[str, list[dict[str, Any]]] = dict(
            entry.options.get(CONF_SCHEDULE, entry.data.get(CONF_SCHEDULE, {}))
        )

    @property
    def native_value(self) -> str:
        # entry.title, not entry.data[CONF_NAME]: a rename (via
        # BestTRVTemplateOptionsFlow in config_flow.py) updates the entry's
        # title directly, the same single source of truth _attr_device_info
        # below already uses - data[CONF_NAME] is only ever the value from
        # creation time and would go stale after a rename.
        return self._entry.title

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "schedule": self._schedule_raw,
            "template_parent_entry_id": self._entry.options.get(
                CONF_TEMPLATE_PARENT_ENTRY_ID,
                self._entry.data.get(CONF_TEMPLATE_PARENT_ENTRY_ID),
            ),
        }

    async def async_set_template_day(self, day: str, slots: list[dict[str, Any]]) -> None:
        """Replace one day's schedule slots (called by the schedule card)."""
        await self._async_write_template_days({day: slots})

    async def async_set_template_days(
        self, days: list[str], slots: list[dict[str, Any]]
    ) -> None:
        """Apply the same slot list to several days in one atomic write.

        Same atomicity rationale as `BestTRV.async_set_schedule_days` in
        climate.py: one config-entry write (and therefore one reload) for
        the whole change, not one per day.
        """
        await self._async_write_template_days({day: slots for day in days})

    async def _async_write_template_days(
        self, updates: dict[str, list[dict[str, Any]]]
    ) -> None:
        raw_schedule = dict(self._schedule_raw)
        for day, slots in updates.items():
            raw_schedule[day] = [
                {"time": slot["time"].isoformat(), "temperature": slot["temperature"]}
                for slot in slots
            ]
        new_options = {**self._entry.data, **self._entry.options, CONF_SCHEDULE: raw_schedule}

        # Update our own state first so the card sees the change instantly
        # - same reasoning as BestTRV._async_write_schedule_days in
        # climate.py. Anything inheriting from this template picks up the
        # change on its own next control tick by reading straight from
        # this entry's config data (see controller.resolve_effective_schedule_raw
        # and climate.py's per-tick chain walk) - not from this entity's
        # state, which exists for the card, not for the control loop.
        self._schedule_raw = raw_schedule
        self.async_write_ha_state()
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
