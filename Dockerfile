FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-install Python dependencies (changes rarely)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for mounted volumes
RUN mkdir -p /app/recordings /app/models /app/server /app/logs

# Expose Central Server port
EXPOSE 8765

ENV PYTHONUNBUFFERED=1
ENV SERVER_HOST=0.0.0.0
ENV SERVER_PORT=8765

CMD ["python", "run_server.py"]
