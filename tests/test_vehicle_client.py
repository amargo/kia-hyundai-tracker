"""Tests for VehicleClient's pure/near-pure logic.

VehicleClient() normally logs in to the UVO API and opens a database connection in
__init__, so tests build instances via __new__ and set only the attributes each test
needs, same as production code does inside __init__.
"""

import datetime
import logging
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hyundai_kia_connect_api.exceptions import AuthenticationError, RateLimitingError

from VehicleClient import ChargeType, VehicleClient


class FakeDbClient:
    def __init__(self):
        self.errors = []

    def log_error(self, exception):
        self.errors.append(exception)


class FakeVehicleManager:
    def __init__(self):
        self.token = "stale-token"
        self.refresh_calls = 0

    def check_and_refresh_token(self):
        self.refresh_calls += 1
        self.token = f"fresh-token-{self.refresh_calls}"


def make_client(**overrides):
    client = VehicleClient.__new__(VehicleClient)
    client.logger = logging.getLogger("test")
    client.charge_type = ChargeType.UNKNOWN
    client.charging_power_in_kilowatts = 0.0
    client.MAX_API_RETRIES = 1
    client.db_client = FakeDbClient()
    client.vm = FakeVehicleManager()
    client.timezone = ZoneInfo("Europe/Budapest")
    client.vehicle = None
    client.interval_in_seconds = 3600 * 4
    client.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL = 600
    client.DC_CHARGE_FORCE_REFRESH_INTERVAL = 1800
    client.AC_CHARGE_FORCE_REFRESH_INTERVAL = 1800
    client.CAR_OFF_FORCE_REFRESH_INTERVAL = 3600 * 4
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


class TestConvertTripTimeToDatetime:
    def test_valid_hhmmss(self):
        client = make_client()
        day = datetime.datetime(2026, 4, 12)
        result = client._convert_trip_time_to_datetime(day, "132845")
        assert result == datetime.datetime(2026, 4, 12, 13, 28, 45)

    def test_none_input(self):
        client = make_client()
        assert client._convert_trip_time_to_datetime(datetime.datetime(2026, 4, 12), None) is None

    def test_wrong_length(self):
        client = make_client()
        assert client._convert_trip_time_to_datetime(datetime.datetime(2026, 4, 12), "123") is None

    def test_non_numeric(self):
        client = make_client()
        assert client._convert_trip_time_to_datetime(datetime.datetime(2026, 4, 12), "abcdef") is None


class TestToday:
    def test_uses_configured_timezone_not_host(self):
        client = make_client(timezone=ZoneInfo("Pacific/Kiritimati"))  # UTC+14
        result = client._today()
        assert isinstance(result, datetime.date)


class TestChargingPowerEstimate:
    def _client(self, **vehicle_attrs):
        client = make_client()
        client.vehicle = SimpleNamespace(**vehicle_attrs)
        return client

    def test_no_vehicle_resets_state(self):
        client = make_client(vehicle=None)
        client.charge_type = ChargeType.DC
        client.charging_power_in_kilowatts = 50.0

        assert client.get_estimated_charging_power() == 0.0
        assert client.charging_power_in_kilowatts == 0.0
        assert client.charge_type is ChargeType.UNKNOWN

    def test_not_charging_resets_state(self):
        client = self._client(ev_battery_is_charging=False)
        client.charge_type = ChargeType.DC
        client.charging_power_in_kilowatts = 50.0

        assert client.get_estimated_charging_power() == 0.0
        assert client.charging_power_in_kilowatts == 0.0
        assert client.charge_type is ChargeType.UNKNOWN

    def test_missing_charge_duration_does_not_crash(self):
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=0,
            ev_battery_percentage=50,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        assert client.get_estimated_charging_power() == 0.0
        assert client.charge_type is ChargeType.UNKNOWN

    def test_ac_charging_sets_the_power_attribute(self):
        # 20% missing of 70 kWh = 14 kWh over 120 min -> 7 kW, below the 8 kW AC cut-off
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=120,
            ev_battery_percentage=80,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        power = client.get_estimated_charging_power()

        assert client.charge_type is ChargeType.AC
        assert power == 7.0
        assert client.charging_power_in_kilowatts == 7.0

    def test_dc_charging_is_capped_by_the_power_curve(self):
        # 70% missing of 70 kWh = 49 kWh over 30 min -> 98 kW, capped to 77 at 30% SoC
        client = self._client(
            ev_battery_is_charging=True,
            ev_estimated_current_charge_duration=30,
            ev_battery_percentage=30,
            ev_charge_limits_ac=100,
            ev_charge_limits_dc=100,
        )
        power = client.get_estimated_charging_power()

        assert client.charge_type is ChargeType.DC
        assert power == 77.0


