"""Climate platform for Best TRV.

One virtual climate entity per config entry (= per room), fanning out to
one or more TRVAdapter instances. See controller.py for the mirrored-
temperature math and changeover debounce, and adapters/ for how commands
actually reach hardware.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .adapters.aqara_e1_z2m import AqaraE1Z2MAdapter
from .adapters.base import TRVAdapter
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
    CONF_TRV_MAPPING,
    DEFAULT_ASSUMED_DEADBAND,
    DEFAULT_FEED_MAX,
    DEFAULT_FEED_MIN,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SUSPEND_ACTIVE_VALUE,
    DEFAULT_SUSPEND_SENSOR,
    DOMAIN,
    MAX_SCHEDULE_SLOTS_PER_DAY,
    UPDATE_TICK_SECONDS,
)
from .controller import (
    DAY_KEYS,
    ChangeoverDebouncer,
    CommandQueue,
    HvacAction,
    ScheduleSlot,
    SystemWaterMode,
    compute_sensor_feed_temperature,
    estimate_hvac_action,
    get_active_schedule_slot,
    parse_schedule_config,
    round_to_step,
    should_push_feed_temperature,
)

_LOGGER = logging.getLogger(__name__)

# Shared by set_schedule_day/set_schedule_days: each slot is validated and
# coerced here (cv.time raises a clean vol.Invalid on a bad "HH:MM[:SS]"
# string, matching how HA's own config/options flows report bad input,
# instead of the entity method having to catch a raw ValueError) rather
# than by hand in the service methods below - one schema, provably correct
# for both services instead of duplicated ad-hoc checks.
_SLOT_SCHEMA = vol.All(
    [{vol.Required("time"): cv.time, vol.Required("temperature"): vol.Coerce(float)}],
    vol.Length(max=MAX_SCHEDULE_SLOTS_PER_DAY),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([BestTRV(hass, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("resync_schedule", {}, "async_resync_schedule")
    platform.async_register_entity_service(
        "set_schedule_day",
        {vol.Required("day"): vol.In(DAY_KEYS), vol.Required("slots"): _SLOT_SCHEMA},
        "async_set_schedule_day",
    )
    platform.async_register_entity_service(
        "set_schedule_days",
        {vol.Required("days"): [vol.In(DAY_KEYS)], vol.Required("slots"): _SLOT_SCHEMA},
        "async_set_schedule_days",
    )
    platform.async_register_entity_service(
        "set_schedule_enabled",
        {vol.Required("enabled"): cv.boolean},
        "async_set_schedule_enabled",
    )


class BestTRV(ClimateEntity, RestoreEntity):
    """A virtual climate entity for a reversible hydronic room loop."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # Only AUTO/OFF are user-selectable - there is deliberately no manual
    # "heat" or "cool" choice. The changeover sensor always decides which
    # direction is actually correct (see SystemWaterMode/ChangeoverDebouncer
    # in controller.py); letting the user pick "cool" while the heat pump is
    # still delivering hot water would just be ignored by the math, which
    # is confusing rather than useful. AUTO here means "on, direction
    # follows the heat pump" - hvac_action reports what that direction
    # currently is.
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry

        data = entry.data
        options = entry.options

        self._attr_unique_id = entry.entry_id
        # One lightweight device per room: with has_entity_name=True and
        # _attr_name=None, this entity inherits the device's name, giving
        # entity_id climate.<slugified room name> (e.g. climate.test_room
        # for a "Test Room" entry) instead of a nameless default.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Best TRV",
            model="Virtual reversible hydronic thermostat",
        )

        self._room_sensor_entity_id: str = data[CONF_ROOM_SENSOR]
        self._changeover_sensor_entity_id: str = data[CONF_CHANGEOVER_SENSOR]

        self._adapters: dict[str, TRVAdapter] = {
            climate_entity_id: AqaraE1Z2MAdapter(
                hass,
                climate_entity_id=climate_entity_id,
                external_temperature_number_entity_id=mapping[CONF_EXTERNAL_TEMP_NUMBER],
                sensor_select_entity_id=mapping[CONF_SENSOR_SELECT],
            )
            for climate_entity_id, mapping in data[CONF_TRV_MAPPING].items()
        }

        self._heat_min = options.get(CONF_HEAT_MIN_TEMP, data[CONF_HEAT_MIN_TEMP])
        self._heat_max = options.get(CONF_HEAT_MAX_TEMP, data[CONF_HEAT_MAX_TEMP])
        self._heat_step = options.get(CONF_HEAT_STEP, data[CONF_HEAT_STEP])
        self._cool_min = options.get(CONF_COOL_MIN_TEMP, data[CONF_COOL_MIN_TEMP])
        self._cool_max = options.get(CONF_COOL_MAX_TEMP, data[CONF_COOL_MAX_TEMP])
        self._cool_step = options.get(CONF_COOL_STEP, data[CONF_COOL_STEP])
        self._min_delta = options.get(CONF_MIN_DELTA, data[CONF_MIN_DELTA])
        self._forced_refresh_seconds = options.get(
            CONF_FORCED_REFRESH_SECONDS, data[CONF_FORCED_REFRESH_SECONDS]
        )
        self._debounce_seconds = options.get(
            CONF_DEBOUNCE_SECONDS, data[CONF_DEBOUNCE_SECONDS]
        )
        self._cooling_active_value: str = options.get(
            CONF_COOLING_ACTIVE_VALUE, data[CONF_COOLING_ACTIVE_VALUE]
        )
        # Optional "stand down" input - .get() with a default on BOTH data
        # and options, not bracket access, since entries created before
        # this feature existed simply won't have these keys at all. An
        # empty suspend_sensor_entity_id means the feature is unused.
        self._suspend_sensor_entity_id: str = options.get(
            CONF_SUSPEND_SENSOR, data.get(CONF_SUSPEND_SENSOR, DEFAULT_SUSPEND_SENSOR)
        )
        self._suspend_active_value: str = options.get(
            CONF_SUSPEND_ACTIVE_VALUE,
            data.get(CONF_SUSPEND_ACTIVE_VALUE, DEFAULT_SUSPEND_ACTIVE_VALUE),
        )
        # Whether _is_suspended() was true on the most recent tick - purely
        # for visibility (extra_state_attributes); never read to decide
        # behavior, only ever (re)computed fresh each tick.
        self._suspended: bool = False
        self._schedule_enabled: bool = options.get(
            CONF_SCHEDULE_ENABLED, data.get(CONF_SCHEDULE_ENABLED, DEFAULT_SCHEDULE_ENABLED)
        )
        # The raw, JSON-safe shape (exactly what's stored in the config
        # entry) - kept alongside the parsed self._schedule below as the
        # single source both are derived from, so the schedule card's
        # "schedule" attribute never needs a separate "serialize
        # ScheduleSlot back to a dict" path that could drift out of sync
        # with parse_schedule_config.
        self._schedule_raw: dict[str, list[dict[str, Any]]] = dict(
            options.get(CONF_SCHEDULE, data.get(CONF_SCHEDULE, {}))
        )
        self._schedule: dict[str, list[ScheduleSlot]] = parse_schedule_config(self._schedule_raw)
        # Identity of the schedule slot last applied to target_temperature.
        # Only re-applying when this changes (a real transition) is what
        # lets a manual adjustment hold until the next scheduled change,
        # instead of the schedule fighting it every tick - see the
        # application block in _async_update_control.
        self._last_applied_schedule_slot: tuple[str, ScheduleSlot] | None = None

        self._changeover = ChangeoverDebouncer(debounce_seconds=self._debounce_seconds)
        # climate_entity_id -> (last value sent, monotonic timestamp) - the
        # min-delta/forced-refresh throttle for feed-temperature pushes.
        #
        # Recovered from hass.data rather than always starting empty: a
        # config-entry reload (triggered by a schedule-card edit, or a
        # Tuning save) tears this whole object down and rebuilds it from
        # scratch within the SAME running Home Assistant process - without
        # this, the fresh instance has no memory of what was already sent
        # moments ago, so should_push_feed_temperature's "always push the
        # first value" rule fires every single time, pushing an unchanged
        # setpoint to a battery-powered TRV for no reason. A genuine HA
        # restart clears hass.data entirely, so a true cold start still
        # gets its normal fresh, immediate push - only a same-process
        # reload benefits from this.
        self._last_sent: dict[str, tuple[float, float]] = dict(
            hass.data.setdefault(DOMAIN, {}).get(entry.entry_id, {}).get("last_sent", {})
        )
        # Retry-with-backoff for failed commands (enable, setpoint, feed
        # temperature) - see controller.CommandQueue. Keyed per adapter so
        # one TRV's failure/backoff never blocks another's.
        self._command_queue = CommandQueue()

        self._attr_hvac_mode: HVACMode = HVACMode.OFF
        self._attr_target_temperature: float | None = None
        self._real_room_temperature: float | None = None
        self._last_feed_temperature: float | None = None
        self._degraded = False
        self._degraded_reason: str | None = None

        self._remove_listeners: list[Any] = []

    # -- lifecycle ------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (
            HVACMode.AUTO.value,
            HVACMode.OFF.value,
        ):
            self._attr_hvac_mode = HVACMode(last_state.state)
            restored_temp = last_state.attributes.get(ATTR_TEMPERATURE)
            if restored_temp is not None:
                try:
                    self._attr_target_temperature = float(restored_temp)
                except (TypeError, ValueError):
                    self._attr_target_temperature = None
        # A state restored from before the AUTO/OFF redesign (plain "heat"
        # or "cool") won't match the check above and falls through to the
        # class default (OFF) - a safe, deliberate restart rather than a
        # guess at which of the two old modes should become AUTO.

        if self._attr_target_temperature is None:
            self._attr_target_temperature = (
                self._cool_min
                if self._changeover.confirmed_mode == SystemWaterMode.COOL
                else self._heat_min
            )

        for adapter in self._adapters.values():
            if isinstance(adapter, AqaraE1Z2MAdapter):
                await adapter.async_ensure_external_sensor_selected()
        # Enable/setpoint sync happens via _async_update_control below (it
        # always runs both, queue-gated) - no need to also call them here.

        tracked_entities = [self._room_sensor_entity_id, self._changeover_sensor_entity_id]
        if self._suspend_sensor_entity_id:
            tracked_entities.append(self._suspend_sensor_entity_id)
        self._remove_listeners.append(
            async_track_state_change_event(
                self.hass, tracked_entities, self._async_input_changed
            )
        )
        self._remove_listeners.append(
            async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=UPDATE_TICK_SECONDS)
            )
        )

        # NOT force=True: that would unconditionally re-push the feed
        # temperature regardless of what _last_sent (just recovered above,
        # from hass.data, if this is a same-process reload rather than a
        # true cold start) already knows - defeating the whole point of
        # recovering it. should_push_feed_temperature's own "always push
        # the first value" rule (last_sent is None) still covers a genuine
        # cold start correctly without needing an explicit override here.
        await self._async_update_control(force=False)

    async def async_will_remove_from_hass(self) -> None:
        for remove in self._remove_listeners:
            remove()
        self._remove_listeners.clear()
        # Survives a same-process config-entry reload (see __init__) so the
        # rebuilt entity doesn't re-push an unchanged feed temperature to a
        # battery-powered TRV just because it forgot it already did.
        self.hass.data.setdefault(DOMAIN, {}).setdefault(self._entry.entry_id, {})[
            "last_sent"
        ] = self._last_sent

    # -- HA entity properties ---------------------------------------------

    # The comfort range/step is picked by the REAL system water mode
    # (confirmed by the changeover sensor), not by the user-selectable
    # hvac_mode - there's only AUTO/OFF to pick from, so this is the only
    # place "heat" vs "cool" ranges actually get applied.
    @property
    def min_temp(self) -> float:
        return (
            self._cool_min
            if self._changeover.confirmed_mode == SystemWaterMode.COOL
            else self._heat_min
        )

    @property
    def max_temp(self) -> float:
        return (
            self._cool_max
            if self._changeover.confirmed_mode == SystemWaterMode.COOL
            else self._heat_max
        )

    @property
    def target_temperature_step(self) -> float:
        return (
            self._cool_step
            if self._changeover.confirmed_mode == SystemWaterMode.COOL
            else self._heat_step
        )

    @property
    def current_temperature(self) -> float | None:
        return self._real_room_temperature

    @property
    def hvac_action(self) -> str | None:
        if self._attr_hvac_mode == HVACMode.OFF:
            return HvacAction.OFF.value
        if self._suspended:
            return HvacAction.IDLE.value
        if self._degraded or self._last_feed_temperature is None or self._attr_target_temperature is None:
            return None
        return estimate_hvac_action(
            mode=self._changeover.confirmed_mode,
            feed_temperature=self._last_feed_temperature,
            setpoint=self._attr_target_temperature,
            assumed_deadband=DEFAULT_ASSUMED_DEADBAND,
        ).value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "system_water_mode": self._changeover.confirmed_mode.value,
            "feed_temperature": self._last_feed_temperature,
            "degraded": self._degraded,
            "degraded_reason": self._degraded_reason,
            "trv_availability": {
                entity_id: adapter.available for entity_id, adapter in self._adapters.items()
            },
            "commands_pending": self._command_queue.pending_count(),
            "suspended": self._suspended,
            "schedule_enabled": self._schedule_enabled,
            "active_schedule_slot": (
                f"{self._last_applied_schedule_slot[0]} {self._last_applied_schedule_slot[1].time}"
                if self._last_applied_schedule_slot is not None
                else None
            ),
            # For the schedule card (custom_components/best_trv/www/) - the
            # raw JSON-safe weekly schedule plus the temperature range it
            # should offer, so the card never has to guess or duplicate the
            # heat/cool min/max already configured here.
            "schedule": self._schedule_raw,
            "schedule_temp_min": min(self._heat_min, self._cool_min),
            "schedule_temp_max": max(self._heat_max, self._cool_max),
        }

    # -- HA entity commands -----------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._attr_target_temperature = min(max(float(temperature), self.min_temp), self.max_temp)
        self.async_write_ha_state()
        # Syncs the setpoint too (and enabled, and the feed push) - no need
        # to also call those individually, they'd just be a redundant
        # queue-gated no-op immediately followed by this anyway.
        await self._async_update_control(force=True)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        if self._attr_target_temperature is not None:
            self._attr_target_temperature = min(
                max(self._attr_target_temperature, self.min_temp), self.max_temp
            )
        self.async_write_ha_state()
        await self._async_update_control(force=True)

    async def _async_apply_schedule(self) -> None:
        """Apply the schedule's temperature for right now, if it changed.

        Only acts when the *active slot's identity* differs from the one
        last applied - not merely when the resulting temperature differs.
        That's what lets a manual adjustment made between two scheduled
        times hold until the next real transition, instead of the schedule
        overwriting it back on every 30s tick. On first run after a reload
        (`_last_applied_schedule_slot` is None) this always applies
        whatever slot is active right now, so a fresh start picks up
        "what should the temperature be at this moment" immediately rather
        than waiting for the next transition.
        """
        if not self._schedule_enabled or not self._schedule:
            return
        active = get_active_schedule_slot(self._schedule, dt_util.now())
        if active is None or active == self._last_applied_schedule_slot:
            return
        await self._apply_schedule_slot(active)

    async def async_resync_schedule(self) -> None:
        """Force-apply whatever the schedule says right now (service call).

        The explicit counterpart to `_async_apply_schedule`'s "hold until
        the next transition" behaviour: this bypasses that guard entirely,
        for a user who wants to stop overriding manually and go back to
        the schedule immediately rather than waiting. A no-op if the
        schedule is off or empty, or if no slot is currently active.
        """
        if not self._schedule_enabled or not self._schedule:
            return
        active = get_active_schedule_slot(self._schedule, dt_util.now())
        if active is None:
            return
        await self._apply_schedule_slot(active)
        self.async_write_ha_state()
        await self._async_update_control(force=True)

    async def async_set_schedule_day(self, day: str, slots: list[dict[str, Any]]) -> None:
        """Replace one day's schedule slots (called by the schedule card)."""
        await self._async_write_schedule_days({day: slots})

    async def async_set_schedule_days(self, days: list[str], slots: list[dict[str, Any]]) -> None:
        """Apply the same slot list to several days in one atomic write.

        Used for the card's "push to other days" action - looping
        `async_set_schedule_day` per day would lose the atomicity the
        config-flow wizard's equivalent step already had (a dropped
        connection mid-loop would leave the week half-pushed) and would
        trigger one reload per day instead of one for the whole change.
        """
        await self._async_write_schedule_days({day: slots for day in days})

    async def _async_write_schedule_days(
        self, updates: dict[str, list[dict[str, Any]]]
    ) -> None:
        raw_schedule = dict(self._schedule_raw)
        for day, slots in updates.items():
            raw_schedule[day] = [
                {"time": slot["time"].isoformat(), "temperature": slot["temperature"]}
                for slot in slots
            ]
        new_options = {**self._entry.data, **self._entry.options, CONF_SCHEDULE: raw_schedule}

        # Update in-memory state and the entity's own state first, so the
        # card sees the change instantly - hass.config_entries.async_update_entry
        # is a plain @callback (not awaitable) that only *schedules* the
        # config-entry-reload update listener as a separate background
        # task, and diffs by value first (identical options -> no reload at
        # all). No need to wait for, or trigger a visible flicker from,
        # that reload just to make an edit "stick" on screen.
        self._schedule_raw = raw_schedule
        self._schedule = parse_schedule_config(raw_schedule)
        self.async_write_ha_state()
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

    async def async_set_schedule_enabled(self, enabled: bool) -> None:
        """Toggle the schedule on/off (called by the schedule card)."""
        new_options = {
            **self._entry.data,
            **self._entry.options,
            CONF_SCHEDULE_ENABLED: enabled,
        }
        self._schedule_enabled = enabled
        self.async_write_ha_state()
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

    async def _apply_schedule_slot(self, active: tuple[str, ScheduleSlot]) -> None:
        self._last_applied_schedule_slot = active
        _, slot = active
        self._attr_target_temperature = min(max(slot.temperature, self.min_temp), self.max_temp)
        await self._async_sync_setpoints()

    async def _async_sync_setpoints(self) -> None:
        """Push our target_temperature to every adapter's own TRV setpoint.

        Safe to call unconditionally on every tick, not just reactively on
        a target-temperature change: `CommandQueue` only lets a genuinely
        new value through immediately, or a previously-failed one once its
        backoff has elapsed - a repeat of an already-applied value is a
        cheap no-op. That's what gives a failed setpoint sync (previously
        unretried until the user happened to change the temperature again)
        an actual periodic retry.
        """
        if self._attr_target_temperature is None:
            return
        target = self._attr_target_temperature
        for climate_entity_id, adapter in self._adapters.items():
            if not adapter.available:
                continue
            key = (climate_entity_id, "setpoint")
            if not self._command_queue.should_attempt(key, target):
                continue
            self._command_queue.mark_attempted(key)
            if await adapter.async_set_setpoint(target):
                self._command_queue.mark_succeeded(key)

    def _is_suspended(self) -> bool:
        """Whether the configured "stand down" sensor is currently active.

        For a room that also has its own independent heating/cooling (a
        portable or split AC unit, say) - fighting it is worse than doing
        nothing: whichever direction the AC runs, Best TRV continuing to
        actively chase its own setpoint just means two uncoordinated
        controllers pulling the same room in different directions (or the
        same direction, making the TRV's own effort redundant) - either
        way, audible valve hunting for no benefit. Standing down while
        that AC (or, generically, whatever's plugged into this - an open
        window/door sensor would work exactly the same way) is active is
        simpler and more correct than trying to model its effect on the
        mirrored-temperature math.

        suspend_active_value is a comma-separated list, not a single
        value - a simple binary_sensor/switch only ever needs one ("on"),
        but a real AC unit's own climate entity typically reports several
        distinct "actually conditioning" states (e.g. heat/cool/heat_cool/
        dry, as opposed to off/fan_only), and this needs to match any of
        them, not force a choice of just one.
        """
        if not self._suspend_sensor_entity_id:
            return False
        state = self.hass.states.get(self._suspend_sensor_entity_id)
        if state is None:
            return False
        active_values = {v.strip() for v in self._suspend_active_value.split(",") if v.strip()}
        return state.state in active_values

    async def _async_sync_enabled(self) -> None:
        """Push our on/off state to every adapter, retried the same way."""
        enabled = self._attr_hvac_mode != HVACMode.OFF and not self._suspended
        for climate_entity_id, adapter in self._adapters.items():
            if not adapter.available:
                continue
            key = (climate_entity_id, "enabled")
            if not self._command_queue.should_attempt(key, enabled):
                continue
            self._command_queue.mark_attempted(key)
            if await adapter.async_set_enabled(enabled):
                self._command_queue.mark_succeeded(key)

    # -- internal update loop ----------------------------------------------

    @callback
    def _async_input_changed(self, event: Event) -> None:
        self.hass.async_create_task(self._async_update_control())

    async def _async_tick(self, _now: Any) -> None:
        await self._async_update_control()

    async def _async_update_control(self, force: bool = False) -> None:
        # Suspend only means anything while the user's own AUTO/OFF choice
        # would otherwise be actively driving the TRV - an explicit OFF
        # already means "no control", so there's nothing to additionally
        # stand down from, and self._suspended stays False rather than
        # muddying an unrelated state with a reason that isn't why it's off.
        self._suspended = self._attr_hvac_mode != HVACMode.OFF and self._is_suspended()

        # Runs every tick regardless of mode - queue-gated, so a failed
        # "turn off" is retried too, not just a failed "turn on"/setpoint.
        await self._async_sync_enabled()

        if self._attr_hvac_mode == HVACMode.OFF or self._suspended:
            self._degraded = False
            self._degraded_reason = None
            self._last_feed_temperature = None
            self.async_write_ha_state()
            return

        await self._async_apply_schedule()
        await self._async_sync_setpoints()

        room_state = self.hass.states.get(self._room_sensor_entity_id)
        changeover_state = self.hass.states.get(self._changeover_sensor_entity_id)

        room_ok = room_state is not None and room_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )
        changeover_ok = changeover_state is not None and changeover_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )

        if room_ok:
            try:
                self._real_room_temperature = float(room_state.state)
            except ValueError:
                room_ok = False

        raw_mode = SystemWaterMode.HEAT
        if changeover_ok and changeover_state.state == self._cooling_active_value:
            raw_mode = SystemWaterMode.COOL
        confirmed_mode = self._changeover.update(raw_mode)

        degraded = not room_ok or not changeover_ok
        reason: str | None = None
        if not room_ok:
            reason = f"room sensor {self._room_sensor_entity_id} unavailable or non-numeric"
        elif not changeover_ok:
            reason = f"changeover sensor {self._changeover_sensor_entity_id} unavailable"

        self._degraded = degraded
        self._degraded_reason = reason

        nominal_feed: float | None = None
        if not degraded and self._attr_target_temperature is not None and self._real_room_temperature is not None:
            nominal_feed = compute_sensor_feed_temperature(
                mode=confirmed_mode,
                setpoint=self._attr_target_temperature,
                real_temperature=self._real_room_temperature,
                min_bound=DEFAULT_FEED_MIN,
                max_bound=DEFAULT_FEED_MAX,
            )
        self._last_feed_temperature = nominal_feed

        for climate_entity_id, adapter in self._adapters.items():
            if not adapter.available:
                continue

            min_bound, max_bound = adapter.feed_temperature_bounds
            step = adapter.feed_temperature_step

            if degraded or nominal_feed is None:
                # Fail open: this installation is heat-pump fed, so on any
                # stale/unreliable input we ask for the widest-open valve
                # rather than risk starving the pump of flow. Bypasses the
                # min-delta throttle - going degraded is safety-relevant.
                feed_value = min_bound
                should_push = True
            else:
                feed_value = compute_sensor_feed_temperature(
                    mode=confirmed_mode,
                    setpoint=self._attr_target_temperature,
                    real_temperature=self._real_room_temperature,
                    min_bound=min_bound,
                    max_bound=max_bound,
                )
                # Snap to what the TRV can actually resolve (the real Aqara
                # E1 only accepts whole degrees) before deciding whether a
                # push is even worth it - otherwise every sub-step wobble in
                # the room sensor looks like a "change" worth a Zigbee write.
                feed_value = round_to_step(
                    feed_value, step=step, min_bound=min_bound, max_bound=max_bound
                )
                last_sent, last_sent_at = self._last_sent.get(climate_entity_id, (None, None))
                should_push = force or should_push_feed_temperature(
                    last_sent=last_sent,
                    new_value=feed_value,
                    min_delta=self._min_delta,
                    last_sent_at=last_sent_at,
                    forced_refresh_seconds=self._forced_refresh_seconds,
                )

            if should_push:
                # `should_push_feed_temperature` decides "is this a moment
                # to try" (magnitude of change / forced refresh); the
                # CommandQueue decides "did the last attempt at this exact
                # value already fail, and if so has its backoff elapsed" -
                # composing both means a failing push doesn't get hammered
                # every tick, but also doesn't just silently stop retrying.
                key = (climate_entity_id, "feed_temperature")
                if self._command_queue.should_attempt(key, feed_value):
                    self._command_queue.mark_attempted(key)
                    if await adapter.async_push_feed_temperature(feed_value):
                        self._command_queue.mark_succeeded(key)
                    self._last_sent[climate_entity_id] = (feed_value, time.monotonic())

        self.async_write_ha_state()
