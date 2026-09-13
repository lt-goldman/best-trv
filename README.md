# Best TRV

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-0.3.3-blue.svg)](https://github.com/lt-goldman/best-trv/releases)
[![Home Assistant](https://img.shields.io/badge/home%20assistant-2024.2.0%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lt-goldman&repository=best-trv&category=integration)

> No public downloads/build/translation badges here on purpose - those
> come from a public GitHub Actions/Crowdin/HACS-analytics setup this
> private, personal repo doesn't have. Faking them with static images
> would just be misleading.

A Home Assistant custom integration (`best_trv`) for hydronic
setups where the **same** radiator/convector/TRV is fed either hot or cold
water by a heat-pump changeover, and the TRV's own firmware only
understands heating logic.

Worked example this was built for: a heat pump feeds hot water in winter
and cold water in summer through the same loop; an Aqara E1 TRV
(`climate.trv`) regulates the flow; a changeover sensor
(`sensor.wp_coolindicator`, `1` = passive cooling active) says which
water the loop is currently carrying.

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

**Via HACS (recommended for iterating - no more manual copy/restart per
change):** the repo is private, so a GitHub personal access token needs to
be configured under HACS's own settings first (Settings -> Devices &
services -> HACS -> Configure) before the button above will work. Then
either click the **"Open your Home Assistant instance and add this
repository to HACS"** badge above, or add it manually: HACS -> the "..."
menu (top right) -> **Custom repositories** -> URL
`https://github.com/lt-goldman/best-trv`, category **Integration**. Once
added, install "Best TRV" from HACS like any other integration - updates
then show up there too, instead of a manual copy each time a new version
is tagged.

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

- **The climate entity only exposes `auto`/`off` - there is no manual
  "heat" or "cool" choice.** Earlier versions exposed `heat`/`cool`/`off`,
  but real-world testing showed the obvious confusion: picking `cool` while
  the changeover sensor still says the loop is heating did nothing, because
  the changeover sensor - not the user - always decides which direction is
  actually correct (see `SystemWaterMode`/`ChangeoverDebouncer` in
  `controller.py`). `auto` means "on, direction follows the heat pump";
  `hvac_action` (`heating`/`cooling`/`idle`) reports what that direction
  currently is. An entity restored from before this change (state `heat`
  or `cool`) falls back to `off` rather than guessing which one to become.
- **The physical TRV's own setpoint is kept in sync with Best TRV's, on
  every change.** Best TRV never controls the valve directly - it only
  toggles the TRV's `heat`/`off` and feeds it a (real or mirrored)
  temperature. The mirrored-temperature math (`fake = 2*setpoint - real`)
  only produces correct valve behaviour if the physical TRV compares that
  feed against the *same* setpoint it was computed against. So every time
  the target temperature changes (user action, mode switch, or the clamp
  applied on restore), `_async_sync_setpoints` pushes it to the TRV's own
  `climate.set_temperature`, clamped to whatever range the physical device
  itself reports (which may be wider than Best TRV's own heat/cool range).
  Skipping this would silently regulate around whichever setpoint was last
  set on the TRV directly (e.g. via its own app) instead of the one shown
  in Home Assistant.
- **Fail-open, not fail-closed.** If the room sensor or the changeover
  sensor becomes unavailable (or reports something non-numeric) while the
  entity is in `heat`/`cool`, every reachable TRV is driven to the lowest
  value its `external_temperature_input` accepts - forcing the valve open
  - rather than closed. This is deliberate for a heat-pump-fed
  installation: closing valves under stale data risks starving the pump of
  flow, which is judged worse than open valves during a sensor outage.
  `off` is unaffected - it's a deliberate stop, not a failure, and sends
  `system_mode: off` directly.
- **Multiple TRVs per room degrade independently.** If one of several TRVs
  in a room goes unavailable, the rest keep being controlled normally; the
  climate entity stays up and reports per-TRV availability in
  `trv_availability`.
- **`hvac_action` is an estimate, not a measurement.** The Aqara E1 exposes
  no valve position or running-state attribute, so `heating`/`cooling`/
  `idle` is inferred from the same feed value we send it, assuming a
  symmetric on-device hysteresis (`DEFAULT_ASSUMED_DEADBAND` in
  `const.py`). Treat it as indicative.
- **No exact documented timeout for `external_temperature_input` reverting,
  but a confirmed real-world failure mode.** No official Aqara/Z2M
  documentation gives a number, but a user reported (Z2M discussion
  [#19357](https://github.com/Koenkk/zigbee2mqtt/discussions/19357), via
  the [external-sensor blueprint thread](https://community.home-assistant.io/t/z2m-aqara-trv-e1-link-external-temperature-sensor/609689))
  that their unit's `sensor` select silently reverted from `external` to
  `internal` after several days idle. The community mitigation that's
  actually reported to work is not a fixed refresh cadence on the *number*
  value, but re-asserting the `select` on every push - so
  `AqaraE1Z2MAdapter.async_push_feed_temperature` checks and, if needed,
  re-sets `select.<trv>_sensor` to `external` before every write, not just
  once at startup. The `forced_refresh_seconds` timer (default 90s) then
  guarantees that check happens at least that often even with a perfectly
  stable room temperature - a wide margin under the days-scale failure
  actually observed. Separately, writing `external_temperature_input` is
  known to intermittently fail with a "Value not found" converter error
  until the device is re-paired ([Z2M issue #21397](https://github.com/Koenkk/zigbee2mqtt/issues/21397));
  the adapter logs and swallows that rather than crashing the control loop,
  and retries on the next tick. **Open item:** still watch the real
  installation - this is evidence-based, not proven on this hardware yet.
- **Changeover debounce (default 120s)** guards against a shunt sensor
  that briefly flaps mid-transition, so the valve doesn't get yanked
  between heat- and cool-direction logic on the changeover moment itself.
- **The weekly schedule is built into Best TRV itself - no HACS
  scheduler dependency.** Deliberate choice: a custom integration that
  depends on a separately-maintained HACS card/component breaks the
  moment either side ships an incompatible update. The engine
  (`controller.get_active_schedule_slot`, fully unit-tested) is a pure
  function of "what temperature applies right now"; the UI is a plain
  form under the integration's **Configure** menu (Tuning / Schedule
  on/off / one step per weekday), not a drag-based time-block editor -
  see "Explicitly out of scope" below for that trade-off. Up to 4
  (time, temperature) slots per day; a day left empty carries over the
  most recent earlier day's last slot (so configuring only Monday holds
  that value all week). A manual temperature change holds until the
  *next* scheduled transition rather than being fought on every tick -
  the schedule only re-applies when the active slot's identity changes,
  not on every control-loop tick. The Aqara E1's own native
  `schedule`/`schedule_settings` are deliberately left alone
  (`schedule: false`) - letting the device change its own setpoint on a
  timer would fight the setpoint-sync this integration depends on,
  especially in `cool` (see the setpoint-sync bullet above).

## Explicitly out of scope for this MVP

Per the original spec, deferred on purpose until the mirrored-temperature
approach has proven itself:

- PID / TPI / MPC / direct valve-position control (`DirectValveAdapter`).
- Presets (comfort/eco/away/sleep).
- Window/door-open suspend.
- Weather compensation, AI-learning.
- A robust command queue with retry/confirmation (MVP does a single
  best-effort service call per push).
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
syntax/undefined-name errors, and have since been verified against a real
running installation (setpoint-sync and `sensor: external` selection both
confirmed via the Aqara's own Zigbee2MQTT state payload) - see the git
history for what that surfaced and fixed (the availability deadlock in
particular).

**Releasing:** bump `version` in `manifest.json`, commit, `git tag -a
vX.Y.Z`, `git push origin master --tags`, then `gh release create vX.Y.Z`
(HACS tracks GitHub Releases, not bare tags, for update notifications).
Keep the `Version` badge above in sync with the same number - it's a
static badge (the repo being private rules out a live shields.io query),
so it only updates when edited by hand.
