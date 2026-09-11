import datetime
import os
from enum import Enum
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitingError,
    RequestTimeoutError,
)

from DatabaseClient import DatabaseClient
from Logger import Logger

# Optional exceptions: only present in newer versions of the library.
try:
    from hyundai_kia_connect_api.exceptions import AuthenticationOTPRequired
except ImportError:  # pragma: no cover - depends on library version
    AuthenticationOTPRequired = None

try:
    from hyundai_kia_connect_api.exceptions import ConsentRequiredError
except ImportError:  # pragma: no cover - depends on library version
    ConsentRequiredError = None

logger = Logger.get_logger(__name__)


class ChargeType(Enum):
    DC = "DC"
    AC = "AC"
    UNKNOWN = "UNKNOWN"


class VehicleClient:
    """
    Vehicle client class
    Role:
    - store data into database
    - handle additional (calculated) attributes that the API does not provide
    """

    def __init__(self):
        # load env vars from .env file
        load_dotenv()

        self.db_client = DatabaseClient(self)

        self.interval_in_seconds: int = 3600 * 4  # default
        self.charging_power_in_kilowatts: float = 0.0  # default = 0 (not charging)
        self.charge_type: ChargeType = ChargeType.UNKNOWN
        self.vehicle: Vehicle | None = None
        self.vm: VehicleManager | None = None
        self.logger = Logger.get_logger(__name__)

        # Timezone the tracker's own clock decisions (day boundaries, scheduling) are
        # evaluated in. Kept under the same env var the HTTP scheduler already used.
        self.timezone = ZoneInfo(os.getenv("UVO_TRACKER_TIMEZONE", "Europe/Budapest"))

        # interval in seconds between checks for cached requests
        # we are limited to 200 requests a day, including cached
        # that's about one every 8 minutes
        # we set it to 2 hours for cached refreshes.
        self.CACHED_REFRESH_INTERVAL = 3600 * 2

        self.CAR_OFF_FORCE_REFRESH_INTERVAL = 3600 * 4

        self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL = 600
        self.DC_CHARGE_FORCE_REFRESH_INTERVAL = 1800
        self.AC_CHARGE_FORCE_REFRESH_INTERVAL = 1800

        # Maximum number of retries for API calls
        self.MAX_API_RETRIES = 1

        self._init_vehicle_manager()

    def _init_vehicle_manager(self):
        self.vm = VehicleManager(
            region=1,
            brand=1,
            username=os.environ["UVO_USERNAME"],
            password=os.environ["UVO_PASSWORD"],
            # EU accounts typically do not require a PIN; default to empty if not provided
            pin=os.getenv("UVO_PIN", ""),
            language=os.getenv("UVO_KIA_LANGUAGE", "en"),
        )

    def get_estimated_charging_power(self):
        """
        Roughly estimates charging speed based on:
        - charge limits for both AC and DC charging
        - current battery percentage (SoC) as reported by the car
        - charging time remaining as reported by the car
        :return:
        """
        if not self.vehicle or not hasattr(self.vehicle, "ev_battery_is_charging"):
            self.logger.info("Vehicle not available or missing charging attributes; skipping power estimation")
            self.charging_power_in_kilowatts = 0.0
            self.charge_type = ChargeType.UNKNOWN
            return 0.0

        if not self.vehicle.ev_battery_is_charging:
            self.charging_power_in_kilowatts = 0.0
            self.charge_type = ChargeType.UNKNOWN
            return 0.0

        charge_duration = self.vehicle.ev_estimated_current_charge_duration
        if not charge_duration or charge_duration <= 0:
            self.logger.warning(
                "Car is charging but reported no estimated charge duration; cannot estimate charging power"
            )
            self.charging_power_in_kilowatts = 0.0
            self.charge_type = ChargeType.UNKNOWN
            return 0.0

        estimated_niro_total_kwh_needed = 70  # 64 usable kwh + unusable kwh + charger losses

        percent_remaining = 100 - self.vehicle.ev_battery_percentage
        kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100

        self.logger.debug(f"Kilowatthours needed for full battery: {kwh_remaining} kWh")

        # todo: there is a bug here: kwh_remaining does not take charge limits into account.
        #  however the "estimated charge time" provided by the car does.
        #  so this formula returns too high values (ex: 20kw when AC charging at home).
        charging_power_in_kilowatts = kwh_remaining / (charge_duration / 60)

        # the delta calculation between ac limits and percentage is a temporary fix for the todo above
        if (
            charging_power_in_kilowatts > 8
            and self.vehicle.ev_charge_limits_ac - self.vehicle.ev_battery_percentage > 15
        ):
            # the car's onboard AC charger cannot exceed 7kW, or 11kW with the optional upgrade
            # if power > 11kW, then assume we are DC charging. recalculate values to take DC charge limits into account
            self.charge_type = ChargeType.DC
            percent_remaining = self.vehicle.ev_charge_limits_dc - self.vehicle.ev_battery_percentage
            kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100
            charging_power_in_kilowatts = kwh_remaining / (charge_duration / 60)

            # DC charging coldgate simulation
            # if the temperature of the battery drops below a certain value, then the BMS will limit the
            # charging power.
            # the rules are roughly:
            # - below 5°c: limited to 22kW
            # - below 15°c: limited to 43kW
            # - below 25°c: limited to 56kW
            # - above 25°c: max: 77kW (except maybe if battery gets too hot)
            # here, we assume that the battery temperature is roughly 5°c above reported outside temperature.
            # we apply a 5°c delta.
            # source: https://www.mojelektromobil.sk/pomale-rychlo-nabijanie-v-chladnom-pocasi-alias-coldgate-blog

            # DISABLED: we don't have access to the outside air temperature through the API
            # if self.vehicle.air_temperature <= 0:
            #     charging_power_in_kilowatts = min(22, charging_power_in_kilowatts)
            # elif self.vehicle.air_temperature <= 10:
            #     charging_power_in_kilowatts = min(43, charging_power_in_kilowatts)
            # elif self.vehicle.air_temperature <= 20:
            #     charging_power_in_kilowatts = min(56, charging_power_in_kilowatts)

            # simulate DC charging power curve for 64kWh e-niro
            # source: https://support.fastned.nl/hc/fr/articles/4408899202193-Kia

            soc = self.vehicle.ev_battery_percentage
            if soc > 95:
                charging_power_in_kilowatts = min(5, charging_power_in_kilowatts)
            elif soc > 90:
                charging_power_in_kilowatts = min(10, charging_power_in_kilowatts)
            elif soc > 80:
                charging_power_in_kilowatts = min(20, charging_power_in_kilowatts)
            elif soc > 75:
                charging_power_in_kilowatts = min(35, charging_power_in_kilowatts)
            elif soc > 55:
                charging_power_in_kilowatts = min(55, charging_power_in_kilowatts)
            elif soc > 40:
                charging_power_in_kilowatts = min(70, charging_power_in_kilowatts)
            elif soc > 27:
                charging_power_in_kilowatts = min(77, charging_power_in_kilowatts)

        else:
            self.charge_type = ChargeType.AC

        self.charging_power_in_kilowatts = round(charging_power_in_kilowatts, 1)
        self.logger.info(f"Estimated charging power: {self.charging_power_in_kilowatts} kW ({self.charge_type.value})")
        return self.charging_power_in_kilowatts

    def _convert_trip_time_to_datetime(self, day_date, trip_hhmmss):
        """
        Convert trip time string (HHMMSS) to datetime object
        :param day_date: datetime object for the day
        :param trip_hhmmss: string in format "HHMMSS" (e.g., "132845")
        :return: datetime object or None if invalid
        """
        if not trip_hhmmss or len(trip_hhmmss) != 6:
            return None

        try:
            hours = int(trip_hhmmss[:2])
            minutes = int(trip_hhmmss[2:4])
            seconds = int(trip_hhmmss[4:])
            return day_date + datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
        except (ValueError, IndexError):
            return None

    def process_trips(self):
        """
        Get, process and save trip info
        A trip contains the following data:
        - timestamp
        - engine time
        - idle time
        - distance
        - max speed
        - average speed
        """
        if not self.vehicle.daily_stats:
            return

        # Determine the oldest and newest date from daily_stats
        dates = [stat.date for stat in self.vehicle.daily_stats]
        oldest_date = min(dates)
        newest_date = max(dates)

        # Build the list of months covered by that range
        months_list = []
        current_date = oldest_date
        while current_date <= newest_date:
            month_str = current_date.strftime("%Y%m")
            if month_str not in months_list:
                months_list.append(month_str)
            current_date += relativedelta(days=1)

        today = self._today()
        for yyyymm in months_list:
            month_info = self._retry_api_call(lambda ym=yyyymm: self.vm.update_month_trip_info(self.vehicle.id, ym))
            if month_info is None and self.vehicle.month_trip_info is None:
                self.logger.error(f"Could not fetch month trip info for {yyyymm}")
                continue
            self.logger.info(f"Successfully updated month trip info for {yyyymm}")

            if self.vehicle.month_trip_info is not None:
                for day in self.vehicle.month_trip_info.day_list:  # ordered on day
                    # Skip current day's trips
                    day_date = datetime.datetime.strptime(day.yyyymmdd, "%Y%m%d").date()
                    if day_date == today:
                        continue

                    most_recent_trip = self.db_client.get_most_recent_saved_trip_timestamp()
                    if most_recent_trip is not None:
                        if datetime.datetime.strptime(day.yyyymmdd, "%Y%m%d") < most_recent_trip:
                            continue

                    day_info = self._retry_api_call(
                        lambda yd=day.yyyymmdd: self.vm.update_day_trip_info(self.vehicle.id, yd)
                    )
                    if day_info is None and self.vehicle.day_trip_info is None:
                        self.logger.error(f"Could not fetch day trip info for {day.yyyymmdd}")
                        continue
                    self.logger.info(f"Successfully updated day trip info for {day.yyyymmdd}")

                    # process and save trips for this day
                    if self.vehicle.day_trip_info is not None:
                        day_date_dt = datetime.datetime.strptime(self.vehicle.day_trip_info.yyyymmdd, "%Y%m%d")

                        # Get the most recent saved trip timestamp to avoid duplicates
                        most_recent_saved_trip = self.db_client.get_most_recent_saved_trip_timestamp()

                        trips_saved = 0
                        for trip in reversed(self.vehicle.day_trip_info.trip_list):  # oldest first
                            # Skip trips that are older than or equal to the most recent saved trip
                            if most_recent_saved_trip and trip.hhmmss:
                                trip_datetime = self._convert_trip_time_to_datetime(day_date_dt, trip.hhmmss)
                                if trip_datetime and trip_datetime <= most_recent_saved_trip:
                                    continue

                            self.db_client.save_trip(day_date_dt, trip)
                            trips_saved += 1

                        if trips_saved > 0:
                            self.logger.info(f"Saved {trips_saved} new trips for {day_date_dt.strftime('%Y-%m-%d')}")
                        else:
                            self.logger.info(f"No new trips to save for {day_date_dt.strftime('%Y-%m-%d')}")

    def save_log(self):
        if not self.vehicle:
            self.logger.warning("save_log called without a valid vehicle; skipping")
            return

        if not hasattr(self.vehicle, "ev_battery_is_charging"):
            self.logger.warning("Vehicle missing expected attributes; skipping save_log")
            return

        if self.vehicle.ev_battery_is_charging:
            self.get_estimated_charging_power()

            estimated_end_datetime = datetime.datetime.now() + datetime.timedelta(
                minutes=self.vehicle.ev_estimated_current_charge_duration
            )
            self.logger.info(f"Estimated end time: {estimated_end_datetime.strftime('%d/%m/%Y at %H:%M')}")
        else:
            # battery is not charging nor is the engine running
            self.charging_power_in_kilowatts = 0.0

        self.db_client.save_log()

    def _retry_api_call(self, api_call, name: str | None = None):
        """
        Generic retry wrapper for API calls with token refresh capability.

        :param api_call: a zero-argument callable. Using a closure (instead of passing
                          bound arguments like self.vm.token separately) means a retry
                          re-reads self.vm.token AFTER handle_api_exception refreshed it,
                          instead of replaying the stale token that was captured before
                          the first attempt.
        :param name: optional label for log messages; falls back to the callable's name
        :return: Result of the API call, or None if all retries failed
        """
        operation_name = name or getattr(api_call, "__name__", "api_call")
        retry_count = 0
        while retry_count <= self.MAX_API_RETRIES:
            try:
                return api_call()
            except Exception as e:
                should_retry = self.handle_api_exception(e)
                if should_retry and retry_count < self.MAX_API_RETRIES:
                    retry_count += 1
                    self.logger.info(f"Retrying {operation_name} after token refresh (attempt {retry_count + 1})")
                    continue
                return None

    def handle_api_exception(self, exc: Exception) -> bool:
        """
        In case of API error, this function defines what to do:
        - log error
        - handle token refresh for authentication errors
        :param exc: the Exception returned by the library
        :return: True if the caller should retry the operation, False otherwise
        """
        # authentication error: token expired or invalid, try to refresh
        if isinstance(exc, AuthenticationError):
            self.logger.warning("Authentication error, attempting to refresh token...")
            try:
                # check_and_refresh_token() already handles a missing/expired token
                # and an empty vehicle list on its own since library 4.x; forcing
                # self.vm.token = None here before calling it used to race with
                # any in-flight call still holding a reference to the old token.
                self.vm.check_and_refresh_token()
                self.logger.info("Token refreshed successfully")
                return True  # caller may retry; closures re-read self.vm.token
            except Exception as refresh_exc:
                self.logger.exception("Failed to refresh token:", exc_info=refresh_exc)
                self.db_client.log_error(exception=refresh_exc)
                return False

        # OTP / consent: needs a human, retrying only makes it worse
        if AuthenticationOTPRequired is not None and isinstance(exc, AuthenticationOTPRequired):
            self.logger.error(
                "The Kia/Hyundai account requires a one-time password to log in. "
                "Complete the OTP flow in the official app, then run again. (%s)",
                exc,
            )
            self.db_client.log_error(exception=exc)
            return False

        if ConsentRequiredError is not None and isinstance(exc, ConsentRequiredError):
            self.logger.error(
                "The Kia/Hyundai account is missing a required consent. "
                "Accept the new terms in the official app, then run again. (%s)",
                exc,
            )
            self.db_client.log_error(exception=exc)
            return False

        # rate limiting: we are blocked for 24 hours
        if isinstance(exc, RateLimitingError):
            self.logger.exception("we got rate limited, probably exceeded 200 requests. exiting", exc_info=exc)
            self.db_client.log_error(exception=exc)
            return False

        # request timeout: vehicle could not be reached.
        # to prevent too many unsuccessful requests in a row (which would lead to rate
        # limiting) we stop here instead of retrying.
        if isinstance(exc, RequestTimeoutError):
            self.logger.exception(
                "The vehicle did not respond. Exiting to prevent too many unsuccessful "
                "requests that would lead to rate limiting ",
                exc_info=exc,
            )
            self.db_client.log_error(exception=exc)
            return False

        # broad API error
        if isinstance(exc, APIError):
            self.logger.exception("server responded with error:", exc_info=exc)
            self.db_client.log_error(exception=exc)
            return False

        # any other exception
        self.logger.exception("generic error:", exc_info=exc)
        self.db_client.log_error(exception=exc)
        return False

    def _today(self) -> datetime.date:
        """Today's date in the tracker's configured timezone, not the host's."""
        return datetime.datetime.now(self.timezone).date()

    def refresh(self):
        self.logger.info("refreshing token...")

        # this command does NOT refresh vehicles (at least for EU and if there is not a
        # preexisting token) - but check_and_refresh_token() itself handles that case
        # since library 4.x, so we no longer need to force self.vm.token = None here.
        try:
            self.vm.check_and_refresh_token()
        except Exception as e:
            self.handle_api_exception(e)
            return

        if self.vm.token is None:
            self.logger.error("No valid token after refresh, aborting this run")
            return

        self.vehicle = self.vm.get_vehicle(os.environ["UVO_VEHICLE_UUID"])
        # fetch cached status, but do not retrieve driving info (driving stats) just yet, to prevent making too
        # many API calls. yes, cached calls also increment the API limit counter.

        response = self._retry_api_call(
            lambda: self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle),
            name="_get_cached_vehicle_state",
        )
        if response is None:
            return

        self.vm.api._update_vehicle_properties(self.vehicle, response)

        self.get_estimated_charging_power()

        self.set_interval()

        # compare odometers. higher odo means we drove and new data must be pulled
        last_db_odometer = self.db_client.get_last_update_odometer()
        if not last_db_odometer:
            self.logger.info("Saving log...")
            self.save_log()

        if last_db_odometer and self.vehicle.odometer > last_db_odometer:
            # it's not time to force refresh yet, but we might still have data on the server
            # that is more recent that our last saved data, so we save it

            response = self._retry_api_call(
                lambda: self.vm.api._get_driving_info(self.vm.token, self.vehicle),
                name="_get_driving_info",
            )
            if response is None:
                return

            self.vm.api._update_vehicle_drive_info(self.vehicle, response)
            self.db_client.save_daily_stats()
            self.get_estimated_charging_power()
            # process_trips() does at least 2 API calls even when there are no new trips.
            # Only process trips if we have valid vehicle data
            if self.vehicle and hasattr(self.vehicle, "daily_stats") and self.vehicle.daily_stats:
                self.process_trips()

        db_last_update_ts = self.db_client.get_last_update_timestamp()

        # if vehicle state has changed, then save an entry.
        # db_last_update_ts is None on an empty log table (first ever run) - treat
        # that as "definitely newer", the comparison below would otherwise TypeError.
        vehicle_updated_at = getattr(self.vehicle, "last_updated_at", None)
        if vehicle_updated_at and (
            db_last_update_ts is None or vehicle_updated_at.replace(tzinfo=None) > db_last_update_ts
        ):
            self.logger.info("Cached data found, saving log...")
            self.save_log()

        if not vehicle_updated_at:
            self.logger.warning("Vehicle missing last_updated_at; skipping force refresh decision")
            return

        delta = datetime.datetime.now() - vehicle_updated_at.replace(tzinfo=None)

        self.logger.info(f"Delta between last saved update and current time: {int(delta.total_seconds())} seconds")

        if delta.total_seconds() < 0:
            self.logger.error(
                f"Negative delta ({delta.total_seconds()}s), probably a timezone issue. Check your logic."
            )
            raise RuntimeError()

        if delta.total_seconds() > self.interval_in_seconds:
            self.logger.info("Performing force refresh...")
            try:
                self.vm.force_refresh_vehicle_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                return

            self.logger.info("Data received by server. Now retrieving from server...")

            try:
                self.vm.update_vehicle_with_cached_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                return

            self.get_estimated_charging_power()

            self.set_interval()

            # process and save data to database.
            self.save_log()

    def set_interval(self):
        if not self.vehicle or not hasattr(self.vehicle, "engine_is_running"):
            return

        if self.vehicle.engine_is_running and not self.vehicle.ev_battery_is_charging:
            # for an EV: "engine running" supposedly means the contact is set and the car is "ready to drive"
            # engine is also reported as "running" in utility mode.
            self.interval_in_seconds = self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL
            self.charging_power_in_kilowatts = 0.0
        elif self.vehicle.ev_battery_is_charging:
            # battery is charging, we can poll more often without draining the 12v battery
            if self.charge_type == ChargeType.DC:
                self.interval_in_seconds = self.DC_CHARGE_FORCE_REFRESH_INTERVAL
            elif self.charge_type in (ChargeType.AC, ChargeType.UNKNOWN):
                self.interval_in_seconds = self.AC_CHARGE_FORCE_REFRESH_INTERVAL
        else:
            # car is off
            self.interval_in_seconds = self.CAR_OFF_FORCE_REFRESH_INTERVAL
