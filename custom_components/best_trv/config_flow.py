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
    CONF_ENTRY_TYPE,
    CONF_EXTERNAL_TEMP_NUMBER,
    CONF_FORCED_REFRESH_SECONDS,
    CONF_HEAT_MAX_TEMP,
    CONF_HEAT_MIN_TEMP,
    CONF_HEAT_STEP,
    CONF_MIN_DELTA,
    CONF_ROOM_SENSOR,
    CONF_SCHEDULE,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_SOURCE,
    CONF_SCHEDULE_TEMPLATE_ENTRY_ID,
    CONF_SENSOR_SELECT,
    CONF_SUSPEND_ACTIVE_VALUE,
    CONF_SUSPEND_SENSOR,
    CONF_TEMPLATE_PARENT_ENTRY_ID,
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
    DEFAULT_SCHEDULE_SOURCE,
    DEFAULT_SUSPEND_ACTIVE_VALUE,
    DEFAULT_SUSPEND_SENSOR,
    DOMAIN,
    ENTRY_TYPE_ROOM,
    ENTRY_TYPE_TEMPLATE,
    SCHEDULE_SOURCE_OWN,
    SCHEDULE_SOURCE_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)

# Sentinel option value for "no parent" / "own schedule, no template" in
# the hand-built SelectSelectors below. Unlike EntitySelector (see the
# suspend-sensor field further down), SelectSelector validates against
# vol.In(options) - since we build that options list ourselves, we can
# just make blank an explicit, always-first, visible choice instead of
# needing the suggested_value dance. Converted back to "key absent" (not
# stored as "") on submit, to match how every other absent-pointer read
# in this integration already works (.get(key) returning None).
#
# Its label is a language-neutral "-" rather than text: unlike the plain
# heat/cool/timing fields, this selector mixes one static sentinel option
# with N dynamically-named template options in the same list, and
# SelectSelector's per-option translation_key lookup only covers a
# uniformly-static options list - it can't translate options we generate
# per installation. The *meaning* of "-" is explained instead through each
# step's normal, fully translatable data_description (see strings.json),
# consistent with how every other field in this integration is localized.
_NO_PARENT = ""
_NO_PARENT_LABEL = "-"


def _template_entries(hass: HomeAssistant, *, exclude_entry_id: str | None = None):
    """Every other best_trv entry that is itself a schedule template."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_TEMPLATE
        and entry.entry_id != exclude_entry_id
    ]


def _template_select_options(
    hass: HomeAssistant, *, exclude_entry_id: str | None = None
) -> list[dict[str, str]]:
    options = [{"value": _NO_PARENT, "label": _NO_PARENT_LABEL}]
    options.extend(
        # entry.title, not entry.data[CONF_NAME]: a rename via
        # BestTRVTemplateOptionsFlow only updates the entry's title, so
        # title is the one field guaranteed current after a rename.
        {"value": entry.entry_id, "label": entry.title}
        for entry in _template_entries(hass, exclude_entry_id=exclude_entry_id)
    )
    return options


def _would_create_cycle(
    hass: HomeAssistant, *, proposed_parent_entry_id: str, this_entry_id: str
) -> bool:
    """Would setting this parent make this_entry_id its own ancestor?

    Walks upward from the proposed parent; a plain `seen` guard also
    protects against an already-broken chain elsewhere in storage, not
    just the cycle this specific save would introduce.
    """
    entry_id: str | None = proposed_parent_entry_id
    seen: set[str] = set()
    while entry_id and entry_id not in seen:
        if entry_id == this_entry_id:
            return True
        seen.add(entry_id)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            break
        entry_id = entry.options.get(
            CONF_TEMPLATE_PARENT_ENTRY_ID, entry.data.get(CONF_TEMPLATE_PARENT_ENTRY_ID)
        )
    return False


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
    """Handle a config flow for Best TRV.

    Two kinds of entry share this flow: a "room" (a physical TRV setup -
    everything this integration did before nested schedule templates
    existed) and a "template" (a named, shareable schedule node with no
    TRVs of its own - see sensor.py/const.CONF_ENTRY_TYPE). `async_step_user`
    is just the fork between the two; each has its own, otherwise
    unchanged wizard below.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._trv_entities: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(step_id="user", menu_options=["room", "template"])

    async def async_step_room(
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
        return self.async_show_form(step_id="room", data_schema=schema, errors=errors)

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
            self._data.setdefault(CONF_ENTRY_TYPE, ENTRY_TYPE_ROOM)
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

    async def async_step_template(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create a named, shareable schedule template - no TRVs involved.

        Its schedule is edited later, via the same dashboard card that
        edits a room's own schedule (point a card at this entry's
        `sensor.*` entity) - not here. This step only creates the node and
        (optionally) places it under an existing template as its parent.
        """
        if user_input is not None:
            parent_entry_id = user_input.get(CONF_TEMPLATE_PARENT_ENTRY_ID, _NO_PARENT)
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_TEMPLATE,
                CONF_NAME: user_input[CONF_NAME],
                CONF_SCHEDULE: {},
            }
            if parent_entry_id != _NO_PARENT:
                data[CONF_TEMPLATE_PARENT_ENTRY_ID] = parent_entry_id
            return self.async_create_entry(title=user_input[CONF_NAME], data=data)

        schema_dict: dict[Any, Any] = {vol.Required(CONF_NAME): str}
        existing_templates = _template_entries(self.hass)
        if existing_templates:
            schema_dict[
                vol.Optional(CONF_TEMPLATE_PARENT_ENTRY_ID, default=_NO_PARENT)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=_template_select_options(self.hass))
            )
        return self.async_show_form(step_id="template", data_schema=vol.Schema(schema_dict))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BestTRVOptionsFlow | BestTRVTemplateOptionsFlow:
        if config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_TEMPLATE:
            return BestTRVTemplateOptionsFlow(config_entry)
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
            menu_options=["tuning", "schedule_toggle", "schedule_source"],
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

    async def async_step_schedule_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Own schedule (default, unchanged) vs. following a template.

        `CONF_SCHEDULE_TEMPLATE_ENTRY_ID`'s sentinel/blank handling is the
        same as the template-creation step's parent picker - see `_NO_PARENT`.
        Picking "template" but leaving this at "-" isn't rejected here: the
        control loop (climate.py `_resolve_schedule_raw`) already falls
        back to "no schedule" for a missing target, exactly like a room
        with an empty schedule of its own does today.
        """
        current = self._current_options()
        if user_input is not None:
            template_entry_id = user_input.get(CONF_SCHEDULE_TEMPLATE_ENTRY_ID, _NO_PARENT)
            new_options = {**current, CONF_SCHEDULE_SOURCE: user_input[CONF_SCHEDULE_SOURCE]}
            if template_entry_id == _NO_PARENT:
                new_options.pop(CONF_SCHEDULE_TEMPLATE_ENTRY_ID, None)
            else:
                new_options[CONF_SCHEDULE_TEMPLATE_ENTRY_ID] = template_entry_id
            return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCHEDULE_SOURCE,
                    default=current.get(CONF_SCHEDULE_SOURCE, DEFAULT_SCHEDULE_SOURCE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[SCHEDULE_SOURCE_OWN, SCHEDULE_SOURCE_TEMPLATE],
                        translation_key="schedule_source",
                    )
                ),
                vol.Optional(
                    CONF_SCHEDULE_TEMPLATE_ENTRY_ID,
                    default=current.get(CONF_SCHEDULE_TEMPLATE_ENTRY_ID, _NO_PARENT),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=_template_select_options(self.hass))
                ),
            }
        )
        return self.async_show_form(step_id="schedule_source", data_schema=schema)


