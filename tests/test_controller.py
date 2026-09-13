"""Unit tests for the pure control logic in controller.py.

No Home Assistant dependency needed here on purpose - this module is meant
to prove the mirrored-temperature math and the debounce/throttle behaviour
in isolation, fast and deterministically.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "best_trv")
)

from datetime import datetime as dt, time as time_of_day  # noqa: E402

from controller import (  # noqa: E402
    ChangeoverDebouncer,
    CommandQueue,
    HvacAction,
    ScheduleSlot,
    SystemWaterMode,
    compute_sensor_feed_temperature,
    estimate_hvac_action,
    get_active_schedule_slot,
    parse_schedule_config,
    parse_time_string,
    round_to_step,
    should_push_feed_temperature,
)


# -- compute_sensor_feed_temperature ----------------------------------------


def test_heat_mode_passes_real_temperature_through():
    feed = compute_sensor_feed_temperature(
        mode=SystemWaterMode.HEAT,
        setpoint=21.0,
        real_temperature=18.5,
        min_bound=0.0,
        max_bound=55.0,
    )
    assert feed == 18.5


def test_cool_mode_mirrors_room_too_warm_around_setpoint():
    # Spec's worked example: setpoint 22, real 24 -> fake 20.
    feed = compute_sensor_feed_temperature(
        mode=SystemWaterMode.COOL,
        setpoint=22.0,
        real_temperature=24.0,
        min_bound=0.0,
        max_bound=55.0,
    )
    assert feed == 20.0


def test_cool_mode_mirrors_room_too_cold_around_setpoint():
    # Spec's worked example: setpoint 22, real 21 -> fake 23.
    feed = compute_sensor_feed_temperature(
        mode=SystemWaterMode.COOL,
        setpoint=22.0,
        real_temperature=21.0,
        min_bound=0.0,
        max_bound=55.0,
    )
    assert feed == 23.0


def test_cool_mode_opens_further_the_warmer_it_gets():
    # The core argument against "just set the TRV to 30 degC": the fake
    # value should move further from setpoint, not converge on it, as the
    # room gets hotter.
    feed_at_24 = compute_sensor_feed_temperature(
        mode=SystemWaterMode.COOL, setpoint=22.0, real_temperature=24.0, min_bound=0.0, max_bound=55.0
    )
    feed_at_30 = compute_sensor_feed_temperature(
        mode=SystemWaterMode.COOL, setpoint=22.0, real_temperature=30.0, min_bound=0.0, max_bound=55.0
    )
    assert feed_at_30 < feed_at_24


def test_feed_temperature_is_clamped_to_bounds():
    feed = compute_sensor_feed_temperature(
        mode=SystemWaterMode.COOL,
        setpoint=22.0,
        real_temperature=40.0,  # would mirror to 4, below a 5 degC floor
        min_bound=5.0,
        max_bound=55.0,
    )
    assert feed == 5.0

    feed = compute_sensor_feed_temperature(
        mode=SystemWaterMode.HEAT,
        setpoint=21.0,
        real_temperature=60.0,  # above the device's accepted range
        min_bound=0.0,
        max_bound=55.0,
    )
    assert feed == 55.0


# -- round_to_step -----------------------------------------------------------
# Confirmed on the real Aqara E1 installation: its external_temperature_input
# number entity reports step=1 (whole degrees only).


def test_round_to_step_snaps_to_whole_degree():
    assert round_to_step(20.3, step=1.0, min_bound=0.0, max_bound=55.0) == 20.0
    assert round_to_step(20.6, step=1.0, min_bound=0.0, max_bound=55.0) == 21.0


def test_round_to_step_respects_finer_steps():
    assert round_to_step(20.34, step=0.5, min_bound=0.0, max_bound=55.0) == 20.5
    assert round_to_step(20.24, step=0.5, min_bound=0.0, max_bound=55.0) == 20.0


def test_round_to_step_reclamps_after_rounding():
    # Rounding 54.8 up to the nearest whole degree would land on 55, fine,
    # but 54.6 with a step of 1 must not round past a tighter bound.
    assert round_to_step(54.6, step=1.0, min_bound=0.0, max_bound=54.5) == 54.5


def test_round_to_step_zero_step_only_clamps():
    assert round_to_step(20.3, step=0.0, min_bound=0.0, max_bound=55.0) == 20.3


# -- estimate_hvac_action ----------------------------------------------------


def test_estimate_hvac_action_heating():
    action = estimate_hvac_action(
        mode=SystemWaterMode.HEAT, feed_temperature=18.0, setpoint=21.0, assumed_deadband=0.5
    )
    assert action is HvacAction.HEATING


def test_estimate_hvac_action_cooling_maps_low_feed_to_cooling():
    # In cool mode, a low feed value still means "the device thinks it's
    # cold and opens" - but the room-level meaning is cooling.
    action = estimate_hvac_action(
        mode=SystemWaterMode.COOL, feed_temperature=20.0, setpoint=22.0, assumed_deadband=0.5
    )
    assert action is HvacAction.COOLING


def test_estimate_hvac_action_idle_when_feed_above_setpoint():
    action = estimate_hvac_action(
        mode=SystemWaterMode.HEAT, feed_temperature=23.0, setpoint=21.0, assumed_deadband=0.5
    )
    assert action is HvacAction.IDLE


def test_estimate_hvac_action_idle_inside_deadband():
    action = estimate_hvac_action(
        mode=SystemWaterMode.HEAT, feed_temperature=21.2, setpoint=21.0, assumed_deadband=0.5
    )
    assert action is HvacAction.IDLE


# -- ChangeoverDebouncer ------------------------------------------------------


def test_changeover_debouncer_ignores_brief_flap():
    debouncer = ChangeoverDebouncer(debounce_seconds=60)
    assert debouncer.confirmed_mode is SystemWaterMode.HEAT

    # Sensor blips to "cooling" for a moment, then back - never held long
    # enough to confirm.
    assert debouncer.update(SystemWaterMode.COOL, now=0) is SystemWaterMode.HEAT
    assert debouncer.update(SystemWaterMode.HEAT, now=10) is SystemWaterMode.HEAT
    assert debouncer.confirmed_mode is SystemWaterMode.HEAT


def test_changeover_debouncer_confirms_after_holding():
    debouncer = ChangeoverDebouncer(debounce_seconds=60)
    assert debouncer.update(SystemWaterMode.COOL, now=0) is SystemWaterMode.HEAT
    assert debouncer.update(SystemWaterMode.COOL, now=30) is SystemWaterMode.HEAT
    assert debouncer.update(SystemWaterMode.COOL, now=61) is SystemWaterMode.COOL
    assert debouncer.confirmed_mode is SystemWaterMode.COOL


def test_changeover_debouncer_restarts_timer_on_a_different_pending_value():
    debouncer = ChangeoverDebouncer(debounce_seconds=60)
    debouncer.update(SystemWaterMode.COOL, now=0)
    # Flaps back to heat before confirming cool - pending timer must reset,
    # not silently keep counting toward the original "cool" candidacy.
    debouncer.update(SystemWaterMode.HEAT, now=10)
    assert debouncer.update(SystemWaterMode.COOL, now=40) is SystemWaterMode.HEAT
    # Not yet 60s since the pending timer restarted at now=40.
    assert debouncer.update(SystemWaterMode.COOL, now=71) is SystemWaterMode.HEAT
    assert debouncer.update(SystemWaterMode.COOL, now=101) is SystemWaterMode.COOL


# -- should_push_feed_temperature --------------------------------------------


def test_should_push_first_value_always():
    assert should_push_feed_temperature(
        last_sent=None, new_value=20.0, min_delta=0.15, last_sent_at=None, forced_refresh_seconds=90, now=0
    )


def test_should_not_push_small_change_before_refresh_window():
    assert not should_push_feed_temperature(
        last_sent=20.0, new_value=20.05, min_delta=0.15, last_sent_at=0, forced_refresh_seconds=90, now=10
    )


def test_should_push_on_change_past_min_delta():
    assert should_push_feed_temperature(
        last_sent=20.0, new_value=20.2, min_delta=0.15, last_sent_at=0, forced_refresh_seconds=90, now=10
    )


def test_should_push_on_forced_refresh_even_without_change():
    assert should_push_feed_temperature(
        last_sent=20.0, new_value=20.0, min_delta=0.15, last_sent_at=0, forced_refresh_seconds=90, now=91
    )


# -- get_active_schedule_slot -------------------------------------------------
# 2026-09-14 is a Monday; 09-15 Tuesday; 09-17 Thursday - used throughout so
# the day-of-week arithmetic is exercised against real calendar dates.


def test_schedule_empty_returns_none():
    assert get_active_schedule_slot({}, dt(2026, 9, 14, 8, 0)) is None
    assert get_active_schedule_slot({"mon": []}, dt(2026, 9, 14, 8, 0)) is None


def test_schedule_uses_last_slot_at_or_before_now():
    schedule = {
        "mon": [ScheduleSlot(time_of_day(7, 0), 21.0), ScheduleSlot(time_of_day(22, 0), 15.0)]
    }
    assert get_active_schedule_slot(schedule, dt(2026, 9, 14, 12, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(7, 0), 21.0),
    )


def test_schedule_switches_at_the_later_slots_own_time():
    schedule = {
        "mon": [ScheduleSlot(time_of_day(7, 0), 21.0), ScheduleSlot(time_of_day(22, 0), 15.0)]
    }
    assert get_active_schedule_slot(schedule, dt(2026, 9, 14, 22, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(22, 0), 15.0),
    )


def test_schedule_carries_over_from_previous_day_before_first_slot():
    schedule = {
        "mon": [ScheduleSlot(time_of_day(22, 0), 15.0)],
        "tue": [ScheduleSlot(time_of_day(7, 0), 21.0)],
    }
    # Tuesday 03:00 - before Tuesday's own first slot, must carry Monday's.
    assert get_active_schedule_slot(schedule, dt(2026, 9, 15, 3, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(22, 0), 15.0),
    )


def test_schedule_carries_over_multiple_unconfigured_days():
    schedule = {"mon": [ScheduleSlot(time_of_day(7, 0), 21.0)]}
    # Thursday - only Monday has slots; still holds Monday's value.
    assert get_active_schedule_slot(schedule, dt(2026, 9, 17, 12, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(7, 0), 21.0),
    )


def test_schedule_single_day_before_its_own_first_slot_wraps_full_week():
    schedule = {"mon": [ScheduleSlot(time_of_day(7, 0), 21.0)]}
    # Monday 03:00 - before Monday's own slot; the only source is Monday
    # itself, so the lookback must wrap a full week to still find it.
    assert get_active_schedule_slot(schedule, dt(2026, 9, 14, 3, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(7, 0), 21.0),
    )


def test_schedule_sorts_unsorted_slots():
    schedule = {
        "mon": [ScheduleSlot(time_of_day(22, 0), 15.0), ScheduleSlot(time_of_day(7, 0), 21.0)]
    }
    assert get_active_schedule_slot(schedule, dt(2026, 9, 14, 8, 0)) == (
        "mon",
        ScheduleSlot(time_of_day(7, 0), 21.0),
    )


# -- parse_time_string / parse_schedule_config --------------------------------


def test_parse_time_string_accepts_hh_mm():
    assert parse_time_string("07:30") == time_of_day(7, 30, 0)


def test_parse_time_string_accepts_hh_mm_ss():
    assert parse_time_string("07:30:15") == time_of_day(7, 30, 15)


def test_parse_schedule_config_round_trips_from_json_safe_form():
    raw = {"mon": [{"time": "07:00:00", "temperature": 21.0}], "tue": []}
    parsed = parse_schedule_config(raw)
    assert parsed == {
        "mon": [ScheduleSlot(time_of_day(7, 0, 0), 21.0)],
        "tue": [],
    }


# -- CommandQueue -------------------------------------------------------------


def test_command_queue_first_attempt_always_allowed():
    queue = CommandQueue()
    assert queue.should_attempt("k", 21.0, now=0)


def test_command_queue_blocks_retry_before_backoff_elapses():
    queue = CommandQueue()
    assert queue.should_attempt("k", 21.0, now=0)
    queue.mark_attempted("k", now=0)  # failed -> backoff to the first step (30s)
    assert not queue.should_attempt("k", 21.0, now=10)
    assert queue.should_attempt("k", 21.0, now=31)


def test_command_queue_backoff_increases_with_repeated_failures():
    queue = CommandQueue(backoff_seconds=(30.0, 60.0))
    queue.should_attempt("k", 21.0, now=0)
    queue.mark_attempted("k", now=0)  # -> next retry at 30
    assert queue.should_attempt("k", 21.0, now=31)
    queue.mark_attempted("k", now=31)  # -> next retry at 31+60=91
    assert not queue.should_attempt("k", 21.0, now=90)
    assert queue.should_attempt("k", 21.0, now=91)


def test_command_queue_backoff_caps_at_the_last_configured_step():
    queue = CommandQueue(backoff_seconds=(30.0, 60.0))
    queue.should_attempt("k", 21.0, now=0)
    for i in range(5):  # far more failures than backoff steps configured
        queue.mark_attempted("k", now=i * 1000)
    # Last attempt was at now=4000; backoff stays at the final 60s step,
    # never grows unbounded or raises an index error past the tuple's end.
    assert not queue.should_attempt("k", 21.0, now=4001)
    assert queue.should_attempt("k", 21.0, now=4061)


def test_command_queue_new_value_resets_and_allows_immediate_attempt():
    queue = CommandQueue()
    queue.should_attempt("k", 21.0, now=0)
    queue.mark_attempted("k", now=0)
    assert not queue.should_attempt("k", 21.0, now=5)
    # A genuinely different desired value is never held back by the old
    # value's backoff - e.g. the room temperature moved, a new feed value
    # is worth trying right away even while the previous one is retrying.
    assert queue.should_attempt("k", 22.0, now=5)


def test_command_queue_mark_succeeded_clears_pending_state():
    queue = CommandQueue()
    queue.should_attempt("k", 21.0, now=0)
    queue.mark_attempted("k", now=0)
    assert queue.pending_count() == 1
    queue.mark_succeeded("k")
    assert queue.pending_count() == 0
    # With no pending state left, the same value again counts as fresh.
    assert queue.should_attempt("k", 21.0, now=1)


def test_command_queue_tracks_multiple_keys_independently():
    queue = CommandQueue()
    assert queue.should_attempt("a", 1, now=0)
    assert queue.should_attempt("b", 2, now=0)
    queue.mark_attempted("a", now=0)
    assert queue.pending_count() == 2
    assert not queue.should_attempt("a", 1, now=1)
    assert queue.should_attempt("b", 2, now=1)  # "b" never failed - still fresh
