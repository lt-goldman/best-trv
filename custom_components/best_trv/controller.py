"""Pure control logic for Best TRV.

Deliberately free of any `homeassistant` import: this is the part of the
integration that can be unit-tested without spinning up hass, and it is
where the mirrored-temperature math, the changeover debounce and the
zigbee-write throttling live. `climate.py` wires this up to real entities
and services.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, time as time_of_day, timedelta
from enum import Enum

# Monday=0 .. Sunday=6, matching `date.weekday()` - this order is load-bearing
# for the lookback in get_active_schedule_slot, not just a display choice.
DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class SystemWaterMode(str, Enum):
    """Which direction the shared hydronic loop is currently running."""

    HEAT = "heat"
    COOL = "cool"


class HvacAction(str, Enum):
    HEATING = "heating"
    COOLING = "cooling"
    IDLE = "idle"
    OFF = "off"


def compute_sensor_feed_temperature(
    *,
    mode: SystemWaterMode,
    setpoint: float,
    real_temperature: float,
    min_bound: float,
    max_bound: float,
) -> float:
    """Return the value to feed the TRV's external-temperature input.

    Heating: pass the real room temperature straight through - the TRV's
    own heat-only logic is already correct.

    Cooling: mirror the real temperature around the setpoint so that the
    TRV's heat-only logic drives the valve in the opposite, correct
    direction for a room fed by cold water::

        fake = 2 * setpoint - real

    The further the room drifts from setpoint, the further the TRV is told
    it has drifted the other way, so it keeps opening rather than
    throttling back as it would if we simply forced a high setpoint.

    The result is clamped to whatever range this TRV's external-temperature
    input actually accepts.
    """
    if mode is SystemWaterMode.HEAT:
        feed = real_temperature
    else:
        feed = (2 * setpoint) - real_temperature
    return min(max(feed, min_bound), max_bound)


def round_to_step(
    value: float, *, step: float, min_bound: float, max_bound: float
) -> float:
    """Snap `value` to the TRV's actual resolution before pushing it.

    Confirmed on the real Aqara E1 installation: its
    `external_temperature_input` number entity reports `step: 1` - sending
    finer deltas (e.g. 20.3) is not wrong, but it's a wasted Zigbee write
    the device rounds away anyway. Re-clamps after rounding in case the
    nearest step lands just outside the bound.
    """
    if step <= 0:
        return min(max(value, min_bound), max_bound)
    snapped = round(value / step) * step
    return min(max(snapped, min_bound), max_bound)


def estimate_hvac_action(
    *,
    mode: SystemWaterMode,
    feed_temperature: float,
    setpoint: float,
    assumed_deadband: float,
) -> HvacAction:
    """Best-effort guess at what the valve is doing right now.

    The Aqara E1 exposes no valve position or running state, so this infers
    intent from the same feed value we send it, assuming a symmetrical
    on-device hysteresis. It is an estimate for display purposes only, not
    a measurement - document it as such wherever it is surfaced.
    """
    if feed_temperature <= setpoint - assumed_deadband:
        return HvacAction.COOLING if mode is SystemWaterMode.COOL else HvacAction.HEATING
    if feed_temperature >= setpoint + assumed_deadband:
        return HvacAction.IDLE
    # Inside the deadband the TRV could be holding either decision; report
    # idle rather than claim a state we cannot actually verify.
    return HvacAction.IDLE


@dataclass
class ChangeoverDebouncer:
    """Requires a changeover reading to hold steady before switching mode.

    Protects against a flapping changeover sensor (e.g. a shunt valve mid
    travel) yanking the TRV between heat- and cool-direction logic during
    the transition itself.
    """

    debounce_seconds: float
    _confirmed_mode: SystemWaterMode = field(default=SystemWaterMode.HEAT)
    _pending_mode: SystemWaterMode | None = field(default=None)
    _pending_since: float | None = field(default=None)

    def update(self, raw_mode: SystemWaterMode, now: float | None = None) -> SystemWaterMode:
        """Feed a fresh raw reading, return the debounced confirmed mode."""
        now = time.monotonic() if now is None else now

        if raw_mode == self._confirmed_mode:
            self._pending_mode = None
            self._pending_since = None
            return self._confirmed_mode

        if raw_mode != self._pending_mode:
            self._pending_mode = raw_mode
            self._pending_since = now
            return self._confirmed_mode

        assert self._pending_since is not None
        if now - self._pending_since >= self.debounce_seconds:
            self._confirmed_mode = raw_mode
            self._pending_mode = None
            self._pending_since = None

        return self._confirmed_mode

    @property
    def confirmed_mode(self) -> SystemWaterMode:
        return self._confirmed_mode


@dataclass(frozen=True)
class ScheduleSlot:
    """One (time, temperature) entry in a day's schedule."""

    time: time_of_day
    temperature: float


