"""Config flow for Best TRV."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_CHANGEOVER_SENSOR,
    CONF_COOL_MAX_TEMP,
    CONF_COOL_MIN_TEMP,
    CONF_COOL_STEP,
    CONF_COOLING_ACTIVE_VALUE,
    CONF_DEBOUNCE_SECONDS,
    CONF_EXTERNAL_TEMP_NUMBER,
    CONF_FORCED_REFRESH_SECONDS,
    CONF_HEAT_MAX_TEMP,
    CONF_HEAT_MIN_TEMP,
    CONF_HEAT_STEP,
    CONF_MIN_DELTA,
    CONF_ROOM_SENSOR,
    CONF_SCHEDULE,
    CONF_SCHEDULE_ENABLED,
    CONF_SENSOR_SELECT,
    CONF_SUSPEND_ACTIVE_VALUE,
    CONF_SUSPEND_SENSOR,
    CONF_TRV_CLIMATE_ENTITIES,
    CONF_TRV_MAPPING,
    DEFAULT_COOL_MAX_TEMP,
    DEFAULT_COOL_MIN_TEMP,
    DEFAULT_COOL_STEP,
    DEFAULT_COOLING_ACTIVE_VALUE,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_FORCED_REFRESH_SECONDS,
    DEFAULT_HEAT_MAX_TEMP,
    DEFAULT_HEAT_MIN_TEMP,
    DEFAULT_HEAT_STEP,
    DEFAULT_MIN_DELTA,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SUSPEND_ACTIVE_VALUE,
    DEFAULT_SUSPEND_SENSOR,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _guess_aux_entities(
    hass: HomeAssistant, climate_entity_id: str
) -> tuple[str | None, str | None]:
    """Guess the Zigbee2MQTT-generated number/select entity ids for a TRV.

    Zigbee2MQTT names auxiliary entities after the device's object_id, e.g.
    climate.trv ->
        number.trv_external_temperature_input
        select.trv_sensor

    Only offered as a default if that entity actually exists right now;
    otherwise the field is left for the user to pick manually (renamed
    entities, a different integration, exposes not enabled in Z2M yet).
    """
    object_id = climate_entity_id.split(".", 1)[-1]
    guessed_number = f"number.{object_id}_external_temperature_input"
    guessed_select = f"select.{object_id}_sensor"
    number = guessed_number if hass.states.get(guessed_number) else None
    select = guessed_select if hass.states.get(guessed_select) else None
    return number, select


class BestTRVConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Best TRV. One entry = one room."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._trv_entities: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._trv_entities = list(user_input[CONF_TRV_CLIMATE_ENTITIES])
            if not self._trv_entities:
                errors["base"] = "no_trvs_selected"
            else:
                self._data.update(user_input)
                return await self.async_step_trv_mapping()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_TRV_CLIMATE_ENTITIES): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate", multiple=True)
                ),
                vol.Required(CONF_ROOM_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_CHANGEOVER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_COOLING_ACTIVE_VALUE, default=DEFAULT_COOLING_ACTIVE_VALUE
                ): str,
                # An EntitySelector rejects "" as an actual value (it's
                # validated as an entity ID/UUID, and "" is neither) - so
                # unlike every plain-string field above, this can't use
                # `default=""`. `description={"suggested_value": ...}` is
                # HA's own pattern for "start this field empty/optional":
                # it only affects what the form initially shows, and if
                # left blank the key is simply absent from user_input
                # rather than submitted as an invalid empty string.
                vol.Optional(
                    CONF_SUSPEND_SENSOR, description={"suggested_value": ""}
                ): selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(
                    CONF_SUSPEND_ACTIVE_VALUE, default=DEFAULT_SUSPEND_ACTIVE_VALUE
                ): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_trv_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            mapping: dict[str, dict[str, str]] = {}
            for idx, climate_entity_id in enumerate(self._trv_entities):
                mapping[climate_entity_id] = {
                    CONF_EXTERNAL_TEMP_NUMBER: user_input[f"number_{idx}"],
                    CONF_SENSOR_SELECT: user_input[f"select_{idx}"],
                }
            self._data[CONF_TRV_MAPPING] = mapping
            self._data.setdefault(CONF_HEAT_MIN_TEMP, DEFAULT_HEAT_MIN_TEMP)
            self._data.setdefault(CONF_HEAT_MAX_TEMP, DEFAULT_HEAT_MAX_TEMP)
            self._data.setdefault(CONF_HEAT_STEP, DEFAULT_HEAT_STEP)
            self._data.setdefault(CONF_COOL_MIN_TEMP, DEFAULT_COOL_MIN_TEMP)
            self._data.setdefault(CONF_COOL_MAX_TEMP, DEFAULT_COOL_MAX_TEMP)
            self._data.setdefault(CONF_COOL_STEP, DEFAULT_COOL_STEP)
            self._data.setdefault(CONF_MIN_DELTA, DEFAULT_MIN_DELTA)
            self._data.setdefault(
                CONF_FORCED_REFRESH_SECONDS, DEFAULT_FORCED_REFRESH_SECONDS
            )
            self._data.setdefault(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
            self._data.setdefault(CONF_SCHEDULE_ENABLED, DEFAULT_SCHEDULE_ENABLED)
            self._data.setdefault(CONF_SCHEDULE, {})
            # Absent (not "") when the suspend-sensor field on the previous
            # step was left blank - see that field's own comment.
            self._data.setdefault(CONF_SUSPEND_SENSOR, DEFAULT_SUSPEND_SENSOR)
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        schema_dict: dict[Any, Any] = {}
        for idx, climate_entity_id in enumerate(self._trv_entities):
            guessed_number, guessed_select = _guess_aux_entities(
                self.hass, climate_entity_id
            )
            number_key = (
                vol.Required(f"number_{idx}", default=guessed_number)
                if guessed_number
                else vol.Required(f"number_{idx}")
            )
            select_key = (
                vol.Required(f"select_{idx}", default=guessed_select)
                if guessed_select
                else vol.Required(f"select_{idx}")
            )
            schema_dict[number_key] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number")
            )
            schema_dict[select_key] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="select")
            )

        return self.async_show_form(
            step_id="trv_mapping",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"trv_count": str(len(self._trv_entities))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BestTRVOptionsFlow:
        return BestTRVOptionsFlow(config_entry)


class BestTRVOptionsFlow(config_entries.OptionsFlow):
    """Tune tolerances/timing, and the schedule on/off switch, after setup.

    A menu rather than one giant form: "Tuning" holds the heat/cool/timing
    fields (plus the stand-down sensor), "Schedule on/off" is a single
    toggle. The weekly schedule itself is no longer edited here - the
    dashboard card (custom_components/best_trv/www/) is the one editing
    surface for it now, both reading and writing the exact same `schedule`
    config-entry key this options flow used to expose as one form per
    weekday. That per-day wizard was removed once the card existed:
    having two different places to edit the same thing, one of them
    considerably more cumbersome, was confusing rather than useful.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _current_options(self) -> dict[str, Any]:
        return {**self._config_entry.data, **self._config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["tuning", "schedule_toggle"],
        )

    async def async_step_tuning(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        current = self._current_options()
        if user_input is not None:
            # The suspend-sensor field is offered via suggested_value, not
            # a schema default (see that field below) - clearing it in the
            # form leaves the key absent from user_input entirely, rather
            # than submitted as "". Without normalizing that here, a plain
            # {**current, **user_input} merge would just silently keep
            # whatever was previously configured instead of actually
            # clearing it.
            user_input.setdefault(CONF_SUSPEND_SENSOR, DEFAULT_SUSPEND_SENSOR)
            return self.async_create_entry(title="", data={**current, **user_input})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HEAT_MIN_TEMP, default=current[CONF_HEAT_MIN_TEMP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_HEAT_MAX_TEMP, default=current[CONF_HEAT_MAX_TEMP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_HEAT_STEP, default=current[CONF_HEAT_STEP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_COOL_MIN_TEMP, default=current[CONF_COOL_MIN_TEMP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_COOL_MAX_TEMP, default=current[CONF_COOL_MAX_TEMP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_COOL_STEP, default=current[CONF_COOL_STEP]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_MIN_DELTA, default=current[CONF_MIN_DELTA]
                ): vol.Coerce(float),
                vol.Required(
                    CONF_FORCED_REFRESH_SECONDS,
                    default=current[CONF_FORCED_REFRESH_SECONDS],
                ): vol.Coerce(int),
                vol.Required(
                    CONF_DEBOUNCE_SECONDS, default=current[CONF_DEBOUNCE_SECONDS]
                ): vol.Coerce(int),
                vol.Required(
                    CONF_COOLING_ACTIVE_VALUE,
                    default=current[CONF_COOLING_ACTIVE_VALUE],
                ): str,
                # See the identical field in BestTRVConfigFlow.async_step_user
                # for why this can't use `default=""` the way every field
                # above it does.
                vol.Optional(
                    CONF_SUSPEND_SENSOR,
                    description={
                        "suggested_value": current.get(CONF_SUSPEND_SENSOR, DEFAULT_SUSPEND_SENSOR)
                    },
                ): selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(
                    CONF_SUSPEND_ACTIVE_VALUE,
                    default=current.get(CONF_SUSPEND_ACTIVE_VALUE, DEFAULT_SUSPEND_ACTIVE_VALUE),
                ): str,
            }
        )
        return self.async_show_form(step_id="tuning", data_schema=schema)

    async def async_step_schedule_toggle(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = self._current_options()
        if user_input is not None:
            return self.async_create_entry(title="", data={**current, **user_input})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCHEDULE_ENABLED,
                    default=current.get(CONF_SCHEDULE_ENABLED, DEFAULT_SCHEDULE_ENABLED),
                ): bool
            }
        )
        return self.async_show_form(step_id="schedule_toggle", data_schema=schema)
