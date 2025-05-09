import os
import time
from datetime import datetime, timezone
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from hyundai_kia_connect_api.exceptions import RateLimitingError, InvalidAPIResponseError
from pytz import timezone
from VehicleClient import VehicleClient
from Logger import Logger

app = Flask(__name__)

vehicle_client = None
logger = Logger.get_logger(__name__)

@app.route("/")
def index():
    """List all available endpoints"""
    endpoints = {
        "/": "This help page",
        "/status": "Get detailed vehicle status (battery, range, charging state, etc.)",
        "/battery": "Get battery percentage",
        "/force_refresh": "Force refresh vehicle state",
        "/charge": "Control charging (parameters: action=[start|stop], synchronous=[true|false])"
    }
    return jsonify({
        "available_endpoints": endpoints,
        "note": "All endpoints return JSON except /battery which returns plain text"
    })

@app.route("/force_refresh")
def force_refresh():
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    vehicle_client.vm.update_vehicle_with_cached_state(vehicle_client.vehicle.id)
    vehicle_client.save_log()
    return jsonify({"action": "force_refresh", "status": "success"})

@app.route("/status")
def get_cached_status():
    vehicle_client.vm.update_all_vehicles_with_cached_state()

    # Convert both timestamps to UTC for comparison
    last_vehicle_update = vehicle_client.vehicle.last_updated_at
    if not last_vehicle_update.tzinfo:
        last_vehicle_update = last_vehicle_update.replace(tzinfo=timezone.utc)
    
    last_db_update = vehicle_client.db_client.get_last_update_timestamp()
    if not last_db_update.tzinfo:
        last_db_update = last_db_update.replace(tzinfo=timezone.utc)

    if last_vehicle_update > last_db_update:
        vehicle_client.save_log()

    result = {
        "battery_percentage": vehicle_client.vehicle.ev_battery_percentage,
        "accessory_battery_percentage": vehicle_client.vehicle.car_battery_percentage,
        "estimated_range_km": vehicle_client.vehicle.ev_driving_range,
        "last_vehicule_update_timestamp": vehicle_client.vehicle.last_updated_at.isoformat(),
        "odometer": vehicle_client.vehicle.odometer,
        "charging": vehicle_client.vehicle.ev_battery_is_charging,
        "engine_is_running": vehicle_client.vehicle.engine_is_running,
        "rough_charging_power_estimate_kw": vehicle_client.charging_power_in_kilowatts,
        "ac_charge_limit_percent": vehicle_client.vehicle.ev_charge_limits_ac,
        "dc_charge_limit_percent": vehicle_client.vehicle.ev_charge_limits_dc,
    }
    return jsonify(result)

@app.route("/battery")
def get_battery_soc():
    vehicle_client.vm.update_all_vehicles_with_cached_state()
    
    # Convert both timestamps to UTC for comparison
    last_vehicle_update = vehicle_client.vehicle.last_updated_at
    if not last_vehicle_update.tzinfo:
        last_vehicle_update = last_vehicle_update.replace(tzinfo=timezone.utc)
    
    last_db_update = vehicle_client.db_client.get_last_update_timestamp()
    if not last_db_update.tzinfo:
        last_db_update = last_db_update.replace(tzinfo=timezone.utc)
    
    if last_vehicle_update > last_db_update:
        vehicle_client.save_log()
    return str(vehicle_client.vehicle.ev_battery_percentage)

@app.route("/charge")
def toggle_charge():
    action = request.args.get('action', 'start')
    wait_for_response = bool(request.args.get('synchronous', False))

    if action == "start":
        vehicle_client.vm.start_charge(vehicle_client.vehicle.id)
    elif action == "stop":
        vehicle_client.vm.stop_charge(vehicle_client.vehicle.id)
    else:
        return jsonify({"error": "Invalid action. Use 'start' or 'stop'"}), 400

    if wait_for_response:
        time.sleep(5)
        status = vehicle_client.vm.get_last_action_status(vehicle_client.vehicle.id)
        return jsonify({"action": "charge_" + action, "status": status})
    
    return jsonify({"action": "charge_" + action, "status": "command_sent"})