def get_active_schedule_slot(
    schedule: dict[str, list[ScheduleSlot]], now: datetime
) -> tuple[str, ScheduleSlot] | None:
    """Return the (day_key, slot) in force at `now`, or None if unused.

    None means no slot is configured anywhere in the schedule - scheduling
    is simply not in use, distinct from "a day has no slots of its own".

    Each day's slots are sorted by time; the active one is the last slot at
    or before `now`'s time-of-day. If `now` is earlier than today's first
    slot (or today has no slots at all), control carries over from the most
    recent earlier day's last slot - exactly how a real time-based
    thermostat schedule holds overnight and across days left unconfigured
    (e.g. only Monday set means every day holds Monday's last value until
    the next explicitly configured day). Looks back a full week so a
    single configured day still resolves correctly on any day.
    """
    if not any(schedule.get(day) for day in DAY_KEYS):
        return None

    for offset in range(8):  # today, then up to 7 days back (a full week)
        check_date = now.date() - timedelta(days=offset)
        day_key = DAY_KEYS[check_date.weekday()]
        slots = sorted(schedule.get(day_key, ()), key=lambda s: s.time)
        if not slots:
            continue
        if offset == 0:
            candidates = [s for s in slots if s.time <= now.time()]
            if candidates:
                return (day_key, candidates[-1])
            continue  # today has slots, but none have started yet
        return (day_key, slots[-1])
    return None  # unreachable given the guard above


def parse_time_string(value: str) -> time_of_day:
    """Parse "HH:MM" or "HH:MM:SS" (what HA's TimeSelector returns)."""
    parts = [int(part) for part in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    hour, minute, second = parts[0], parts[1], parts[2]
    return time_of_day(hour, minute, second)


def parse_schedule_config(
    raw: dict[str, list[dict[str, object]]],
) -> dict[str, list[ScheduleSlot]]:
    """Turn the JSON-serializable config-entry representation into slots.

    Config entries can only hold JSON-safe data, so each slot is stored as
    `{"time": "07:00:00", "temperature": 21.0}` rather than a real
    `datetime.time` - this is the one place that gets parsed back.
    """
    parsed: dict[str, list[ScheduleSlot]] = {}
    for day_key, slots in raw.items():
        parsed[day_key] = [
            ScheduleSlot(
                time=parse_time_string(str(slot["time"])),
                temperature=float(slot["temperature"]),
            )
            for slot in slots
        ]
    return parsed


def should_push_feed_temperature(
    *,
    last_sent: float | None,
    new_value: float,
    min_delta: float,
    last_sent_at: float | None,
    forced_refresh_seconds: float,
    now: float | None = None,
) -> bool:
    """Decide whether a new external-temperature-input write is worth it.

    Always pushes the first value. After that, pushes on a change past
    `min_delta`, or once `forced_refresh_seconds` has elapsed since the
    last write - the forced refresh guards against any undocumented
    on-device fallback to the internal sensor if the external value goes
    stale (no official confirmation either way was found for the Aqara E1;
    see project notes).
    """
    now = time.monotonic() if now is None else now

    if last_sent is None:
        return True
    if abs(new_value - last_sent) >= min_delta:
        return True
    if last_sent_at is not None and (now - last_sent_at) >= forced_refresh_seconds:
        return True
    return False
