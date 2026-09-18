import datetime
import json
import os
from typing import TYPE_CHECKING

import pymysql.cursors
from hyundai_kia_connect_api.Vehicle import TripInfo

from Logger import Logger

if TYPE_CHECKING:
    # Only needed for the type hint below. A real (non-TYPE_CHECKING) import here
    # created a circular import with VehicleClient.py's own `from DatabaseClient
    # import DatabaseClient` - it happened to work when VehicleClient was always the
    # first module imported (main.py, http_server.py both do that), but broke as
    # soon as anything imported DatabaseClient first, e.g. a test.
    from VehicleClient import VehicleClient

logger = Logger.get_logger(__name__)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "db", "db_schema.sql")


class DatabaseClient:
    def __init__(self, vehicle_client: "VehicleClient"):
        # Retrieve MySQL/MariaDB connection parameters from environment variables
        self.db_host = os.environ.get("UVO_DB_HOST")
        self.db_port = int(os.environ.get("UVO_DB_PORT", 3306))
        self.db_user = os.environ.get("UVO_DB_USER")
        self.db_password = os.environ.get("UVO_DB_PASSWORD")
        self.db_database = os.environ.get("UVO_DB_NAME")

        if not (self.db_host and self.db_user and self.db_database):
            raise NameError(
                "Required database environment variables (UVO_DB_HOST, UVO_DB_USER, UVO_DB_NAME) are not set"
            )

        # Check if the schema is initialized (e.g., if the 'log' table exists)
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SHOW TABLES LIKE 'log'")
            if cur.fetchone() is None:
                logger.info("Database schema not found. Initializing schema.")
                with open(SCHEMA_PATH, encoding="utf-8") as f:
                    schema_script = f.read()
                # Split the schema script by semicolons and execute each non-empty statement,
                # skipping any transaction control statements.
                for statement in schema_script.split(";"):
                    statement = statement.strip()
                    if statement and not (
                        statement.upper().startswith("START TRANSACTION") or statement.upper().startswith("COMMIT")
                    ):
                        cur.execute(statement)
                conn.commit()
                logger.info("Database schema created successfully.")
        except Exception as e:
            logger.exception(f"Failed to initialize database: {e}")
            raise
        finally:
            conn.close()

        self.vehicle_client = vehicle_client

    def create_connection(self):
        """Create and return a new connection to the MySQL/MariaDB database."""
        try:
            return pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                db=self.db_database,
                charset="utf8mb4",
                autocommit=True,
            )
        except pymysql.MySQLError as e:
            logger.exception(f"Error connecting to MySQL/MariaDB: {e}")
            raise

    def get_last_update_timestamp(self) -> datetime.datetime | None:
        """Return the most recent update timestamp from the 'log' table."""
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(unix_last_vehicle_update_timestamp) FROM log;")
            row = cur.fetchone()
            return datetime.datetime.fromtimestamp(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()

    def get_last_update_odometer(self) -> float | None:
        """Return the maximum odometer reading from the 'log' table."""
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(odometer) FROM log;")
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    @staticmethod
    def build_log_params(vehicle, charging_power_in_kilowatts: float, now: datetime.datetime | None = None) -> tuple:
        """
        Build the parameter tuple for an INSERT into 'log', in column order.

        Pure function (no I/O) so it can be unit tested without a database.
        """
        now = now or datetime.datetime.now()
        odometer = int(vehicle.odometer) if vehicle.odometer else 0
        last_vehicle_update_ts = max(vehicle.last_updated_at, vehicle.location_last_updated_at)

        return (
            vehicle.ev_battery_percentage,
            vehicle.car_battery_percentage,
            vehicle.ev_driving_range,
            now,
            round(now.timestamp()),
            last_vehicle_update_ts,
            round(last_vehicle_update_ts.timestamp()),
            # location_latitude/longitude can legitimately be 0.0 (equator/meridian);
            # only a missing value should become NULL, not a falsy one.
            vehicle.location_latitude if vehicle.location_latitude is not None else None,
            vehicle.location_longitude if vehicle.location_longitude is not None else None,
            odometer,
            1 if vehicle.ev_battery_is_charging else 0,
            1 if vehicle.engine_is_running else 0,
            charging_power_in_kilowatts,
            vehicle.ev_charge_limits_ac or 100,
            vehicle.ev_charge_limits_dc or 100,
            vehicle.air_temperature,
            # dict -> proper JSON text. This used to be an f-string interpolation of
            # Python's dict repr (single-quoted, not valid JSON, and unescaped) directly
            # into a double-quoted SQL string literal - broke on any embedded quote.
            json.dumps(vehicle.data, default=str),
        )

    def save_log(self):
        """Insert a new log entry into the 'log' table."""
        params = self.build_log_params(self.vehicle_client.vehicle, self.vehicle_client.charging_power_in_kilowatts)
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            sql = """INSERT INTO log(
                battery_percentage,
                accessory_battery_percentage,
                estimated_range_km,
                timestamp,
                unix_timestamp,
                last_vehicule_update_timestamp,
                unix_last_vehicle_update_timestamp,
                latitude,
                longitude,
                odometer,
                charging,
                engine_is_running,
                rough_charging_power_estimate_kw,
                ac_charge_limit_percent,
                dc_charge_limit_percent,
                target_climate_temperature,
                raw_api_data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            cur.execute(sql, params)
            conn.commit()
            logger.debug(f"Saved log entry, odometer={params[9]}")
        finally:
            conn.close()

    @staticmethod
    def build_daily_stat_params(day) -> tuple:
        """
        Build the parameter tuple for an INSERT into 'stats_per_day', in column order.

        Pure function (no I/O) so it can be unit tested without a database.

        total_consumed/regenerated_energy on `day` are in Wh. average_consumption_kwh
        is meant to be consumption per 100 km, in kWh - i.e. (Wh / distance_km * 100) / 1000.
        The previous formula computed total_consumed / (100 / distance), which is
        total_consumed * distance / 100: only equal to the correct value when
        distance happens to be exactly 100 km, and the reciprocal of it otherwise.
        """
        day_str = day.date.strftime("%Y-%m-%d")

        average_consumption = 0.0
        average_consumption_regen_deducted = 0.0
        if day.distance > 0:
            average_consumption = day.total_consumed / day.distance * 100
            average_consumption_regen_deducted = (day.total_consumed - day.regenerated_energy) / day.distance * 100

        return (
            day_str,
            round(day.date.timestamp()),
            round(day.total_consumed / 1000, 1),
            round(day.engine_consumption / 1000, 1),
            round(day.climate_consumption / 1000, 1),
            round(day.onboard_electronics_consumption / 1000, 1),
            round(day.battery_care_consumption / 1000, 1),
            round(day.regenerated_energy / 1000, 1),
            day.distance,
            round(average_consumption / 1000, 1),
            round(average_consumption_regen_deducted / 1000, 1),
        )

    def save_daily_stats(self):
        """Insert daily statistics for every day not already in 'stats_per_day'."""
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT date FROM stats_per_day;")
            saved_dates = {row[0] for row in cur.fetchall()}
            current_date = self.vehicle_client._today()

            sql = """INSERT INTO stats_per_day(
                date,
                unix_timestamp,
                total_consumed_kwh,
                engine_consumption_kwh,
                climate_consumption_kwh,
                onboard_electronics_consumption_kwh,
                battery_care_consumption_kwh,
                regenerated_energy_kwh,
                distance,
                average_consumption_kwh,
                average_consumption_regen_deducted_kwh
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

            for day in self.vehicle_client.vehicle.daily_stats:
                # Skip the current day as it might change during the day
                if day.date.date() == current_date:
                    continue

                day_str = day.date.strftime("%Y-%m-%d")
                if day_str in saved_dates:
                    continue

                cur.execute(sql, self.build_daily_stat_params(day))
                conn.commit()
                saved_dates.add(day_str)
                logger.info(f"Saved new daily stats for: {day_str}")
        finally:
            conn.close()

    def log_error(self, exception: Exception):
        """Log an error entry into the 'errors' table."""
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            now = datetime.datetime.now()
            sql = """INSERT INTO errors(
                timestamp,
                unix_timestamp,
                exc_type,
                exc_args
            ) VALUES(%s, %s, %s, %s)"""
            cur.execute(
                sql,
                (now, round(now.timestamp()), type(exception).__name__, str(exception.args)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_most_recent_saved_trip_timestamp(self) -> datetime.datetime | None:
        """Return the most recent trip timestamp from the 'trips' table."""
        conn = self.create_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(unix_timestamp) FROM trips;")
            row = cur.fetchone()
            return datetime.datetime.fromtimestamp(row[0]) if row and row[0] is not None else None
        except Exception as e:
            logger.exception(f"Failed to read most recent trip timestamp: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def build_trip_params(day_date: datetime.datetime, trip: TripInfo, convert_time) -> tuple:
        """
        Build (trip_unix_timestamp, params_tuple) for an INSERT into 'trips'.

        :param convert_time: callable(day_date, hhmmss) -> datetime | None, injected so
                              this stays a pure function independent of VehicleClient.
        """
        trip_unix_timestamp = None
        trip_datetime = None
        if trip.hhmmss:
            trip_datetime = convert_time(day_date, trip.hhmmss)
            if trip_datetime:
                trip_unix_timestamp = int(trip_datetime.timestamp())

        date_string = trip_datetime.strftime("%Y-%m-%d %H:%M") if trip_datetime else day_date.strftime("%Y-%m-%d")

        params = (
            trip_unix_timestamp,
            date_string,
            trip.drive_time or 0,
            trip.idle_time or 0,
            int(trip.distance) if trip.distance else 0,
            int(trip.avg_speed) if trip.avg_speed else 0,
            int(trip.max_speed) if trip.max_speed else 0,
        )
        return trip_unix_timestamp, params

    def save_trip(self, day_date: datetime.datetime, trip: TripInfo):
        """Save a single trip to the database, avoiding duplicates."""
        trip_unix_timestamp, params = self.build_trip_params(
            day_date, trip, self.vehicle_client._convert_trip_time_to_datetime
        )

        conn = self.create_connection()
        try:
            cur = conn.cursor()

            if trip_unix_timestamp:
                cur.execute("SELECT COUNT(*) FROM trips WHERE unix_timestamp = %s", (trip_unix_timestamp,))
                if cur.fetchone()[0] > 0:
                    logger.debug(f"Trip already exists for timestamp {trip_unix_timestamp}, skipping...")
                    return

            sql = """INSERT INTO trips(
                unix_timestamp,
                date,
                driving_time_minutes,
                idle_time_minutes,
                distance_km,
                avg_speed_kmh,
                max_speed_kmh
            ) VALUES(%s, %s, %s, %s, %s, %s, %s)"""
            cur.execute(sql, params)
            conn.commit()
            logger.info(f"Saved new trip for {day_date.strftime('%Y-%m-%d')}")
        finally:
            conn.close()
