"""Configuration management using environment variables."""
import os
from pathlib import Path


class Config:
    """Application configuration from environment variables with sensible defaults."""

    # ROMM API Configuration
    ROMM_URL = os.getenv("ROMM_URL", "")
    ROMM_USERNAME = os.getenv("ROMM_USERNAME", "")
    ROMM_PASSWORD = os.getenv("ROMM_PASSWORD", "")
    ROMM_UPLOAD_METHOD = os.getenv("ROMM_UPLOAD_METHOD", "API")  # valid options: API, FILE

    # Sync Configuration
    SYNC_FOLDER = Path(os.getenv("SYNC_FOLDER", "/data/sync"))
    CACHE_FILE = Path(os.getenv("CACHE_FILE", "/data/cache/local_games.json"))

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Performance Configuration
    API_TIMEOUT = int(os.getenv("API_TIMEOUT", "30"))
    API_PAGE_SIZE = int(os.getenv("API_PAGE_SIZE", "50"))

    # Sync Schedule (in seconds)
    SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "300"))

    # Feature Flags
    DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
    ENABLE_METRICS = os.getenv("ENABLE_METRICS", "false").lower() == "true"

    @classmethod
    def validate(cls):
        """Validate required configuration values.

        Raises:
            ValueError: If required configuration is missing or invalid.
        """
        errors = []

        if not cls.ROMM_URL:
            errors.append("ROMM Host URL is required!")
        if not cls.ROMM_PASSWORD:
            errors.append("ROMM_PASSWORD is required!")

        if not cls.ROMM_USERNAME:
            errors.append("ROMM_USERNAME is required!")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return True
