import datetime
import logging
import os
import pymysql.cursors

import VehicleClient
from hyundai_kia_connect_api.Vehicle import TripInfo

class DatabaseClient:
    def __init__(self, vehicle_client: VehicleClient):
        # Retrieve MySQL/MariaDB connection parameters from environment variables
        self.db_host = os.environ.get("UVO_DB_HOST")
        self.db_port = int(os.environ.get("UVO_DB_PORT", 3306))
        self.db_user = os.environ.get("UVO_DB_USER")
        self.db_password = os.environ.get("UVO_DB_PASSWORD")
        self.db_database = os.environ.get("UVO_DB_NAME")

        if not (self.db_host and self.db_user and self.db_database):
            raise NameError("Required database environment variables (UVO_DB_HOST, UVO_DB_USER, UVO_DB_DATABASE) are not set")

        # Check if the schema is initialized (e.g., if the 'log' table exists)
        try:
            conn = self.create_connection()
            cur = conn.cursor()
            cur.execute("SHOW TABLES LIKE 'log'")
            if cur.fetchone() is None:
                logging.info("Database schema not found. Initializing schema.")
                with open("db_schema.sql", "r", encoding="utf-8") as f:
                    schema_script = f.read()
                # Split the schema script by semicolons and execute each non-empty statement,
                # skipping any transaction control statements.
                for statement in schema_script.split(';'):
                    statement = statement.strip()
                    if statement and not (statement.upper().startswith("START TRANSACTION") or statement.upper().startswith("COMMIT")):
                        cur.execute(statement)
                conn.commit()
                logging.info("Database schema created successfully.")
        except Exception as e:
            logging.exception("Failed to initialize database: " + str(e))
            raise
        finally:
            conn.close()

        self.vehicle_client = vehicle_client

    def create_connection(self):
        """Create and return a new connection to the MySQL/MariaDB database."""
        try:
            conn = pymysql.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                db=self.db_database,
                charset='utf8mb4',
                autocommit=True
            )
            return conn
        except pymysql.MySQLError as e:
            logging.exception("Error connecting to MySQL/MariaDB: " + str(e))
            raise

    def get_last_update_timestamp(self) -> datetime.datetime:
        """Return the most recent update timestamp from the 'log' table."""
        conn = self.create_connection()
        cur = conn.cursor()
        sql = 'SELECT MAX(unix_last_vehicle_update_timestamp) FROM log;'
        cur.execute(sql)
        row = cur.fetchone()
        conn.close()
        return datetime.datetime.fromtimestamp(row[0]) if row[0] is not None else None

    def get_last_update_odometer(self) -> float:
        """Return the maximum odometer reading from the 'log' table."""
        conn = self.create_connection()
        cur = conn.cursor()
        sql = 'SELECT MAX(odometer) FROM log;'
        cur.execute(sql)
        row = cur.fetchone()
        conn.close()
        return row[0]

    def get_most_recent_saved_trip_timestamp(self):
        """Return the most recent trip timestamp from the 'trips' table."""
        conn = self.create_connection()
        cur = conn.cursor()
        sql = 'SELECT MAX(unix_timestamp) FROM trips;'
        cur.execute(sql)
        row = cur.fetchone()
        conn.close()
        try:
            return datetime.datetime.fromtimestamp(int(row[0])) if row[0] is not None else None
        except Exception as e:
            logging.exception(e)
            return None

    def save_trip(self, date: datetime.datetime, trip: TripInfo):
        """
        Save a trip record to the 'trips' table.
        :param date: date of the trip
        :param trip: trip data
        """
        conn = self.create_connection()
        cur = conn.cursor()
        hours = int(trip.hhmmss[:2])
        minutes = int(trip.hhmmss[2:4])
        seconds = int(trip.hhmmss[4:])
        timestamp = date + datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
        sql = f'''
        INSERT INTO trips(
            unix_timestamp,
            date,
            driving_time_minutes,
            idle_time_minutes,
            distance_km,
            avg_speed_kmh,
            max_speed_kmh
        )
        VALUES(
            {round(datetime.datetime.timestamp(timestamp))},
            "{timestamp.strftime("%Y-%m-%d %H:%M")}",
            {trip.drive_time},
            {trip.idle_time},
            {trip.distance},
            {trip.avg_speed},
            {trip.max_speed}
        )'''
        print(sql)
        cur.execute(sql)
        conn.commit()
        conn.close()

    def save_log(self):
        """
        Insert a new log entry into the 'log' table.
        """
        conn = self.create_connection()
        cur = conn.cursor()
        latitude = self.vehicle_client.vehicle.location_latitude or 'NULL'
        longitude = self.vehicle_client.vehicle.location_longitude or 'NULL'
        odometer = int(self.vehicle_client.vehicle.odometer) if self.vehicle_client.vehicle.odometer else 0
        last_vehicle_update_ts = max(
            self.vehicle_client.vehicle.last_updated_at,
            self.vehicle_client.vehicle.location_last_updated_at
        )
        sql = f'''INSERT INTO log(
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
        )
        VALUES(
            {self.vehicle_client.vehicle.ev_battery_percentage},
            {self.vehicle_client.vehicle.car_battery_percentage},
            {self.vehicle_client.vehicle.ev_driving_range},
            '{datetime.datetime.now()}',
            {round(datetime.datetime.timestamp(datetime.datetime.now()))},
            '{last_vehicle_update_ts}',
            {round(datetime.datetime.timestamp(last_vehicle_update_ts))},
            {latitude},
            {longitude},
            {odometer},
            {1 if self.vehicle_client.vehicle.ev_battery_is_charging else 0},
            {1 if self.vehicle_client.vehicle.engine_is_running else 0},
            {self.vehicle_client.charging_power_in_kilowatts},
            {self.vehicle_client.vehicle.ev_charge_limits_ac or 100},
            {self.vehicle_client.vehicle.ev_charge_limits_dc or 100},
            {self.vehicle_client.vehicle.air_temperature},
            "{self.vehicle_client.vehicle.data}"
        )'''
        print(sql)
        cur.execute(sql)
        conn.commit()
        conn.close()

    def save_daily_stats(self):
        """Insert or update daily statistics in the 'stats_per_day' table."""
        conn = self.create_connection()
        cur = conn.cursor()
        sql = 'SELECT date FROM stats_per_day;'
        cur.execute(sql)
        rows = cur.fetchall()
        current_date = datetime.datetime.now().date()
        saved_dates = [row[0] for row in rows]

        for day in self.vehicle_client.vehicle.daily_stats:
            # Skip the current day as it might change during the day
            if day.date.date() == current_date:
                continue

            # Skip already saved days
            day_str = day.date.strftime("%Y-%m-%d")
            if day_str in saved_dates:
                continue

            # Calculate consumption values for new days only
            average_consumption = 0
            average_consumption_regen_deducted = 0
            if day.distance > 0:
                average_consumption = day.total_consumed / (100 / day.distance)
                average_consumption_regen_deducted = (day.total_consumed - day.regenerated_energy) / (100 / day.distance)

            # Insert new day's data
            sql = f'''
            INSERT INTO stats_per_day(
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
            )
            VALUES(
                '{day_str}',
                {round(datetime.datetime.timestamp(day.date))},
                {round(day.total_consumed / 1000, 1)},
                {round(day.engine_consumption / 1000, 1)},
                {round(day.climate_consumption / 1000, 1)},
                {round(day.onboard_electronics_consumption / 1000, 1)},
                {round(day.battery_care_consumption / 1000, 1)},
                {round(day.regenerated_energy / 1000, 1)},
                {day.distance},
                {round(average_consumption / 1000, 1)},
                {round(average_consumption_regen_deducted / 1000, 1)}
            )'''
            cur.execute(sql)
            conn.commit()
            logging.info(f"Saved new daily stats for: {day_str}")

        conn.close()

    def log_error(self, exception: Exception):
        """Log an error entry into the 'errors' table."""
        conn = self.create_connection()
        cur = conn.cursor()
        sql = '''INSERT INTO errors(
            timestamp,
            unix_timestamp,
            exc_type,
            exc_args
        ) VALUES(%s, %s, %s, %s)'''
        cur.execute(sql, (
            datetime.datetime.now(),
            round(datetime.datetime.timestamp(datetime.datetime.now())),
            type(exception).__name__,
            str(exception.args)
        ))
        conn.commit()
        conn.close()

    def save_trip(self, day_date, trip):
        """Save a single trip to the database, avoiding duplicates."""
        conn = self.create_connection()
        cur = conn.cursor()
        
        # Convert trip timestamp to unix timestamp for comparison
        trip_unix_timestamp = None
        if trip.hhmmss:
            trip_datetime = self.vehicle_client._convert_trip_time_to_datetime(day_date, trip.hhmmss)
            if trip_datetime:
                trip_unix_timestamp = int(trip_datetime.timestamp())
        
        if trip_unix_timestamp:
            # Check if this trip already exists
            cur.execute("SELECT COUNT(*) FROM trips WHERE unix_timestamp = %s", (trip_unix_timestamp,))
            if cur.fetchone()[0] > 0:
                print(f"Trip already exists for timestamp {trip_unix_timestamp}, skipping...")
                conn.close()
                return
        
        # Insert new trip
        sql = '''INSERT INTO trips(
            unix_timestamp,
            date,
            driving_time_minutes,
            idle_time_minutes,
            distance_km,
            avg_speed_kmh,
            max_speed_kmh
        ) VALUES(%s, %s, %s, %s, %s, %s, %s)'''
        
        # Get the full datetime with hour, minute, second for the date field
        trip_datetime = self.vehicle_client._convert_trip_time_to_datetime(day_date, trip.hhmmss)
        date_string = trip_datetime.strftime("%Y-%m-%d %H:%M") if trip_datetime else day_date.strftime("%Y-%m-%d")
        
        cur.execute(sql, (
            trip_unix_timestamp,
            date_string,
            trip.drive_time if trip.drive_time else 0,
            trip.idle_time if trip.idle_time else 0,
            int(trip.distance) if trip.distance else 0,
            int(trip.avg_speed) if trip.avg_speed else 0,
            int(trip.max_speed) if trip.max_speed else 0
        ))
        
        conn.commit()
        conn.close()
        print(f"Saved new trip for {day_date.strftime('%Y-%m-%d')}")

    def get_most_recent_saved_trip_timestamp(self):
        """Get the timestamp of the most recently saved trip."""
        conn = self.create_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT MAX(unix_timestamp) FROM trips")
        result = cur.fetchone()
        conn.close()
        
        if result and result[0]:
            return datetime.datetime.fromtimestamp(result[0])
        return None
