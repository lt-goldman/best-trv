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
    weekday opens its own menu (see `_show_day_menu`) with a button per
    existing slot, "Add another slot" (immediate, no form), "Copy from
    another day", "Push to other days", and "Done" - only "Done" actually
    writes anything back and closes the dialog, so a day can be built up
    over several button taps without reopening "Configure" each time.
    Finishing a whole week still means visiting each day's menu once and
    hitting "Done" on it, a known trade-off of the plain config-flow
    approach chosen over a custom Lovelace card (see README).
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        # Working buffer for whichever day's schedule is currently being
        # edited via the day menu below - a fresh visit seeds it from
        # what's already configured, and it's only written back on
        # "Done".
        self._working_day_key: str | None = None
        self._working_day_slots: list[dict[str, Any]] = []
        # Staged "also push to these days" selection for the day currently
        # being edited - applied together with the day itself on "Done".
        self._working_push_to_days: list[str] = []
        # Which slot the shared "edit_slot" mini-form is currently acting
        # on - set right before showing it, since the form itself is
        # re-entered on submission under one common step_id regardless of
        # which "Edit slot N" menu button was originally clicked.
        self._editing_slot_index: int = 0

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

    # -- one step per weekday, all delegating to the same menu -------------

    async def async_step_schedule_mon(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("mon")

    async def async_step_schedule_tue(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("tue")

    async def async_step_schedule_wed(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("wed")

    async def async_step_schedule_thu(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("thu")

    async def async_step_schedule_fri(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("fri")

    async def async_step_schedule_sat(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("sat")

    async def async_step_schedule_sun(self, user_input=None) -> FlowResult:
        return await self._async_enter_day("sun")

    async def _async_enter_day(self, day_key: str) -> FlowResult:
        """First arrival at a weekday: seed the working buffers and show its menu."""
        if self._working_day_key != day_key:
            current = self._current_options()
            self._working_day_key = day_key
            self._working_day_slots = list(current.get(CONF_SCHEDULE, {}).get(day_key, []))
            self._working_push_to_days = []
        return self._show_day_menu()

    def _show_day_menu(self) -> FlowResult:
        """The one-tap menu for the day currently being edited.

        Every action is a direct button - "Add another slot" appends and
        redraws this same menu immediately, with no intervening form to
        confirm. Editing an existing slot, copying from another day, and
        picking which days to push to are each still a (much smaller) form
        of their own, since those genuinely need input; "Done" is what
        actually writes the result back to the config entry.
        """
        slots = self._working_day_slots
        menu_options = [f"edit_slot_{i}" for i in range(len(slots))]
        if len(slots) < MAX_SCHEDULE_SLOTS_PER_DAY:
            menu_options.append("add_slot")
        menu_options.extend(["copy_day", "push_days", "day_done"])

        slots_summary = (
            ", ".join(f"{s['time'][:5]} → {s['temperature']}°C" for s in slots)
            if slots
            else "(no slots yet)"
        )
        push_summary = (
            ", ".join(_DAY_LABELS[d] for d in self._working_push_to_days)
            if self._working_push_to_days
            else "none"
        )
        return self.async_show_menu(
            step_id="day_menu",
            menu_options=menu_options,
            description_placeholders={
                "day": _DAY_LABELS[self._working_day_key],
                "slots_summary": slots_summary,
                "push_summary": push_summary,
            },
        )

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

    async def async_step_add_slot(self, user_input=None) -> FlowResult:
        """No form at all - the genuinely one-tap action the checkbox used to gate."""
        if len(self._working_day_slots) < MAX_SCHEDULE_SLOTS_PER_DAY:
            self._working_day_slots.append(self._suggest_next_slot(self._current_options()))
        return self._show_day_menu()

    # -- editing one existing slot: 12 thin dispatchers into one form ------

    async def async_step_edit_slot_0(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(0, user_input)

    async def async_step_edit_slot_1(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(1, user_input)

    async def async_step_edit_slot_2(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(2, user_input)

    async def async_step_edit_slot_3(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(3, user_input)

    async def async_step_edit_slot_4(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(4, user_input)

    async def async_step_edit_slot_5(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(5, user_input)

    async def async_step_edit_slot_6(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(6, user_input)

    async def async_step_edit_slot_7(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(7, user_input)

    async def async_step_edit_slot_8(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(8, user_input)

    async def async_step_edit_slot_9(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(9, user_input)

    async def async_step_edit_slot_10(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(10, user_input)

    async def async_step_edit_slot_11(self, user_input=None) -> FlowResult:
        return await self._async_step_edit_slot(11, user_input)

    async def async_step_edit_slot(self, user_input=None) -> FlowResult:
        """Reached only via submission of the form below - `async_show_form`
        dispatches the next step by the step_id it was shown under, which
        is this shared one regardless of which `edit_slot_N` menu button
        was originally clicked; `_editing_slot_index` (set just before the
        form was shown) says which slot that submission is for."""
        return await self._async_step_edit_slot(self._editing_slot_index, user_input)

    async def _async_step_edit_slot(
        self, index: int, user_input: dict[str, Any] | None
    ) -> FlowResult:
        slots = self._working_day_slots
        if index >= len(slots):
            # Stale button (e.g. the menu was re-rendered by another
            # in-flight change before this one was clicked) - just bail
            # back to the menu rather than crashing on a bad index.
            return self._show_day_menu()

        self._editing_slot_index = index

        if user_input is not None:
            if user_input.get("delete_slot", False):
                del slots[index]
            else:
                slots[index] = {
                    "time": user_input["time"],
                    "temperature": user_input["temperature"],
                }
            return self._show_day_menu()

        slot = slots[index]
        schema = vol.Schema(
            {
                vol.Required("time", default=slot["time"]): selector.TimeSelector(),
                vol.Required("temperature", default=slot["temperature"]): vol.Coerce(float),
                vol.Optional("delete_slot", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="edit_slot",
            data_schema=schema,
            description_placeholders={
                "day": _DAY_LABELS[self._working_day_key],
                "slot_number": str(index + 1),
            },
        )

    async def async_step_copy_day(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        current = self._current_options()
        if user_input is not None:
            source_day = user_input["source_day"]
            self._working_day_slots = list(current.get(CONF_SCHEDULE, {}).get(source_day, []))
            return self._show_day_menu()

        schema = vol.Schema(
            {
                vol.Required("source_day"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=d, label=label)
                            for d, label in _DAY_LABELS.items()
                            if d != self._working_day_key
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="copy_day",
            data_schema=schema,
            description_placeholders={"day": _DAY_LABELS[self._working_day_key]},
        )

    async def async_step_push_days(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._working_push_to_days = list(user_input.get("target_days") or [])
            return self._show_day_menu()

        schema = vol.Schema(
            {
                vol.Optional(
                    "target_days", default=list(self._working_push_to_days)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=d, label=label)
                            for d, label in _DAY_LABELS.items()
                            if d != self._working_day_key
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="push_days",
            data_schema=schema,
            description_placeholders={"day": _DAY_LABELS[self._working_day_key]},
        )

    async def async_step_day_done(self, user_input=None) -> FlowResult:
        """Write this day (and any staged pushes to other days) back and close."""
        current = self._current_options()
        schedule = dict(current.get(CONF_SCHEDULE, {}))
        schedule[self._working_day_key] = list(self._working_day_slots)
        for target_day in self._working_push_to_days:
            schedule[target_day] = list(self._working_day_slots)

        self._working_day_key = None
        self._working_day_slots = []
        self._working_push_to_days = []
        return self.async_create_entry(title="", data={**current, CONF_SCHEDULE: schedule})
