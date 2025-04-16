# Kia/Hyundai Vehicle Tracker

Track your Kia/Hyundai vehicle using the Kia Connect / Bluelink API. This application provides real-time tracking of your vehicle's status, including battery level, charging state, location, and more.

## Features

- Real-time vehicle status monitoring
- Battery level and charging status tracking
- Location tracking
- Configurable refresh intervals
- REST API for easy integration
- Support for both SQLite and MySQL databases
- Grafana dashboard support

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

1. Create a `.env` file based on the example:
```bash
cp .env.example .env
```

2. Edit the `.env` file with your credentials and preferences

#### Timezone configuration for scheduler

You can set the timezone for the periodic background tasks using the `KIA_TRACKER_TIMEZONE` environment variable. If not set, the default is `Europe/Budapest`.

Example in your `.env` file:
```env
KIA_TRACKER_TIMEZONE=Europe/Budapest
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
- `KIA_USERNAME`: Your Kia Connect/Bluelink email
- `KIA_PASSWORD`: Your Kia Connect/Bluelink password
- `KIA_VEHICLE_UUID`: Your vehicle's UUID
- `KIA_PIN`: Your Kia Connect/Bluelink PIN code

### Optional Settings
- `REFRESH_START_HOUR`: Start hour for vehicle updates (default: 7)
- `REFRESH_END_HOUR`: End hour for vehicle updates (default: 22)
- `REFRESH_INTERVAL_MINUTES`: Minutes between updates (default: 30)
- `HTTP_SERVER_PASSWORD`: Password for the HTTP API

### Database Configuration
By default, SQLite is used. For MySQL:
```env
KIA_DB_HOST=your-mysql-host
KIA_DB_USER=your-username
KIA_DB_PASSWORD=your-password
KIA_DB_NAME=your-database
```

## Usage

### HTTP API Endpoints

- `/status` - Get detailed vehicle status
- `/battery` - Get battery percentage
- `/force_refresh` - Force refresh vehicle state
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

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.