import argparse
from VehicleClient import VehicleClient

if __name__ == '__main__':  
    vehicle_client = VehicleClient()

    parser = argparse.ArgumentParser()

    parser.add_argument("--interval", type=int)
    args = parser.parse_args()

    if args.interval:
        vehicle_client.interval_in_seconds = args.interval
    else:
        vehicle_client.interval_in_seconds = vehicle_client.CACHED_REFRESH_INTERVAL

    vehicle_client.refresh()
