"""Configuration management using environment variables."""
import os
import sys
import logging
from pathlib import Path
from typing import Optional
from loguru import logger
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
    def setup(cls, log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()):
        """Configure loguru logging and return logger instance."""

        if cls._initialized:
            return logger

        if log_level is None:
            log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        else:
            log_level = log_level.upper()

        # Remove default handler
        logger.remove()

        # Custom format function for colored module names
        def format_record(record):
            module = record.get("extra", {}).get("stdlib_name", record["name"])
            level_name = record["level"].name

            # Color code based on module
            if module.startswith("watchdog"):
                module_colored = f"<light-blue>{module}</light-blue>"  # Distinct from level colors
            elif module.startswith(("requests", "urllib3", "src.romm_api_func", "romm_api_func")):
                module_colored = f"<magenta>{module}</magenta>"
            else:
                # App code - use default terminal color for all levels
                module_colored = module

            # Level colors (works on both light and dark terminals)
            # Using darker variants (standard yellow/red) instead of light- variants
            level_colors = {
                "TRACE": "dim",
                "DEBUG": "blue",
                "INFO": "",  # Default text color
                "SUCCESS": "green",
                "WARNING": "yellow",  # [33m - darker than light-yellow, better on light terminals
                "ERROR": "red",  # [31m - darker than light-red, better on light terminals
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

            # Build format string - message uses default color (adapts to terminal)
            return (
                "{time:YYYY-MM-DD HH:mm:ss} | "
                f"{module_colored} | "
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


# Initialize logging on module import
LoggingConfig.setup()

# Export TRACE constant for convenience (loguru has TRACE=5 built-in)
TRACE = 5