def is_within_active_hours():
    """Check if current time is within the configured active hours"""
    current_hour = datetime.now().hour
    start_hour = int(os.getenv('REFRESH_START_HOUR', '6'))
    end_hour = int(os.getenv('REFRESH_END_HOUR', '22'))
    return start_hour <= current_hour < end_hour

def get_min_aux_battery_soc():
    """Get minimum auxiliary battery SOC threshold from env, ensuring it's not below 60%"""
    return max(60, int(os.getenv('MIN_AUX_BATTERY_SOC', '80')))

def is_aux_battery_ok():
    """Check if auxiliary battery level is above minimum threshold"""
    min_aux_soc = get_min_aux_battery_soc()    
    current_soc = vehicle_client.vehicle.car_battery_percentage
    
    if current_soc is None:
        logger.warning("Auxiliary battery SOC is not available")
        return False
        
    logger.debug(f"Current auxiliary battery SOC: {current_soc}%")
    return current_soc >= min_aux_soc

def update_vehicle_state():
    """Force refresh and update vehicle state"""
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    vehicle_client.vm.update_vehicle_with_cached_state(vehicle_client.vehicle.id)

def scheduled_refresh():
    """Perform scheduled refresh if within active hours and auxiliary battery is OK"""
    if not is_within_active_hours():
        return

    try:
        vehicle_client.vm.update_vehicle_with_cached_state(vehicle_client.vehicle.id)

        if not is_aux_battery_ok():
            logger.warning(f"Auxiliary battery SOC ({vehicle_client.vehicle.car_battery_percentage}%) is below minimum threshold ({get_min_aux_battery_soc()}%), skipping refresh")
            return

        logger.info("=== Starting scheduled refresh ===")
        logger.info("Step 1/2: Requesting fresh data from vehicle...")
        update_vehicle_state()
        logger.info("Step 1/2: Successfully received fresh data from vehicle")
    except InvalidAPIResponseError:
        logger.warning("Token expired, attempting to refresh...")
        try:
            vehicle_client.vm.check_and_refresh_token()
            update_vehicle_state()
            logger.info("Successfully refreshed token and updated vehicle state")
        except Exception as refresh_error:
            logger.error(f"Failed to refresh token or update vehicle state: {str(refresh_error)}")
            return
    except Exception as e:
        logger.error(f"Failed during vehicle refresh: {str(e)}")
        return

    try:
        logger.info("Step 2/2: Processing and saving vehicle data...")
        vehicle_client.refresh()
        logger.info("Step 2/2: Successfully processed and saved vehicle data")
        logger.info("=== Scheduled refresh completed successfully ===")
    except Exception as e:
        logger.error(f"Step 2/2: Failed to process vehicle data: {str(e)}")

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Initialize scheduler
    scheduler_timezone = os.getenv('UVO_TRACKER_TIMEZONE')
    if scheduler_timezone:
        scheduler = BackgroundScheduler(timezone=timezone(scheduler_timezone))
    else:
        scheduler = BackgroundScheduler()
    refresh_interval = int(os.getenv('REFRESH_INTERVAL_MINUTES', '30'))
    scheduler.add_job(scheduled_refresh, 'interval', minutes=refresh_interval)
    scheduler.start()
    
    try:
        # Initialize vehicle client
        vehicle_client = VehicleClient()
        while True:
            try:
                vehicle_client.vm.check_and_refresh_token()
                break
            except RateLimitingError:
                logger.error("Got rate limited. Will try again in 1 hour.")
                time.sleep(60 * 60)

        vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["UVO_VEHICLE_UUID"])

        # Run Flask app
        app.run(host='0.0.0.0', 
                port=int(os.getenv('PORT', 5000)),
                debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')  
    except KeyboardInterrupt:
        scheduler.shutdown()
