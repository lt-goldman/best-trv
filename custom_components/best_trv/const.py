"""Constants for Best TRV."""
from __future__ import annotations

DOMAIN = "best_trv"

PLATFORMS = ["climate"]

# -- config keys ------------------------------------------------------------

CONF_TRV_CLIMATE_ENTITIES = "trv_climate_entities"
CONF_TRV_MAPPING = "trv_mapping"
CONF_EXTERNAL_TEMP_NUMBER = "external_temperature_number_entity_id"
CONF_SENSOR_SELECT = "sensor_select_entity_id"
CONF_ROOM_SENSOR = "room_sensor_entity_id"
CONF_CHANGEOVER_SENSOR = "changeover_sensor_entity_id"
CONF_COOLING_ACTIVE_VALUE = "cooling_active_value"

CONF_HEAT_MIN_TEMP = "heat_min_temp"
CONF_HEAT_MAX_TEMP = "heat_max_temp"
CONF_HEAT_STEP = "heat_step"
CONF_COOL_MIN_TEMP = "cool_min_temp"
CONF_COOL_MAX_TEMP = "cool_max_temp"
CONF_COOL_STEP = "cool_step"

CONF_MIN_DELTA = "min_delta"
CONF_FORCED_REFRESH_SECONDS = "forced_refresh_seconds"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"

# Optional "stand down" input: any entity whose state, when it equals
# suspend_active_value, means something else is already actively
# conditioning this room (a portable/split AC unit, or - the same idea,
# not yet wired up as its own thing - an open window/door sensor) and
# Best TRV should get out of its way rather than fight it. An empty
# suspend_sensor_entity_id ("" - not None, to keep every stored option a
# plain JSON-safe string) means the feature is simply unused, which is
# also the default for every existing installation.
CONF_SUSPEND_SENSOR = "suspend_sensor_entity_id"
CONF_SUSPEND_ACTIVE_VALUE = "suspend_active_value"

# Schedule: a per-day list of {"time": "HH:MM:SS", "temperature": float}
# slots, JSON-safe for config-entry storage. See controller.ScheduleSlot /
# get_active_schedule_slot for how it's interpreted, and DAY_KEYS there for
# the canonical day ordering/keys ("mon".."sun").
CONF_SCHEDULE_ENABLED = "schedule_enabled"
CONF_SCHEDULE = "schedule"

# -- defaults -----------------------------------------------------------

# Matches the physical Aqara E1's own reported range (climate.thermo_*
# entities report min_temp: 5, max_temp: 30) - no reason for Best TRV to be
# more restrictive than the hardware it's driving.
DEFAULT_HEAT_MIN_TEMP = 5.0
DEFAULT_HEAT_MAX_TEMP = 24.0
DEFAULT_HEAT_STEP = 0.5
# Not a condensation safeguard - that lives at the heat pump, which decides
# the actual mix/supply water temperature. This is just a room-setpoint
# default; the mirrored math itself has no lower bound that matters here
# (see controller.compute_sensor_feed_temperature).
DEFAULT_COOL_MIN_TEMP = 15.0
DEFAULT_COOL_MAX_TEMP = 27.0
DEFAULT_COOL_STEP = 0.5

# Push threshold and forced-refresh cadence for external_temperature_input.
# No official Aqara/Z2M documentation gives an exact timeout, but a
# real-world report exists (Z2M discussion #19357) of the device's `sensor`
# select silently reverting from "external" to "internal" after several
# days idle. 90s gives a wide safety margin under that days-scale failure,
# and - more importantly - the adapter re-asserts the select on every push,
# not just on a timer (see adapters/aqara_e1_z2m.py). Tune via the options
# flow if the real installation shows different behaviour.
DEFAULT_MIN_DELTA = 0.15
DEFAULT_FORCED_REFRESH_SECONDS = 90
DEFAULT_DEBOUNCE_SECONDS = 120
DEFAULT_COOLING_ACTIVE_VALUE = "1"
DEFAULT_SUSPEND_SENSOR = ""
DEFAULT_SUSPEND_ACTIVE_VALUE = "on"

# Assumed on-device hysteresis, used only to *estimate* hvac_action for
# display - the Aqara E1 exposes no valve position or running state.
DEFAULT_ASSUMED_DEADBAND = 0.5

# How often the control loop re-evaluates, independent of state-change events.
UPDATE_TICK_SECONDS = 30

# Fallback external_temperature_input bounds/step if a TRV's number entity
# doesn't (yet) expose min/max/step attributes. Confirmed on the real
# installation: the Aqara E1's own number entity reports step=1 (whole
# degrees only) - DEFAULT_FEED_STEP is only used if that attribute is
# somehow missing, not what actually applies to that hardware.
DEFAULT_FEED_MIN = 0.0
DEFAULT_FEED_MAX = 55.0
DEFAULT_FEED_STEP = 0.5

DEFAULT_SCHEDULE_ENABLED = False
# A day's slots grow one at a time via the "add another slot" toggle in the
# options flow - this is just a safety cap on the loop, not a fixed count
# shown upfront. 12 is generous for any realistic daily pattern.
MAX_SCHEDULE_SLOTS_PER_DAY = 12

# -- bundled dashboard card ---------------------------------------------

# Registered once (in __init__.async_setup) via HA's own
# add_extra_js_url/StaticPathConfig - the schedule card ships inside the
# integration itself, no separate HACS "plugin" install needed.
CARD_FILENAME = "best-trv-schedule-card.js"
CARD_URL = f"/best_trv_files/{CARD_FILENAME}"
