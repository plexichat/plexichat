"""
Logger utility module - Zero-friction logging for Python applications.

Usage:
    # In main.py (setup once)
    import utils.logger as logger
    logger.setup(log_dir="logs", level="DEBUG")

    # In any other file (no setup needed)
    import utils.logger as logger
    logger.info("This is a log message")
    logger.error("Something went wrong")
"""

import logging
from typing import Optional, List
from .core import Logger, DualFileHandler
from .sanitizer import sanitize_data, sanitize_log_message, mask_email, mask_string

# Global logger instance
_logger_instance: Optional[Logger] = None
_setup_called = False


def setup(
    log_dir: str,
    log_name_format: str = "app_%Y-%m-%d_%H-%M-%S.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    level: str = "INFO",
    zip_logs: bool = True,
    rotate: bool = True,
    max_zip_age_days: Optional[int] = 30,
) -> None:
    """
    Setup the logger. Should be called once in your main application file.

    Args:
        log_dir (str): Directory to store logs.
        log_name_format (str): Format for log filenames (strftime format).
        max_bytes (int): Max size of a log file before rotation.
        backup_count (int): Number of backup files to keep.
        level (str): Logging level ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
        zip_logs (bool): Whether to zip old logs on startup.
        rotate (bool): Whether to use RotatingFileHandler for size-based rotation.
        max_zip_age_days (int|None): Delete zipped logs older than this many days (None to keep forever).
    """
    global _logger_instance, _setup_called

    # Convert string level to logging constant
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    log_level = level_map.get(level.upper(), logging.INFO)

    _logger_instance = Logger(
        log_dir=log_dir,
        log_name_format=log_name_format,
        max_bytes=max_bytes,
        backup_count=backup_count,
        level=log_level,
        zip_logs=zip_logs,
        rotate=rotate,
        max_zip_age_days=max_zip_age_days,
    )
    _setup_called = True


def _ensure_setup() -> None:
    """Internal: Ensures setup was called before using logger functions."""
    if not _setup_called:
        raise RuntimeError(
            "Logger not configured. Please call logger.setup() in your main.py file first."
        )


def _get_logger() -> logging.Logger:
    """Internal: Get the actual logger instance."""
    _ensure_setup()
    assert _logger_instance is not None
    return _logger_instance.get_logger()


# Expose logging methods directly
def debug(msg: str, *args, **kwargs) -> None:
    """Log a debug message."""
    _get_logger().debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    """Log an info message."""
    _get_logger().info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    """Log a warning message."""
    _get_logger().warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    """Log an error message."""
    _get_logger().error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs) -> None:
    """Log a critical message."""
    _get_logger().critical(msg, *args, **kwargs)


def get_logs() -> List[str]:
    """Returns a list of all log files."""
    _ensure_setup()
    assert _logger_instance is not None
    return _logger_instance.get_logs()


def get_current_log_path() -> str:
    """Returns the path to the current dated log file."""
    _ensure_setup()
    assert _logger_instance is not None
    return _logger_instance.get_current_log_path()


def get_latest_log_path() -> str:
    """Returns the path to latest.log."""
    _ensure_setup()
    assert _logger_instance is not None
    return _logger_instance.get_latest_log_path()


# For backward compatibility, also expose the Logger class
__all__ = [
    "Logger",
    "DualFileHandler",
    "setup",
    "debug",
    "info",
    "warning",
    "error",
    "critical",
    "get_logs",
    "get_current_log_path",
    "get_latest_log_path",
    "sanitize_data",
    "sanitize_log_message",
    "mask_email",
    "mask_string",
]
