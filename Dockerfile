FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create data directories
RUN mkdir -p /data/sync /data/cache /data/backup

# Non-root user for security
RUN useradd -m -u 1000 rommsync && \
    chown -R rommsync:rommsync /app /data
USER rommsync

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from pathlib import Path; exit(0 if Path('/data/sync').exists() else 1)"

# Default command
CMD ["python", "src/romm_sync.py"]