class BestTRVTemplateOptionsFlow(config_entries.OptionsFlow):
    """Rename or re-parent a schedule template after creation.

    Deliberately its own, separate class rather than another menu item
    bolted onto `BestTRVOptionsFlow`: a template has none of the
    heat/cool/timing/suspend fields a room has, so sharing that flow would
    mean branching almost every step in it on entry kind instead of just
    branching once, in `async_get_options_flow`.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _current_options(self) -> dict[str, Any]:
        return {**self._config_entry.data, **self._config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        # Immediately delegates to a differently-named step rather than
        # rendering here directly: HA looks up a step's title/description/
        # field labels by (integration domain, step_id) in strings.json,
        # not by which Python class is showing it - and BestTRVOptionsFlow
        # (the room options flow) already owns "init" for its own, quite
        # different menu. Framework requirement: the very first call into
        # a fresh OptionsFlow instance is always async_step_init, whatever
        # step_id it then chooses to actually show.
        return await self.async_step_template_settings(user_input)

    async def async_step_template_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = self._current_options()
        errors: dict[str, str] = {}
        if user_input is not None:
            parent_entry_id = user_input.get(CONF_TEMPLATE_PARENT_ENTRY_ID, _NO_PARENT)
            if parent_entry_id != _NO_PARENT and _would_create_cycle(
                self.hass,
                proposed_parent_entry_id=parent_entry_id,
                this_entry_id=self._config_entry.entry_id,
            ):
                errors["base"] = "template_parent_cycle"
            else:
                # A rename only ever updates entry.title directly - not
                # data/options - so every reader of a template's name
                # (sensor.py's native_value, the picker labels above,
                # climate.py's _resolve_schedule_raw) only has to trust one
                # field, and it's the same one HA's own UI already shows.
                self.hass.config_entries.async_update_entry(
                    self._config_entry, title=user_input[CONF_NAME]
                )
                new_options = dict(current)
                new_options.pop(CONF_NAME, None)
                if parent_entry_id == _NO_PARENT:
                    new_options.pop(CONF_TEMPLATE_PARENT_ENTRY_ID, None)
                else:
                    new_options[CONF_TEMPLATE_PARENT_ENTRY_ID] = parent_entry_id
                return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._config_entry.title): str,
                vol.Optional(
                    CONF_TEMPLATE_PARENT_ENTRY_ID,
                    default=current.get(CONF_TEMPLATE_PARENT_ENTRY_ID, _NO_PARENT),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_template_select_options(
                            self.hass, exclude_entry_id=self._config_entry.entry_id
                        )
                    )
                ),
            }
        )
        return self.async_show_form(step_id="template_settings", data_schema=schema, errors=errors)
