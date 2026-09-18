import argparse
import logging
import os
import sys

from VehicleClient import VehicleClient


def main():
    vehicle_client = VehicleClient()

    parser = argparse.ArgumentParser(description="Kia Hyundai Vehicle Tracker")
    parser.add_argument("--interval", type=int, help="Refresh interval in seconds")
    parser.add_argument(
        "--action",
        type=str,
        choices=["refresh", "trips", "daily_stats", "all"],
        default="refresh",
        help="Action to perform",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.interval:
        vehicle_client.interval_in_seconds = args.interval
    else:
        vehicle_client.interval_in_seconds = vehicle_client.CACHED_REFRESH_INTERVAL

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        if args.action == "refresh":
            print("Performing vehicle data refresh...")
            vehicle_client.refresh()
            print("Vehicle data refresh completed.")

        elif args.action == "trips":
            print("Processing and saving trip information...")
            vehicle_client.vm.check_and_refresh_token()
            vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["UVO_VEHICLE_UUID"])
            vehicle_client.vm.update_all_vehicles_with_cached_state()

            if (
                vehicle_client.vehicle
                and hasattr(vehicle_client.vehicle, "daily_stats")
                and vehicle_client.vehicle.daily_stats
            ):
                vehicle_client.process_trips()
                print("Trip information processed and saved.")
            else:
                print("No trip data available to process.")

        elif args.action == "daily_stats":
            print("Saving daily statistics...")
            vehicle_client.vm.check_and_refresh_token()
            vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["UVO_VEHICLE_UUID"])
            vehicle_client.vm.update_all_vehicles_with_cached_state()

            if (
                vehicle_client.vehicle
                and hasattr(vehicle_client.vehicle, "daily_stats")
                and vehicle_client.vehicle.daily_stats
            ):
                vehicle_client.db_client.save_daily_stats()
                print("Daily statistics saved.")
            else:
                print("No daily stats available to save.")

        elif args.action == "all":
            # refresh() already does everything "all" advertises - trip processing,
            # daily stats and a log entry - but only when there is actually new data
            # (the odometer moved since the last saved entry). It used to be followed
            # by an unconditional repeat of process_trips()/save_daily_stats()/save_log()
            # here, which cost at least 2 extra UVO API calls per run for no new data,
            # eating into the 200 requests/day budget.
            print("Performing full refresh (data + trips + daily stats + logs)...")
            vehicle_client.refresh()
            print("Full refresh completed.")

    except Exception as e:
        print(f"Error during {args.action}: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
