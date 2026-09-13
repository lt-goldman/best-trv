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
    DOMAIN,
    MAX_SCHEDULE_SLOTS_PER_DAY,
)
from .controller import DAY_KEYS

_LOGGER = logging.getLogger(__name__)

_DAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}
_COPY_FROM_NONE = "none"


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
    """Tune tolerances/timing, and the schedule, after setup.

    A menu rather than one giant form: "Tuning" holds the original
    heat/cool/timing fields, "Schedule on/off" is a single toggle, and each
    weekday is its own step with up to MAX_SCHEDULE_SLOTS_PER_DAY (time,
    temperature) pairs. Saving any one step applies immediately (each
    `async_create_entry` closes this dialog per HA's options-flow model) -
    setting up a full week means reopening "Configure" once per day, a
    known trade-off of the plain-form approach chosen over a custom
    Lovelace card (see README).
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _current_options(self) -> dict[str, Any]:
        return {**self._config_entry.data, **self._config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["tuning", "schedule_toggle", *[f"schedule_{d}" for d in DAY_KEYS]],
        )

    async def async_step_tuning(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        current = self._current_options()
        if user_input is not None:
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

    # -- one step per weekday, all delegating to the same handler ----------

    async def async_step_schedule_mon(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("mon", user_input)

    async def async_step_schedule_tue(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("tue", user_input)

    async def async_step_schedule_wed(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("wed", user_input)

    async def async_step_schedule_thu(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("thu", user_input)

    async def async_step_schedule_fri(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("fri", user_input)

    async def async_step_schedule_sat(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("sat", user_input)

    async def async_step_schedule_sun(self, user_input=None) -> FlowResult:
        return await self._async_step_schedule_day("sun", user_input)

    async def _async_step_schedule_day(
        self, day_key: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        current = self._current_options()
        existing_slots: list[dict[str, Any]] = current.get(CONF_SCHEDULE, {}).get(day_key, [])

        if user_input is not None:
            copy_from = user_input.get("copy_from_day", _COPY_FROM_NONE)
            if copy_from != _COPY_FROM_NONE:
                # Copying a whole day's program takes priority over whatever
                # is in the slot fields for this same submission - the user
                # picked a source day precisely to avoid re-typing it.
                new_slots = list(current.get(CONF_SCHEDULE, {}).get(copy_from, []))
            else:
                new_slots = [
                    {
                        "time": user_input[f"slot{i}_time"],
                        "temperature": user_input[f"slot{i}_temperature"],
                    }
                    for i in range(MAX_SCHEDULE_SLOTS_PER_DAY)
                    if user_input.get(f"slot{i}_time") is not None
                    and user_input.get(f"slot{i}_temperature") is not None
                ]
            schedule = dict(current.get(CONF_SCHEDULE, {}))
            schedule[day_key] = new_slots
            return self.async_create_entry(title="", data={**current, CONF_SCHEDULE: schedule})

        schema_dict: dict[Any, Any] = {
            vol.Optional("copy_from_day", default=_COPY_FROM_NONE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=_COPY_FROM_NONE, label="Don't copy"),
                        *[
                            selector.SelectOptionDict(value=d, label=f"Copy from {label}")
                            for d, label in _DAY_LABELS.items()
                            if d != day_key
                        ],
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
        for i in range(MAX_SCHEDULE_SLOTS_PER_DAY):
            existing = existing_slots[i] if i < len(existing_slots) else None
            time_key = (
                vol.Optional(f"slot{i}_time", default=existing["time"])
                if existing
                else vol.Optional(f"slot{i}_time")
            )
            temp_key = (
                vol.Optional(f"slot{i}_temperature", default=existing["temperature"])
                if existing
                else vol.Optional(f"slot{i}_temperature")
            )
            schema_dict[time_key] = selector.TimeSelector()
            schema_dict[temp_key] = vol.Coerce(float)

        return self.async_show_form(
            step_id=f"schedule_{day_key}", data_schema=vol.Schema(schema_dict)
        )
