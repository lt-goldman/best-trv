# Best TRV

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/lt-goldman/best-trv)](https://github.com/lt-goldman/best-trv/releases)
[![Home Assistant](https://img.shields.io/badge/home%20assistant-2024.2.0%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lt-goldman&repository=best-trv&category=integration)

## Why this exists

More and more heat pumps can also provide passive cooling through the
same hydronic loop that heats in winter - but at the time of writing, no
Thermostatic Radiator Valve (TRV) on the market natively supports
reversed control logic for cooling. Every TRV assumes "room too cold ->
open, room too warm -> close"; none of them can flip that around when the
same radiator suddenly carries cold water instead of hot.

**Best TRV makes an ordinary, heat-only TRV work correctly in both
directions anyway - no new hardware needed.** It's for anyone with a heat
pump that heats and cools through the same radiators/convectors as the
existing heating system, using regular Zigbee TRVs that were never
designed for this. It also includes a full weekly schedule built in, and
lets a room use multiple TRVs together with a single external
room-temperature sensor.

The currently supported hardware is the Aqara E1 via Zigbee2MQTT (see
Architecture below for how other TRVs would plug in). Worked example: a
heat pump feeds hot water in winter and cold water in summer through the
same loop; an Aqara E1 TRV (`climate.trv`) regulates the flow; a
changeover sensor (`sensor.wp_coolindicator`, `1` = passive cooling
active) says which water the loop is currently carrying.

## The problem and the trick

A normal TRV opens when the room is too cold and closes when it's too warm.
During cooling that has to be reversed: open when too warm, close when too
cold. The Aqara E1 firmware doesn't support that directly. Rather than
implementing our own PID/valve control, this integration keeps using the
TRV's own (heat-only) controller and manipulates the **temperature it
sees** via Zigbee2MQTT's `external_temperature_input`:

- **Heating:** feed the real room temperature straight through.
- **Cooling:** feed a temperature mirrored around the setpoint:
  `fake = 2 * setpoint - real`. The warmer the room actually gets, the
  colder the TRV is told it is, so it keeps opening further instead of
  throttling back as it would if the setpoint were simply forced to 30 °C.

See [`custom_components/best_trv/controller.py`](custom_components/best_trv/controller.py)
for the exact math and its unit tests in [`tests/test_controller.py`](tests/test_controller.py).

## Architecture

```
                     Real room sensor
                            |
                            v
                 BestTRV (climate.py)
                            |
              Changeover sensor -> ChangeoverDebouncer
                            |
              +-------------+-------------+
              |                           |
             HEAT                        COOL
              |                           |
              v                           v
        real temperature           mirrored temperature
              |                           |
              +-------------+-------------+
                            |
                            v
                       TRVAdapter
                    (adapters/base.py)
                            |
                            v
              AqaraE1Z2MAdapter (adapters/aqara_e1_z2m.py)
                            |
              select.<trv>_sensor = "external"
              number.<trv>_external_temperature_input = feed value
                            |
                            v
                     Aqara E1 firmware
                     (its own heat-only
                      valve modulation)
```

`controller.py` holds all the control math (mirrored-temperature
computation, changeover debounce, push-throttling) with **no Home
Assistant import**, so it's unit-tested in isolation. `climate.py` wires
that logic to real entities and a `TRVAdapter` per physical TRV.
`adapters/` is the extension point for other hardware: the spec calls for
`GenericClimateAdapter`, `DirectValveAdapter` and `TargetTemperatureAdapter`
alongside `AqaraE1Z2MAdapter` eventually - only the Aqara/Z2M one exists so
far, deliberately.

## Requirements on the Zigbee2MQTT side

Each TRV needs three entities exposed in Home Assistant (Zigbee2MQTT
creates these automatically for the SRTS-A01 as long as the exposes aren't
disabled):

- `climate.<device>` - the physical TRV's own climate entity (used only to
  flip its `system_mode` between `heat`/`off`; this device has no `cool`).
- `number.<device>_external_temperature_input` - where the feed
  temperature (real or mirrored) is written. Confirmed accepted range:
  0-55 °C.
- `select.<device>_sensor` - must be set to `external` (the integration
  does this automatically on startup).

## Installing

**Via HACS:** click the **"Open your Home Assistant instance and add this
repository to HACS"** badge above, or add it manually: HACS -> the "..."
menu (top right) -> **Custom repositories** -> URL
`https://github.com/lt-goldman/best-trv`, category **Integration**. Once
added, install "Best TRV" from HACS like any other integration.

**Manual (no HACS):**

1. Copy `custom_components/best_trv` into your Home
   Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Settings -> Devices & Services -> Add Integration -> "Best TRV".
4. Step 1: name the room, pick the TRV(s), the room sensor, and the
   changeover sensor + the value it reports while cooling (`1` in the
   worked example).
5. Step 2: confirm (or correct) the auto-guessed `number`/`select` entity
   per TRV.
6. Afterwards, use the integration's **Configure** option (a menu: Tuning /
   Schedule on/off / one entry per weekday) to adjust min/max/step per
   mode, the push threshold, forced-refresh interval, changeover debounce,
   and the weekly schedule - all without recreating the entry. Each menu
   choice is its own form; saving one closes the dialog, so setting up a
   full week means reopening Configure once per day (see README's design
   notes for why).

## Design decisions worth knowing about

- **`auto`/`off` only - no manual "heat"/"cool" choice.** The changeover
  sensor, not the user, always decides which direction is correct; picking
  "cool" while the loop is actually heating wouldn't do anything, so it
  isn't offered. `hvac_action` (`heating`/`cooling`/`idle`) reports the
  real direction.
- **The physical TRV's own setpoint stays in sync with Best TRV's.** The
  mirrored-temperature math only works if the TRV compares the feed value
  against the same setpoint it was computed against, so every target-
  temperature change is pushed to the TRV directly, not just shown in Home
  Assistant.
- **Fail-open, not fail-closed.** If the room or changeover sensor
  becomes unavailable, every reachable TRV is driven fully open rather
  than closed - appropriate for a heat-pump-fed system, where a closed
  valve on stale data risks starving the pump of flow. `off` is
  unaffected.
- **Multiple TRVs in one room degrade independently** - one unavailable
  TRV doesn't take the others, or the room, down with it.
- **`hvac_action` is an estimate, not a measurement.** Most TRVs don't
  expose valve position, so it's inferred from the feed value and an
  assumed hysteresis (`DEFAULT_ASSUMED_DEADBAND`).
- **The TRV's external-sensor selection is re-asserted on every push**,
  not just at setup, since some TRVs can silently revert to their own
  internal sensor over time. Write failures are logged and retried on the
  next tick rather than crashing the control loop.
- **Changeover debounce** avoids yanking the valve between directions if
  the changeover sensor briefly flaps mid-transition.
- **Scheduling is native, not a HACS dependency** - a custom integration
  depending on a separately-maintained HACS component breaks the moment
  either side ships an incompatible update. Slots per weekday grow one at
  a time via an "add another slot" toggle (no fixed count, no re-typing a
  block of empty rows) - a new slot's time defaults to an hour after the
  previous one. A day left empty carries over the most recent configured
  day. Each day's step can also **pull** another day's whole program via
  "copy from", and **push** its own resulting program out to other days
  via "also apply to these days" in the same submission - setting up
  several identical days no longer means visiting each one individually.
  Manual adjustments hold until the next scheduled change instead of
  being overwritten on every tick - call the `best_trv.resync_schedule`
  service (target the entity) to snap back to whatever the schedule
  currently says immediately, instead of waiting for the next transition.
  The TRV's own native scheduling is left disabled - it would fight the
  setpoint-sync above.
- **Failed commands are retried with backoff, not sent once and
  forgotten.** Enabling/disabling a TRV, syncing its setpoint, and
  pushing the feed temperature each go through a small per-adapter
  `CommandQueue` (`controller.py`, fully unit-tested): a new desired
  value is always tried immediately, but a failed attempt backs off
  (30s/60s/120s/300s) before being retried automatically on a later tick,
  rather than a single best-effort call with no follow-up. `enable` in
  particular used to have no error handling at all, so a failed Zigbee
  write there could crash entity setup instead of just being retried.
  `commands_pending` in the entity's attributes shows how many commands
  are currently backing off.

## Explicitly out of scope for this MVP

Deferred on purpose until the mirrored-temperature approach has proven
itself in the field:

- PID / TPI / MPC / direct valve-position control (`DirectValveAdapter`).
- Presets (comfort/eco/away/sleep).
- Window/door-open suspend.
- Weather compensation, AI-learning.
- **A custom Lovelace card with a drag-based schedule time-bar.** The
  schedule *engine* is built (see above); a polished visual editor like
  the HACS scheduler-card is a genuinely separate project (its own
  JS/TS frontend build pipeline, none of which this integration has
  today) and was deliberately deferred rather than rushed alongside the
  backend. The current plain-form UI can be replaced later without
  touching `controller.py` at all.

## Development

```bash
pip install pytest ruff
pytest tests/ -v
ruff check custom_components/ tests/
```

`controller.py` has zero Home Assistant dependency, so `tests/` runs
without a Home Assistant install. `climate.py`, `config_flow.py` and
`adapters/aqara_e1_z2m.py` are checked with `ruff --select=F,E9` for
syntax/undefined-name errors, and have been verified end-to-end against a
real running installation.

**Releasing:** bump `version` in `manifest.json`, commit, `git tag -a
vX.Y.Z`, `git push origin master --tags`, then `gh release create vX.Y.Z`
(HACS tracks GitHub Releases, not bare tags, for update notifications).
The `GitHub release` badge above is live (shields.io queries the public
repo directly) so it updates on its own - nothing to edit by hand.

**Brand icon:** `custom_components/best_trv/brand/{icon,icon@2x,logo,logo@2x}.png`
ships inside the integration itself (Home Assistant 2026.3.0+ serves
local brand images directly - no PR to the external
[home-assistant/brands](https://github.com/home-assistant/brands) repo
needed, and that repo now auto-closes custom-integration PRs anyway).
Regenerate with `python tools/generate_icon.py` (needs `pip install
pillow numpy`, dev-only, not a runtime dependency of the integration).
