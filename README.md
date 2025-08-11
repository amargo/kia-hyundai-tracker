# Kia/Hyundai Vehicle Tracker

Track your Kia/Hyundai vehicle using the Kia Connect / Bluelink API. This application provides real-time tracking of your vehicle's status, including battery level, charging state, location, and more.

## Features

- Real-time vehicle status monitoring
- Battery level and charging status tracking
- Location tracking and trip history
- Daily driving statistics collection
- Configurable refresh intervals
- REST API for easy integration
- Support for both SQLite and MySQL databases
- Grafana dashboard support
- Automated trip processing and duplicate prevention
- Comprehensive logging system

## Installation

### Using Docker (Recommended)

The easiest way to run the tracker is using Docker. Pre-built images are available on both Docker Hub and GitHub Container Registry.

```bash
# Pull from Docker Hub
docker pull gszoboszlai/kia-hyundai-tracker:latest

# Or pull from GitHub Container Registry
docker pull ghcr.io/amargo/kia-hyundai-tracker:latest
```

#### Quick Start with Docker Compose (Recommended)

This project includes a [`docker-compose.yml`](docker-compose.yml) file that sets up both the tracker and a MySQL database.

1. Create a `.env` file based on the example:
```bash
cp .env.example .env
```

2. Edit the `.env` file with your credentials and preferences

#### Timezone configuration for scheduler

You can set the timezone for the periodic background tasks using the `UVO_TRACKER_TIMEZONE` environment variable. If not set, the default is `Europe/Budapest`.

Example in your `.env` file:
```env
UVO_TRACKER_TIMEZONE=Europe/Budapest
```

This ensures that all scheduled tasks will run according to your local time.

3. Start the services using Docker Compose:
```bash
docker-compose up -d
```

This will start both the tracker and a MySQL database. The database data will be persisted in a Docker volume.

To view the logs:
```bash
# View all logs
docker-compose logs

# Follow logs in real-time
docker-compose logs -f

# View logs for a specific service
docker-compose logs tracker
docker-compose logs db
```

To stop the services:
```bash
docker-compose down
```

#### Alternative: Running with Docker (Standalone)

If you prefer to use your own database, you can run just the tracker container:

```bash
docker run -d \
  --name kia-tracker \
  --restart unless-stopped \
  -p 5000:5000 \
  --env-file .env \
  gszoboszlai/kia-hyundai-tracker:latest
```

### Manual Installation

1. Clone the repository:
```bash
git clone https://github.com/amargo/kia-hyundai-tracker.git
cd kia-hyundai-tracker
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install requirements:
```bash
pip install -r requirements.txt
```

4. Set up the environment:
```bash
cp .env.example .env
# Edit .env with your credentials and preferences
```

## Configuration

The application is configured using environment variables. Here are the key settings:

### Required Settings
- `UVO_USERNAME`: Your Kia Connect/Bluelink email
- `UVO_PASSWORD`: Your Kia Connect/Bluelink password
- `UVO_VEHICLE_UUID`: Your vehicle's UUID
- `UVO_PIN`: Your Kia Connect/Bluelink PIN code

### Optional Settings
- `REFRESH_START_HOUR`: Start hour for vehicle updates (default: 7)
- `REFRESH_END_HOUR`: End hour for vehicle updates (default: 22)
- `REFRESH_INTERVAL_MINUTES`: Minutes between updates (default: 30)
- `HTTP_SERVER_PASSWORD`: Password for the HTTP API

### Database Configuration
By default, SQLite is used. For MySQL:
```env
UVO_DB_HOST=your-mysql-host
UVO_DB_USER=your-username
UVO_DB_PASSWORD=your-password
UVO_DB_NAME=your-database
```

## Usage

### Command Line Interface

The application supports multiple actions via command line:

```bash
# Basic vehicle data refresh
python main.py --action refresh --verbose

# Process and save trip information only
python main.py --action trips --verbose

# Save daily statistics only
python main.py --action daily_stats --verbose

# Complete data collection (refresh + trips + daily stats + logs)
python main.py --action all --verbose
```

#### Available Actions

- **`refresh`** - Updates vehicle status, battery, location data
- **`trips`** - Processes trip history with duplicate prevention
- **`daily_stats`** - Saves daily driving statistics
- **`all`** - Complete data collection including all above + log entries

### Scheduling with Cron

For automated data collection, set up cron jobs:

```bash
# Edit crontab
crontab -e

# Add these entries for automated collection:

# Complete data collection every 6 hours
0 */6 * * * cd /path/to/kia-hyundai-tracker && python main.py --action all --verbose >> /var/log/kia-tracker.log 2>&1

# Trip processing every 2 hours (during day)
0 8-22/2 * * * cd /path/to/kia-hyundai-tracker && python main.py --action trips --verbose >> /var/log/kia-tracker.log 2>&1

# Daily stats once per day at 23:30
30 23 * * * cd /path/to/kia-hyundai-tracker && python main.py --action daily_stats --verbose >> /var/log/kia-tracker.log 2>&1
```

### HTTP API Endpoints

- `/status` - Get detailed vehicle status
- `/battery` - Get battery percentage
- `/force_refresh` - Force refresh vehicle state
- `/force_trips` - Manually trigger trip processing
- `/force_daily_stats` - Manually save daily statistics
- `/charge` - Control charging (start/stop)

Example API calls:
```bash
# Get vehicle status
curl http://localhost:5000/status

# Get battery level
curl http://localhost:5000/battery

# Start charging
curl "http://localhost:5000/charge?action=start"

# Stop charging
curl "http://localhost:5000/charge?action=stop"
```

## Building from Source

```bash
# Build Docker image locally
docker build -t kia-hyundai-tracker .

# Run the locally built image
docker run -d \
  --name kia-tracker \
  --restart unless-stopped \
  -p 5000:5000 \
  --env-file .env \
  -v $(pwd)/database.db:/app/database.db \
  kia-hyundai-tracker
```

### Running CLI Commands in Docker

The Docker container runs the HTTP server by default, but you can also execute CLI commands:

```bash
# Execute one-time CLI commands in running container
docker exec kia-tracker python main.py --action all --verbose

# Run CLI commands in a new container (without HTTP server)
docker run --rm --env-file .env \
  -v $(pwd)/database.db:/app/database.db \
  kia-hyundai-tracker python main.py --action trips --verbose

# Interactive shell for debugging
docker exec -it kia-tracker /bin/bash
```

### Docker Scheduling Options

#### Option 1: Use Docker with External Cron
```bash
# Add to host crontab for CLI-based scheduling
0 */6 * * * docker exec kia-tracker python main.py --action all --verbose >> /var/log/kia-tracker.log 2>&1
```

#### Option 2: Use HTTP Server with Built-in Scheduling
The HTTP server includes automatic scheduling:
- **Vehicle refresh**: Every 30 minutes (configurable via `REFRESH_INTERVAL_MINUTES`)
- **Trip processing**: Every 2 hours during day (8:00-22:00)
- **Daily stats**: Once per day at 23:30

```bash
# Run with built-in scheduler (default behavior)
docker run -d \
  --name kia-tracker \
  --restart unless-stopped \
  -p 5000:5000 \
  --env-file .env \
  kia-hyundai-tracker
```

#### Option 3: Manual HTTP API Triggers
```bash
# Trigger operations via HTTP API
curl http://localhost:5000/force_trips
curl http://localhost:5000/force_daily_stats
curl http://localhost:5000/force_refresh
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.