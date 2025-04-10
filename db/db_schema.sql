START TRANSACTION;

CREATE TABLE IF NOT EXISTS `stats_per_day` (
  `date` VARCHAR(10),
  `unix_timestamp` INT,
  `total_consumed_kwh` DOUBLE,
  `engine_consumption_kwh` DOUBLE,
  `climate_consumption_kwh` DOUBLE,
  `onboard_electronics_consumption_kwh` DOUBLE,
  `battery_care_consumption_kwh` DOUBLE,
  `regenerated_energy_kwh` DOUBLE,
  `distance` INT,
  `average_consumption_kwh` DOUBLE,
  `average_consumption_regen_deducted_kwh` DOUBLE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `log` (
  `battery_percentage` INT,
  `accessory_battery_percentage` INT,
  `estimated_range_km` INT,
  `timestamp` VARCHAR(255),
  `unix_timestamp` INT,
  `last_vehicule_update_timestamp` VARCHAR(255),
  `unix_last_vehicle_update_timestamp` INT,
  `latitude` VARCHAR(255),
  `longitude` VARCHAR(255),
  `odometer` INT,
  `charging` INT,
  `engine_is_running` INT,
  `rough_charging_power_estimate_kw` DOUBLE,
  `returned_api_status` VARCHAR(255),
  `ac_charge_limit_percent` INT,
  `dc_charge_limit_percent` INT,
  `target_climate_temperature` INT,
  `raw_api_data` TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `errors` (
  `timestamp` VARCHAR(255),
  `unix_timestamp` INT,
  `exc_type` VARCHAR(255),
  `exc_args` TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `trips` (
  `unix_timestamp` INT,
  `date` VARCHAR(255),
  `driving_time_minutes` INT,
  `idle_time_minutes` INT,
  `distance_km` INT,
  `avg_speed_kmh` INT,
  `max_speed_kmh` INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

COMMIT;
