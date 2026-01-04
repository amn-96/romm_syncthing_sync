"""Configuration management using environment variables."""
import os
from pathlib import Path
from .romm_api_func import RommUser


class Config:
    """Application configuration from environment variables with sensible defaults.
    Note that __init__ uses default args to get docker environment variables. This is called at function def time, NOT call time
    so if they change after the function is imported, the change won't be used. Should not be an issue since I'm defining these ONCE at container start.

    Used by main_loop.py for continuous synchronization mode.
    See .env.example for all available configuration options and descriptions.
    """

    def __init__(self,
                 romm_credentials: RommUser = RommUser(),  # auto-populated from environment variables
                 romm_upload_method: str = os.getenv("ROMM_UPLOAD_METHOD", "API"),
                 sync_folder: str | Path = os.getenv("SYNC_FOLDER", "/data/sync"),
                 cache_file: str | Path = os.getenv("CACHE_FILE", "/data/cache/sync_cache.json"),
                 log_level: str = os.getenv("LOG_LEVEL", "INFO"),
                 sync_interval_seconds: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "300")),
                 dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true",
                 enable_metrics: bool = os.getenv("ENABLE_METRICS", "false").lower() == "true"):
        """Initialize config with optional overrides for testing."""
        
        # ROMM API Configuration
        self.ROMM_CREDENTIALS: RommUser = romm_credentials 
        self.ROMM_UPLOAD_METHOD = romm_upload_method

        # Sync Configuration
        self.SYNC_FOLDER = Path(sync_folder)
        # include the configured romm_user to identify the cache file
        cache_name = f"{Path(cache_file).stem}_{self.ROMM_CREDENTIALS.user}.json" 
        self.CACHE_FILE = Path(cache_file).parent / cache_name

        # Logging Configuration
        self.LOG_LEVEL = log_level
        self.LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # Sync Schedule (in seconds)
        self.SYNC_INTERVAL_SECONDS = sync_interval_seconds

        # Feature Flags
        self.DRY_RUN = dry_run
        self.ENABLE_METRICS = enable_metrics

    def validate(self):
        """Validate required configuration values.

        Raises:
            ValueError: If required configuration is missing or invalid.
        """
        errors = []

        if not self.ROMM_CREDENTIALS.url:
            errors.append("ROMM Host URL is required!")
        if not self.ROMM_CREDENTIALS.password:
            errors.append("ROMM_PASSWORD is required!")

        if not self.ROMM_CREDENTIALS.user:
            errors.append("ROMM_USERNAME is required!")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return True
