import logging
import os
import time
from datetime import datetime, timezone
import threading
from apscheduler.schedulers.background import BackgroundScheduler

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from VehicleClient import VehicleClient
from hyundai_kia_connect_api import ClimateRequestOptions
from hyundai_kia_connect_api.const import OrderStatus
from hyundai_kia_connect_api.exceptions import DeviceIDError, RateLimitingError

app = Flask(__name__)

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

def scheduled_refresh():
    """Perform scheduled refresh if within active hours"""
    if is_within_active_hours():
        try:
            vehicle_client.refresh()
            logging.info("Scheduled refresh completed successfully")
        except Exception as e:
            logging.error(f"Error during scheduled refresh: {e}")

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Configure logging
    logging.basicConfig(level=logging.DEBUG)
    
    # Initialize scheduler
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
                logging.error("Got rate limited. Will try again in 1 hour.")
                time.sleep(60 * 60)

        vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])

        # Run Flask app
        app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
    except KeyboardInterrupt:
        scheduler.shutdown()
