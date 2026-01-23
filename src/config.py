"""Configuration management using environment variables."""
import os
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, List
from loguru import logger
from .romm_api_func import RommUser
from dotenv import load_dotenv

load_dotenv()


# File filtering constants
METADATA_FILE_FILTERS = [
    "sync-conflict",
    "syncthing",
    ".stfolder",
    ".stversions",
    ".stignore",
    ".DS_Store",
    "._",
    "~",
]


class Config:
    """Application configuration from environment variables with sensible defaults.

    All values are read from environment variables at container startup.
    Bind mount paths are hardcoded since they're defined in docker-compose.
    """

    def __init__(self,
                 romm_credentials: RommUser = RommUser(),
                 save_sync_dir: Path | None = Path("/syncdata/saves") if os.getenv("SAVE_SYNC_FOLDER") else None,   # docker bind mount
                 state_sync_dir: Path | None = Path("/syncdata/states") if os.getenv("STATE_SYNC_FOLDER") else None,  # docker bind mount
                 all_sync_dir: Path | None = Path("/syncdata") if os.getenv("ALL_SYNC_FOLDER") else None,  # docker bind mount
                 watchdog_delay_seconds: int = int(os.getenv("WATCHDOG_DELAY_SECONDS", "60")),
                 sync_mode: str = os.getenv("SYNC_MODE", "periodic"),
                 sync_interval_seconds: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "1800")),
                 platform_map: Path = Path("/config/platform_mapping.yaml"),
                 log_level: str = os.getenv("LOG_LEVEL", "INFO"),
                 dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true",
                 enable_metrics: bool = os.getenv("ENABLE_METRICS", "false").lower() == "true"):
        """Initialize config from environment variables."""

        # ROMM API Configuration
        self.ROMM_CREDENTIALS: RommUser = romm_credentials

        # Sync Configuration
        self.SAVE_SYNC_DIR = save_sync_dir
        self.STATE_SYNC_DIR = state_sync_dir
        self.ALL_SYNC_DIR = all_sync_dir
        self.SYNC_MODE = sync_mode
        self.SYNC_INTERVAL_SECONDS = sync_interval_seconds
        self.WATCHDOG_DELAY_SECONDS = watchdog_delay_seconds
        self.PLATFORM_MAP_PATH = platform_map
        self.PLATFORM_MAP: Dict[str, List[str]] = {}

        # Logging Configuration
        self.LOG_LEVEL = log_level
        self.LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # Initialize logging with the specified level
        LoggingConfig.setup(log_level=self.LOG_LEVEL)

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

        # Validate sync directory configuration pattern
        # Valid patterns: (SAVE_SYNC_DIR + STATE_SYNC_DIR) XOR ALL_SYNC_DIR
        has_specific_dirs = self.SAVE_SYNC_DIR is not None and self.STATE_SYNC_DIR is not None
        has_all_dir = self.ALL_SYNC_DIR is not None

        if not (has_specific_dirs ^ has_all_dir):
            # edge case where none of them are present
            if self.SAVE_SYNC_DIR is None and self.STATE_SYNC_DIR is None and self.ALL_SYNC_DIR is None:
                errors.append("No sync directories configured. Check your .env configuration.")
            else:
                errors.append("Must specify either (SAVE_SYNC_DIR & STATE_SYNC_DIR) or ALL_SYNC_DIR, not both or neither.")

        # Validate that specified directories exist
        if self.SAVE_SYNC_DIR and not self.SAVE_SYNC_DIR.exists():
            errors.append(f"SAVE_SYNC_DIR does not exist: {self.SAVE_SYNC_DIR}")
        if self.STATE_SYNC_DIR and not self.STATE_SYNC_DIR.exists():
            errors.append(f"STATE_SYNC_DIR does not exist: {self.STATE_SYNC_DIR}")
        if self.ALL_SYNC_DIR and not self.ALL_SYNC_DIR.exists():
            errors.append(f"ALL_SYNC_DIR does not exist: {self.ALL_SYNC_DIR}")

        # Validate platform mapping file (optional - will fallback to exact matching)
        if not self.PLATFORM_MAP_PATH.exists():
            logger.warning(f"Platform mapping file not found: {self.PLATFORM_MAP_PATH}. Will use exact platform slug matching only.")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return True


