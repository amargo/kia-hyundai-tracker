"""Tests for DatabaseClient's pure param builders - no MySQL connection needed.

These are the functions that used to build SQL with f-string interpolation; the
formulas and quoting bugs are exactly what these tests pin down.
"""

import datetime
from types import SimpleNamespace

from DatabaseClient import DatabaseClient


def make_vehicle(**overrides):
    now = datetime.datetime(2026, 9, 1, 12, 0, 0)
    vehicle = SimpleNamespace(
        ev_battery_percentage=55,
        car_battery_percentage=90,
        ev_driving_range=320,
        last_updated_at=now,
        location_last_updated_at=now,
        location_latitude=47.4979,
        location_longitude=19.0402,
        odometer=119741,
        ev_battery_is_charging=False,
        engine_is_running=False,
        ev_charge_limits_ac=100,
        ev_charge_limits_dc=100,
        air_temperature=21,
        data={"vin": 'contains "quotes" and a backslash \\'},
    )
    for key, value in overrides.items():
        setattr(vehicle, key, value)
    return vehicle


def make_day(*, distance=192, total_consumed=25450, regenerated_energy=7650):
    return SimpleNamespace(
        date=datetime.datetime(2026, 4, 12),
        total_consumed=total_consumed,
        engine_consumption=1379,
        climate_consumption=49,
        onboard_electronics_consumption=300,
        battery_care_consumption=0,
        regenerated_energy=regenerated_energy,
        distance=distance,
    )


def make_trip(*, hhmmss="132845", drive_time=60, idle_time=5, distance=42, avg_speed=55, max_speed=110):
    return SimpleNamespace(
        hhmmss=hhmmss,
        drive_time=drive_time,
        idle_time=idle_time,
        distance=distance,
        avg_speed=avg_speed,
        max_speed=max_speed,
    )


class TestBuildLogParams:
    def test_column_order_matches_the_insert_statement(self):
        vehicle = make_vehicle()
        params = DatabaseClient.build_log_params(vehicle, charging_power_in_kilowatts=7.2)

        assert params[0] == 55  # battery_percentage
        assert params[9] == 119741  # odometer
        assert params[12] == 7.2  # rough_charging_power_estimate_kw

    def test_odometer_is_an_integer(self):
        vehicle = make_vehicle(odometer=119741.9)
        params = DatabaseClient.build_log_params(vehicle, 0)
        assert params[9] == 119741

    def test_odometer_none_becomes_zero(self):
        vehicle = make_vehicle(odometer=None)
        params = DatabaseClient.build_log_params(vehicle, 0)
        assert params[9] == 0

    def test_zero_latitude_is_kept_not_treated_as_missing(self):
        # 0.0 is a real coordinate (equator); only None should become NULL.
        vehicle = make_vehicle(location_latitude=0.0, location_longitude=0.0)
        params = DatabaseClient.build_log_params(vehicle, 0)
        assert params[7] == 0.0
        assert params[8] == 0.0

    def test_missing_location_becomes_none(self):
        vehicle = make_vehicle(location_latitude=None, location_longitude=None)
        params = DatabaseClient.build_log_params(vehicle, 0)
        assert params[7] is None
        assert params[8] is None

    def test_raw_api_data_is_valid_json_with_embedded_quotes(self):
        import json

        vehicle = make_vehicle()
        params = DatabaseClient.build_log_params(vehicle, 0)
        raw = params[-1]
        # must round-trip through json.loads - the old f-string dict-repr embedding
        # would neither be valid JSON nor survive an embedded double quote.
        decoded = json.loads(raw)
        assert decoded["vin"] == 'contains "quotes" and a backslash \\'

    def test_charging_flags_are_ints_not_bools(self):
        vehicle = make_vehicle(ev_battery_is_charging=True, engine_is_running=False)
        params = DatabaseClient.build_log_params(vehicle, 0)
        assert params[10] == 1
        assert params[11] == 0


class TestBuildDailyStatParams:
    def test_average_consumption_kwh_per_100km(self):
        # 192 km on 25.45 kWh gross -> 13.3 kWh/100km (the manual README-style example)
        day = make_day(total_consumed=25450, regenerated_energy=7650, distance=192)
        params = DatabaseClient.build_daily_stat_params(day)

        date_str, ts, total_kwh, engine, climate, elec, battery_care, regen, distance, avg, avg_regen = params

        assert date_str == "2026-04-12"
        assert distance == 192
        assert avg == 13.3  # regression: old formula gave total_consumed*distance/100/1000

    def test_average_consumption_regen_deducted(self):
        day = make_day(total_consumed=25450, regenerated_energy=7650, distance=192)
        params = DatabaseClient.build_daily_stat_params(day)
        avg_regen_deducted = params[-1]
        # (25450 - 7650) / 192 * 100 / 1000
        assert avg_regen_deducted == round((25450 - 7650) / 192 * 100 / 1000, 1)

    def test_old_formula_was_the_reciprocal_except_at_exactly_100km(self):
        # Sanity check pinning down exactly what was wrong: the old code computed
        # total_consumed / (100 / distance) == total_consumed * distance / 100,
        # which only matches the correct total_consumed / distance * 100 when
        # distance == 100.
        day = make_day(total_consumed=25450, regenerated_energy=0, distance=192)
        correct = day.total_consumed / day.distance * 100
        old_buggy = day.total_consumed / (100 / day.distance)
        assert correct != old_buggy
        assert round(old_buggy / 1000, 1) != round(correct / 1000, 1)

    def test_zero_distance_does_not_divide_by_zero(self):
        day = make_day(total_consumed=140, regenerated_energy=0, distance=0)
        params = DatabaseClient.build_daily_stat_params(day)
        assert params[-2] == 0  # average_consumption_kwh
        assert params[-1] == 0  # average_consumption_regen_deducted_kwh

    def test_already_saved_days_are_skipped_by_the_caller(self):
        # build_daily_stat_params itself has no dedup logic - that lives in
        # save_daily_stats() around the DB round trip. Documented via a plain call.
        day = make_day()
        params = DatabaseClient.build_daily_stat_params(day)
        assert params[0] == "2026-04-12"


class TestBuildTripParams:
    def test_timestamp_and_date_string_use_the_trip_time(self):
        day_date = datetime.datetime(2026, 4, 12)
        trip = make_trip(hhmmss="132845")

        def convert(d, hhmmss):
            h, m, s = int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:])
            return d + datetime.timedelta(hours=h, minutes=m, seconds=s)

        ts, params = DatabaseClient.build_trip_params(day_date, trip, convert)

        assert ts == int(datetime.datetime(2026, 4, 12, 13, 28, 45).timestamp())
        assert params[1] == "2026-04-12 13:28"

    def test_missing_hhmmss_falls_back_to_day_only(self):
        day_date = datetime.datetime(2026, 4, 12)
        trip = make_trip(hhmmss=None)

        ts, params = DatabaseClient.build_trip_params(day_date, trip, lambda d, h: None)

        assert ts is None
        assert params[1] == "2026-04-12"

    def test_missing_speed_and_distance_become_zero_not_none(self):
        day_date = datetime.datetime(2026, 4, 12)
        trip = make_trip(hhmmss=None, distance=None, avg_speed=None, max_speed=None)

        _, params = DatabaseClient.build_trip_params(day_date, trip, lambda d, h: None)

        assert params[4] == 0  # distance_km
        assert params[5] == 0  # avg_speed_kmh
        assert params[6] == 0  # max_speed_kmh
