"""Configuration management using environment variables."""
import os
import logging
from pathlib import Path
from typing import Optional
from .romm_api_func import RommUser
from dotenv import load_dotenv
load_dotenv()


class Config:
    """Application configuration from environment variables with sensible defaults.

    All values are read from environment variables at container startup.
    Bind mount paths are hardcoded since they're defined in docker-compose.
    """

    def __init__(self,
                 romm_credentials: RommUser = RommUser(),
                 sync_dir: str | Path = "/syncdata",  # docker bind mount
                 data_dir: str | Path = "/appdata",   # docker bind mount
                 watchdog_delay_seconds: int = int(os.getenv("WATCHDOG_DELAY_SECONDS", "60")),
                 sync_mode: str = os.getenv("SYNC_MODE", "periodic"),
                 sync_interval_seconds: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "1800")),
                 log_level: str = os.getenv("LOG_LEVEL", "INFO"),
                 dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true",
                 enable_metrics: bool = os.getenv("ENABLE_METRICS", "false").lower() == "true"):
        """Initialize config from environment variables."""

        # ROMM API Configuration
        self.ROMM_CREDENTIALS: RommUser = romm_credentials

        # Sync Configuration
        self.SYNC_DIR = Path(sync_dir)
        self.SYNC_MODE = sync_mode
        self.SYNC_INTERVAL_SECONDS = sync_interval_seconds
        self.WATCHDOG_DELAY_SECONDS = watchdog_delay_seconds

        # Data/Cache Configuration
        self.DATA_DIR = Path(data_dir)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.CACHE_FILEPATH = self.DATA_DIR / f"LibraryCache_{self.ROMM_CREDENTIALS.user}.json"

        # Logging Configuration
        self.LOG_LEVEL = log_level
        self.LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

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
        if not self.SYNC_DIR or not self.SYNC_DIR.exists():
            errors.append("SYNC_DIR does not exist or is not configured!")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return True


# Global config singleton
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global Config instance.

    Auto-initializes from environment variables on first call if not already set.
    This allows internal functions to get the config without needing to pass it around.
    """
    global _config
    if _config is None:
        _config = Config()
        _config.validate()
    return _config


class LoggingConfig:
    """Centralized logging configuration with TraceLogger support."""

    class TraceLogger(logging.Logger):
        """Custom logger with TRACE level support."""
        TRACE = 5

        def trace(self, message, *args, **kwargs):
            """Log a trace-level message."""
            if self.isEnabledFor(self.TRACE):
                self._log(self.TRACE, message, *args, **kwargs)

    _initialized = False

    @classmethod
    def setup(cls, log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()) -> logging.Logger:
        """Configure logging with a lower level TraceLogger and return root logger."""
        
        if cls._initialized:
            return logging.getLogger()

        if log_level is None:
            log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        else:
            log_level = log_level.upper()

        # Set custom logger class and add TRACE level
        logging.setLoggerClass(cls.TraceLogger)
        logging.addLevelName(cls.TraceLogger.TRACE, "TRACE")

        # Configure basicConfig
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        cls._initialized = True

        return logging.getLogger()


# Initialize logging on module import
LoggingConfig.setup()

# Export TRACE constant for convenience
TRACE = LoggingConfig.TraceLogger.TRACE