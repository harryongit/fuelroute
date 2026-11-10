FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=fuelroute.settings

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Run database migrations and load fuel stations into SQLite database
RUN python manage.py migrate --noinput && \
    python manage.py load_stations

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