# Global config singleton
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global Config instance. The first call to initialize the config is in main.py.

    Auto-initializes from environment variables on first call if not already set.
    This allows internal functions to get the config without needing to pass it around.
    """
    global _config
    if _config is None:
        _config = Config()
        _config.validate()
    return _config


class LoggingConfig:
    """Centralized logging configuration using loguru."""

    _initialized = False

    @classmethod
    def _get_filter_func(cls, log_level: str):
        """Conditional configuration for watchdog and requests because
        their matching levels are too verbose for INFO and DEBUG.
        Using the custom TRACE level to allow the option to see them.

        Returns a filter function that implements per-library level control.
        """
        def filter_func(record):
            # For intercepted stdlib logs, check the 'name' in extra dict
            # For native loguru logs, use record["name"]
            module = record.get("extra", {}).get("stdlib_name", record["name"])

            if log_level == "TRACE":
                # TRACE: Set watchdog/inotify to INFO (their debug is too verbose for our trace)
                # and requests/urllib to INFO (they log requests at INFO)
                if module.startswith("watchdog"):
                    return record["level"].no >= 20  # INFO and above
                if module.startswith(("requests", "urllib3")):
                    return record["level"].no >= 20  # INFO and above
            elif log_level == "DEBUG":
                # DEBUG: Set watchdog/inotify to INFO and requests to WARNING
                if module.startswith("watchdog"):
                    return record["level"].no >= 20  # INFO and above
                if module.startswith(("requests", "urllib3")):
                    return record["level"].no >= 30  # WARNING and above
            elif log_level == "INFO":
                # INFO: Set requests/urllib to WARNING
                if module.startswith(("requests", "urllib3")):
                    return record["level"].no >= 30  # WARNING and above
            # For all other levels (WARNING, ERROR, CRITICAL) or modules, use global level
            return True

        return filter_func

    @classmethod
    def setup(cls, log_level: str | None = None):
        """Configure loguru logging and return logger instance.
        """

        if log_level is None:
            log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        else:
            log_level = log_level.upper()

        # Skip if already initialized with the same level
        if cls._initialized and hasattr(cls, '_current_level') and cls._current_level == log_level:
            return logger

        # Allow reconfiguration if level changed
        cls._current_level = log_level

        # Remove default handler
        logger.remove()

        # Custom format function with module-colored timestamps
        def format_record(record):
            module = record.get("extra", {}).get("stdlib_name", record["name"])
            level_name = record["level"].name

            # Color code timestamp based on module
            if module.startswith("watchdog"):
                timestamp_colored = "<light-blue>{time:YYYY-MM-DD HH:mm:ss}</light-blue>"
            elif module.startswith(("requests", "urllib3", "src.romm_api_func", "romm_api_func")):
                timestamp_colored = "<magenta>{time:YYYY-MM-DD HH:mm:ss}</magenta>"
            elif module.startswith(("src.sync_orchestrator", "sync_orchestrator")):
                timestamp_colored = "<light-red>{time:YYYY-MM-DD HH:mm:ss}</light-red>"
            elif module.startswith(("src.library_classes", "library_classes", "src.games_class", "games_class")):
                timestamp_colored = "<light-magenta>{time:YYYY-MM-DD HH:mm:ss}</light-magenta>"
            else:
                # App code - use default terminal color
                timestamp_colored = "{time:YYYY-MM-DD HH:mm:ss}"

            # chose colors that should be equally visible in light and dark mode
            level_colors = {
                "TRACE": "dim",
                "DEBUG": "blue",
                "INFO": "",  # Default text color so it automatically adapts to light and dark mode
                "SUCCESS": "green",
                "WARNING": "yellow",
                "ERROR": "red",  
                "CRITICAL": "red"
            }
            level_color = level_colors.get(level_name, "")

            # Apply bold to CRITICAL separately
            if level_name == "CRITICAL":
                if level_color:
                    level_formatted = f"<bold><{level_color}>{{level: <8}}</{level_color}></bold>"
                else:
                    level_formatted = "<bold>{level: <8}</bold>"
            else:
                if level_color:
                    level_formatted = f"<{level_color}>{{level: <8}}</{level_color}>"
                else:
                    level_formatted = "{level: <8}"

            # Build format string. The timestamps are colored by module.
            return (
                f"{timestamp_colored} | "
                f"{level_formatted} | "
                "{message}\n"
            )

        # Add custom handler with our format and filter
        logger.add(
            sys.stderr,
            format=format_record,
            level=log_level if log_level != "TRACE" else 5,
            filter=cls._get_filter_func(log_level),
            colorize=True
        )

        # Intercept stdlib logging (for watchdog, requests, urllib3)
        # This redirects all stdlib logging to loguru
        class InterceptHandler(logging.Handler):
            def emit(self, record):
                # Get corresponding Loguru level if it exists
                try:
                    level = logger.level(record.levelname).name
                except ValueError:
                    level = record.levelno

                # Find caller from where the logged message originated
                frame, depth = logging.currentframe(), 2
                while frame and frame.f_code.co_filename == logging.__file__:
                    frame = frame.f_back
                    depth += 1

                # Pass the stdlib logger name in extra so the filter can use it
                logger.bind(stdlib_name=record.name).opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

        # Set up interception for stdlib logging
        logging.basicConfig(handlers=[InterceptHandler()], level=0)
        # Also set handlers on root logger to be safe
        logging.root.handlers = [InterceptHandler()]
        logging.root.setLevel(0)

        cls._initialized = True

        return logger