class TestSetInterval:
    def test_no_vehicle_is_a_noop(self):
        client = make_client(vehicle=None)
        client.set_interval()  # must not raise
        assert client.interval_in_seconds == 3600 * 4

    def test_engine_running_uses_the_short_interval(self):
        client = make_client()
        client.vehicle = SimpleNamespace(engine_is_running=True, ev_battery_is_charging=False)
        client.set_interval()
        assert client.interval_in_seconds == client.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL
        assert client.charging_power_in_kilowatts == 0.0

    def test_dc_charging_uses_the_dc_interval(self):
        client = make_client(charge_type=ChargeType.DC)
        client.vehicle = SimpleNamespace(engine_is_running=False, ev_battery_is_charging=True)
        client.set_interval()
        assert client.interval_in_seconds == client.DC_CHARGE_FORCE_REFRESH_INTERVAL

    def test_car_off_uses_the_long_interval(self):
        client = make_client()
        client.vehicle = SimpleNamespace(engine_is_running=False, ev_battery_is_charging=False)
        client.set_interval()
        assert client.interval_in_seconds == client.CAR_OFF_FORCE_REFRESH_INTERVAL


class TestRetryApiCall:
    def test_success_on_first_try_needs_no_refresh(self):
        client = make_client()
        result = client._retry_api_call(lambda: "ok")
        assert result == "ok"
        assert client.vm.refresh_calls == 0

    def test_retry_reads_the_token_fresh_after_refresh(self):
        """
        Regression test for the '{}.access_token'-on-None crash seen in production:
        a naive retry wrapper that captures self.vm.token as an argument BEFORE the
        first attempt replays that same stale value on retry. A closure-based call
        must observe the token that handle_api_exception() just refreshed.
        """
        client = make_client()
        seen_tokens = []
        attempts = {"n": 0}

        def api_call():
            seen_tokens.append(client.vm.token)
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise AuthenticationError("Token is expired")
            return "ok"

        result = client._retry_api_call(api_call)

        assert result == "ok"
        assert client.vm.refresh_calls == 1
        assert seen_tokens[0] == "stale-token"
        assert seen_tokens[1] == "fresh-token-1"  # not a replay of seen_tokens[0]

    def test_gives_up_after_max_retries(self):
        client = make_client()
        client.MAX_API_RETRIES = 1
        calls = {"n": 0}

        def always_fails():
            calls["n"] += 1
            raise AuthenticationError("Token is expired")

        result = client._retry_api_call(always_fails)

        assert result is None
        assert calls["n"] == 2  # first attempt + one retry

    def test_non_retryable_error_does_not_retry(self):
        client = make_client()

        def rate_limited():
            raise RateLimitingError("blocked")

        result = client._retry_api_call(rate_limited)

        assert result is None
        assert client.vm.refresh_calls == 0


class TestHandleApiException:
    def test_authentication_error_triggers_refresh_and_signals_retry(self):
        client = make_client()
        should_retry = client.handle_api_exception(AuthenticationError("Token is expired"))
        assert should_retry is True
        assert client.vm.refresh_calls == 1

    def test_failed_refresh_signals_no_retry_and_logs(self):
        client = make_client()

        def boom():
            raise RuntimeError("network down")

        client.vm.check_and_refresh_token = boom

        should_retry = client.handle_api_exception(AuthenticationError("Token is expired"))

        assert should_retry is False
        assert len(client.db_client.errors) == 1

    def test_rate_limiting_never_retries(self):
        client = make_client()
        assert client.handle_api_exception(RateLimitingError("blocked")) is False
        assert client.vm.refresh_calls == 0

    def test_generic_exception_is_logged_and_not_retried(self):
        client = make_client()
        assert client.handle_api_exception(ValueError("whatever")) is False
        assert len(client.db_client.errors) == 1
