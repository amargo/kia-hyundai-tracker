import os
import time
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from hyundai_kia_connect_api.exceptions import RateLimitingError, InvalidAPIResponseError
from pytz import timezone as pytz_timezone
from datetime import datetime, timezone
from VehicleClient import VehicleClient
from Logger import Logger

app = Flask(__name__)

vehicle_client = None
logger = Logger.get_logger(__name__)

def safe_update_vehicle_state():
    """
    Safely update vehicle state with automatic token refresh on expiry
    Returns True on success, False on failure
    """
    try:
        vehicle_client.vm.update_all_vehicles_with_cached_state()
        return True
    except Exception as e:
        # If token expired or other error, try to refresh
        should_retry = vehicle_client.handle_api_exception(e)
        if should_retry:
            try:
                # Retry after token refresh
                vehicle_client.vm.update_all_vehicles_with_cached_state()
                return True
            except Exception as retry_e:
                logger.exception("Failed to update vehicle state even after token refresh:", exc_info=retry_e)
                return False
        else:
            logger.exception("Failed to update vehicle state:", exc_info=e)
            return False

@app.route("/")
def index():
    """List all available endpoints"""
    endpoints = {
        "/": "This help page",
        "/status": "Get detailed vehicle status (battery, range, charging state, etc.)",
        "/battery": "Get battery percentage",
        "/force_refresh": "Force refresh vehicle state",
        "/force_trips": "Force refresh and save trip information to database",
        "/force_daily_stats": "Force save daily statistics to database",
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

@app.route("/force_trips")
def force_trips():
    """Force refresh and save trip information to database"""
    try:
        # First ensure we have fresh vehicle data
        if not safe_update_vehicle_state():
            return jsonify({
                "action": "force_trips",
                "status": "error",
                "message": "Failed to update vehicle state"
            }), 500

        # Process and save trips to database
        if vehicle_client.vehicle and hasattr(vehicle_client.vehicle, 'daily_stats') and vehicle_client.vehicle.daily_stats:
            vehicle_client.process_trips()
            return jsonify({
                "action": "force_trips",
                "status": "success",
                "message": "Trip information refreshed and individual trips saved to database"
            })
        else:
            return jsonify({
                "action": "force_trips",
                "status": "warning",
                "message": "No daily stats available for trip processing"
            })
    except Exception as e:
        logger.exception("Error during force trips operation:", exc_info=e)
        return jsonify({
            "action": "force_trips",
            "status": "error",
            "message": f"Failed to process trips: {str(e)}"
        }), 500

@app.route("/force_daily_stats")
def force_daily_stats():
    """Force save daily statistics to database"""
    try:
        # First ensure we have fresh vehicle data
        if not safe_update_vehicle_state():
            return jsonify({
                "action": "force_daily_stats",
                "status": "error",
                "message": "Failed to update vehicle state"
            }), 500

        # Save daily statistics to database
        if vehicle_client.vehicle and hasattr(vehicle_client.vehicle, 'daily_stats') and vehicle_client.vehicle.daily_stats:
            vehicle_client.db_client.save_daily_stats()
            return jsonify({
                "action": "force_daily_stats",
                "status": "success",
                "message": "Daily statistics saved to database"
            })
        else:
            return jsonify({
                "action": "force_daily_stats",
                "status": "warning",
                "message": "No daily stats available to save"
            })
    except Exception as e:
        logger.exception("Error during force daily stats operation:", exc_info=e)
        return jsonify({
            "action": "force_daily_stats",
            "status": "error",
            "message": f"Failed to save daily stats: {str(e)}"
        }), 500

@app.route("/status")
def get_cached_status():
    if not safe_update_vehicle_state():
        return jsonify({
            "status": "error",
            "message": "Failed to update vehicle state"
        }), 500

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
    if not safe_update_vehicle_state():
        return "Error: Failed to update vehicle state", 500

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
    try:
        if not is_within_active_hours():
            logger.info("Outside active hours, skipping scheduled refresh")
            return

        if not is_aux_battery_ok():
            logger.info("Auxiliary battery level too low, skipping scheduled refresh")
            return

        logger.info("Starting scheduled refresh")
        
        # Step 1: Update vehicle state
        try:
            update_vehicle_state()
            logger.info("Step 1/2: Vehicle state updated successfully")
        except Exception as e:
            logger.error(f"Step 1/2: Failed to update vehicle state: {str(e)}")
            return

        # Step 2: Process and save data
        try:
            if vehicle_client.vehicle:
                # Save current state to database
                vehicle_client.save_log()
                logger.info("Step 2/2: Vehicle data processed and saved successfully")
            else:
                logger.warning("Step 2/2: No vehicle data available to process")
        except Exception as e:
            logger.error(f"Step 2/2: Failed to process vehicle data: {str(e)}")
        
        logger.info("Scheduled refresh completed")
        
    except Exception as e:
        logger.error(f"Scheduled refresh failed: {str(e)}")

def scheduled_trip_processing():
    """Scheduled trip processing - runs every 2 hours during day"""
    try:
        logger.info("Starting scheduled trip processing")
        
        # Ensure we have fresh vehicle data
        if not safe_update_vehicle_state():
            logger.error("Failed to update vehicle state for scheduled trip processing")
            return
        
        # Process trips if data is available
        if vehicle_client.vehicle and hasattr(vehicle_client.vehicle, 'daily_stats') and vehicle_client.vehicle.daily_stats:
            vehicle_client.process_trips()
            logger.info("Scheduled trip processing completed successfully")
        else:
            logger.warning("No trip data available for scheduled processing")
            
    except Exception as e:
        logger.error(f"Scheduled trip processing failed: {str(e)}")

def scheduled_daily_stats():
    """Scheduled daily stats saving - runs once per day at 23:30"""
    try:
        logger.info("Starting scheduled daily stats saving")
        
        # Ensure we have fresh vehicle data
        if not safe_update_vehicle_state():
            logger.error("Failed to update vehicle state for scheduled daily stats")
            return
        
        # Save daily stats if data is available
        if vehicle_client.vehicle and hasattr(vehicle_client.vehicle, 'daily_stats') and vehicle_client.vehicle.daily_stats:
            vehicle_client.db_client.save_daily_stats()
            logger.info("Scheduled daily stats saving completed successfully")
        else:
            logger.warning("No daily stats data available for scheduled saving")
            
    except Exception as e:
        logger.error(f"Scheduled daily stats saving failed: {str(e)}")

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()

    # Initialize scheduler
    scheduler_timezone = os.getenv('UVO_TRACKER_TIMEZONE')
    if scheduler_timezone:
        scheduler = BackgroundScheduler(timezone=pytz_timezone(scheduler_timezone))
    else:
        scheduler = BackgroundScheduler()
    refresh_interval = int(os.getenv('REFRESH_INTERVAL_MINUTES', '30'))
    # Add scheduled jobs
    scheduler.add_job(scheduled_refresh, 'interval', minutes=refresh_interval)
    
    # Add trip processing job - every 2 hours during day
    scheduler.add_job(scheduled_trip_processing, 'cron', hour='8-22/2', minute=0)
    
    # Add daily stats job - once per day at 23:30
    scheduler.add_job(scheduled_daily_stats, 'cron', hour=23, minute=30)
    
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
