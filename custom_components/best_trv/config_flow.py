"""Config flow for Best TRV."""
from __future__ import annotations

import logging
from datetime import time as time_of_day
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
from .controller import DAY_KEYS, parse_time_string

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
        # Working buffer for whichever day's schedule is currently being
        # edited - lets "add another slot" loop the same step repeatedly
        # (growing the list one slot at a time) before the user finalizes.
        self._working_day_key: str | None = None
        self._working_day_slots: list[dict[str, Any]] = []

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
        """One weekday's schedule, with dynamic slot growth and fan-out.

        Only the already-confirmed slots are ever shown as fields (blank a
        slot's time to delete it) - there is no extra, always-visible
        "next slot" pair inviting confusion about whether it's real yet.
        Checking "Add another slot" and submitting appends one new slot
        (time an hour after the last one, temperature copied from it, both
        then freely editable) and re-shows this same step with it as a
        genuine row - nothing appears until that checkbox is actually used.
        "Copy from" pulls another day's whole program in one shot; "push
        to" fans this day's resulting program out to other days in the
        same submission - setting up several identical days no longer
        means visiting each one individually.
        """
        current = self._current_options()

        if self._working_day_key != day_key:
            # Fresh visit to this day - seed the working list from what's
            # already configured (empty for a never-touched day).
            self._working_day_key = day_key
            self._working_day_slots = list(current.get(CONF_SCHEDULE, {}).get(day_key, []))

        # How many (time, temperature) field pairs the form we're now
        # responding to actually showed - exactly one per existing slot,
        # no trailing extra.
        shown_count = len(self._working_day_slots)

        if user_input is not None:
            copy_from = user_input.get("copy_from_day", _COPY_FROM_NONE)
            push_to_days: list[str] = user_input.get("push_to_days") or []

            if copy_from != _COPY_FROM_NONE:
                self._working_day_slots = list(
                    current.get(CONF_SCHEDULE, {}).get(copy_from, [])
                )
            else:
                self._working_day_slots = [
                    {
                        "time": user_input[f"slot{i}_time"],
                        "temperature": user_input[f"slot{i}_temperature"],
                    }
                    for i in range(shown_count)
                    if user_input.get(f"slot{i}_time") is not None
                    and user_input.get(f"slot{i}_temperature") is not None
                ]

                if user_input.get("add_another_slot", False) and len(
                    self._working_day_slots
                ) < MAX_SCHEDULE_SLOTS_PER_DAY:
                    self._working_day_slots.append(
                        self._suggest_next_slot(current)
                    )
                    return self._show_schedule_day_form(day_key)

            schedule = dict(current.get(CONF_SCHEDULE, {}))
            schedule[day_key] = list(self._working_day_slots)
            for target_day in push_to_days:
                schedule[target_day] = list(self._working_day_slots)
            self._working_day_key = None
            return self.async_create_entry(title="", data={**current, CONF_SCHEDULE: schedule})

        return self._show_schedule_day_form(day_key)

    def _suggest_next_slot(self, current: dict[str, Any]) -> dict[str, Any]:
        """A starting point for a freshly-appended slot: an hour after the
        previous one (wrapping midnight), same temperature as it - so
        adding a run of slots is mostly nudging the time forward rather
        than typing each pair from scratch. Falls back to midnight and the
        room's own configured heat-mode minimum for the very first slot."""
        slots = self._working_day_slots
        if slots:
            last_time = parse_time_string(slots[-1]["time"])
            suggested_time = time_of_day((last_time.hour + 1) % 24, last_time.minute)
            suggested_temp = slots[-1]["temperature"]
        else:
            suggested_time = time_of_day(0, 0)
            suggested_temp = current.get(CONF_HEAT_MIN_TEMP, 20.0)
        return {"time": suggested_time.isoformat(), "temperature": suggested_temp}

    def _show_schedule_day_form(self, day_key: str) -> FlowResult:
        slots = self._working_day_slots

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
            ),
        }

        for i, slot in enumerate(slots):
            schema_dict[vol.Optional(f"slot{i}_time", default=slot["time"])] = (
                selector.TimeSelector()
            )
            schema_dict[vol.Optional(f"slot{i}_temperature", default=slot["temperature"])] = (
                vol.Coerce(float)
            )

        if len(slots) < MAX_SCHEDULE_SLOTS_PER_DAY:
            schema_dict[vol.Optional("add_another_slot", default=False)] = bool
        schema_dict[vol.Optional("push_to_days", default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=d, label=label)
                    for d, label in _DAY_LABELS.items()
                    if d != day_key
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        )

        summary = (
            ", ".join(f"{s['time'][:5]} → {s['temperature']}°C" for s in slots)
            if slots
            else "(no slots yet)"
        )
        return self.async_show_form(
            step_id=f"schedule_{day_key}",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"slots_summary": summary},
        )
