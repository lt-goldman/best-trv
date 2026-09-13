"""Adapter for an Aqara E1 (SRTS-A01) exposed through Zigbee2MQTT.

Strategy 1 from the project spec: never touch valve position ourselves.
Let the Aqara's own heat-only firmware controller do the work, and steer it
by writing to the two auxiliary entities Zigbee2MQTT creates for this
device's `sensor` and `external_temperature_input` exposes:

    select.<device>_sensor                      -> must be "external"
    number.<device>_external_temperature_input  -> the value we mirror

`system_mode` (this device only has `off`/`heat`, no `cool`) is controlled
through the device's own climate entity via the standard
`climate.set_hvac_mode` service - EBT's virtual `cool` mode still leaves the
physical TRV in `heat`, just fed a mirrored temperature.

Confirmed against the Zigbee2MQTT device page for SRTS-A01:
external_temperature_input accepts 0-55 degC.

No official documentation gives an exact timeout for the on-device
fallback to the internal sensor, but it is a confirmed real-world failure
mode: at least one user reported (Z2M discussion #19357/blueprint thread)
their unit silently reverted `sensor` from `external` to `internal` after
several days with no interaction. The community mitigation that is
actually reported to work is not a fixed refresh cadence on the *number*
value, but re-asserting the `select` state on every push - which is what
`async_push_feed_temperature` does below, in addition to the periodic
forced-refresh in climate.py. Separately, writes to
external_temperature_input are known to intermittently fail with a
"Value not found" zigbee-herdsman-converters error until the device is
re-paired (Z2M issue #21397) - `async_push_feed_temperature` swallows and
logs that instead of raising, since the next control-loop tick will retry.
"""
from __future__ import annotations

import logging

from homeassistant.components.climate.const import HVACMode
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..const import DEFAULT_FEED_MAX, DEFAULT_FEED_MIN, DEFAULT_FEED_STEP

_LOGGER = logging.getLogger(__name__)

_EXTERNAL_SENSOR_OPTION = "external"


class AqaraE1Z2MAdapter:
    """See module docstring."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        climate_entity_id: str,
        external_temperature_number_entity_id: str,
        sensor_select_entity_id: str,
    ) -> None:
        self._hass = hass
        self.climate_entity_id = climate_entity_id
        self._number_entity_id = external_temperature_number_entity_id
        self._select_entity_id = sensor_select_entity_id

    @property
    def available(self) -> bool:
        # The physical TRV's own climate entity reflects real Zigbee
        # reachability - "unknown" there is as much a red flag as
        # "unavailable".
        climate_state = self._hass.states.get(self.climate_entity_id)
        if climate_state is None or climate_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False

        # The number/select entities are write targets WE command - a
        # fresh one reads "unknown" until we've ever pushed a value to it.
        # Treating that as "unavailable" would refuse to ever write the
        # first value, since the only way it stops being unknown is us
        # writing to it: a self-perpetuating deadlock confirmed on the real
        # installation. Only a missing entity or an explicit "unavailable"
        # (the underlying device is actually unreachable) blocks us here.
        for entity_id in (self._number_entity_id, self._select_entity_id):
            state = self._hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                return False
        return True

    @property
    def feed_temperature_bounds(self) -> tuple[float, float]:
        state = self._hass.states.get(self._number_entity_id)
        if state is None:
            return (DEFAULT_FEED_MIN, DEFAULT_FEED_MAX)
        attrs = state.attributes
        try:
            return (
                float(attrs.get("min", DEFAULT_FEED_MIN)),
                float(attrs.get("max", DEFAULT_FEED_MAX)),
            )
        except (TypeError, ValueError):
            return (DEFAULT_FEED_MIN, DEFAULT_FEED_MAX)

    @property
    def feed_temperature_step(self) -> float:
        state = self._hass.states.get(self._number_entity_id)
        if state is None:
            return DEFAULT_FEED_STEP
        try:
            return float(state.attributes.get("step", DEFAULT_FEED_STEP))
        except (TypeError, ValueError):
            return DEFAULT_FEED_STEP

    async def async_ensure_external_sensor_selected(self) -> None:
        """Make sure the TRV is reading `sensor: external`, not its own probe."""
        state = self._hass.states.get(self._select_entity_id)
        if state is not None and state.state == _EXTERNAL_SENSOR_OPTION:
            return
        try:
            await self._hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": self._select_entity_id, "option": _EXTERNAL_SENSOR_OPTION},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Failed to (re)select 'external' sensor on %s via %s: %s",
                self.climate_entity_id,
                self._select_entity_id,
                err,
            )

    async def async_set_enabled(self, enabled: bool) -> None:
        await self._hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {
                "entity_id": self.climate_entity_id,
                "hvac_mode": HVACMode.HEAT if enabled else HVACMode.OFF,
            },
            blocking=True,
        )

    async def async_set_setpoint(self, setpoint: float) -> None:
        # Clamp to what the physical device itself accepts - its own
        # min/max may be wider than Best TRV's configured heat/cool range
        # (e.g. climate.trv reports 5-30 degC), but
        # should never be narrower than what we'd try to push.
        state = self._hass.states.get(self.climate_entity_id)
        if state is not None:
            device_min = state.attributes.get("min_temp")
            device_max = state.attributes.get("max_temp")
            if device_min is not None:
                setpoint = max(setpoint, float(device_min))
            if device_max is not None:
                setpoint = min(setpoint, float(device_max))

        try:
            await self._hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": self.climate_entity_id, "temperature": setpoint},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Failed to sync setpoint %.1f to %s: %s", setpoint, self.climate_entity_id, err
            )

    async def async_push_feed_temperature(self, temperature: float) -> None:
        # Re-assert `sensor: external` on every push rather than only once
        # at startup - the community-observed failure mode is this select
        # silently reverting to `internal` after days of otherwise-normal
        # operation (see module docstring). This is a cheap no-op read plus,
        # only when actually needed, one extra service call.
        await self.async_ensure_external_sensor_selected()

        # climate.py already rounds to feed_temperature_step before deciding
        # whether to push at all; this round() is just float-artifact
        # cleanup (e.g. 20.700000000000003), not step enforcement.
        value = round(temperature, 3)

        try:
            await self._hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": self._number_entity_id, "value": value},
                blocking=True,
            )
        except HomeAssistantError as err:
            # Known intermittent zigbee-herdsman-converters failure on this
            # device ("Value not found") until it is re-paired. Don't crash
            # the control loop over it - the next tick retries.
            _LOGGER.warning(
                "Failed to push feed temperature %s to %s via %s: %s",
                value,
                self.climate_entity_id,
                self._number_entity_id,
                err,
            )
            return

        _LOGGER.debug(
            "Pushed feed temperature %s to %s via %s",
            value,
            self.climate_entity_id,
            self._number_entity_id,
        )
