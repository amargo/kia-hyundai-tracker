FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install build dependencies (pycryptodome and friends may need to compile from
# source depending on the platform/wheel availability)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

RUN pip install --no-cache-dir wheel==0.42.0 setuptools==69.2.0 && \
    pip install --no-cache-dir -r requirements.txt

# Copy only what's needed at runtime - db/db_schema.sql is required, it's read on
# first start to auto-create the MySQL schema.
COPY main.py VehicleClient.py DatabaseClient.py http_server.py Logger.py ./
COPY db/ ./db/

# Create a non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose the port the app runs on
EXPOSE 5000

# Command to run the application
CMD ["python", "http_server.py"]
