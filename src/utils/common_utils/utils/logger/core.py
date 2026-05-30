import os
import logging
import zipfile
import glob
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional, List, Union


class SanitizingFilter(logging.Filter):
    """Filter that sanitizes log records to prevent PII leakage."""

    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            from .sanitizer import sanitize_log_message

            record.msg = sanitize_log_message(record.msg)
        return True


class DualFileHandler(logging.Handler):
    """
    Custom handler that writes to both a dated log file and latest.log simultaneously.
    This ensures both files always have the same content in real-time.
    """

    def __init__(
        self,
        dated_log_path: str,
        latest_log_path: str,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        rotate: bool = True,
    ):
        super().__init__()
        self.dated_log_path = dated_log_path
        self.latest_log_path = latest_log_path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.rotate = rotate

        # Create the dated file handler
        self.dated_handler: Union[RotatingFileHandler, logging.FileHandler]
        if rotate:
            self.dated_handler = RotatingFileHandler(
                dated_log_path, maxBytes=max_bytes, backupCount=backup_count
            )
        else:
            self.dated_handler = logging.FileHandler(dated_log_path)

        # Create latest.log handler - truncate file first, then open in append mode
        # This ensures the file is cleared on startup but subsequent writes work correctly
        open(latest_log_path, "w").close()
        self.latest_handler: logging.FileHandler = logging.FileHandler(
            latest_log_path, mode="a"
        )

    def setFormatter(self, fmt: Optional[logging.Formatter]) -> None:
        super().setFormatter(fmt)
        if fmt is not None:
            self.dated_handler.setFormatter(fmt)
            self.latest_handler.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        """Write to both handlers simultaneously."""
        try:
            self.dated_handler.emit(record)
            self.latest_handler.emit(record)
            # Flush both handlers to ensure immediate write to disk
            self.dated_handler.flush()
            self.latest_handler.flush()
            # Also flush the underlying streams
            if hasattr(self.dated_handler, "stream") and self.dated_handler.stream:
                self.dated_handler.stream.flush()
            if hasattr(self.latest_handler, "stream") and self.latest_handler.stream:
                self.latest_handler.stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if hasattr(self, "dated_handler") and self.dated_handler is not None:
            self.dated_handler.close()
        if hasattr(self, "latest_handler") and self.latest_handler is not None:
            self.latest_handler.close()
        super().close()


class Logger:
    """
    A configurable logging utility with:
    - Live updates to both dated log and latest.log
    - Automatic zipping of old logs on startup
    - Configurable rotation
    - Cleanup of old latest.log files
    """

    def __init__(
        self,
        log_dir: str,
        log_name_format: str = "app_%Y-%m-%d.log",
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        level: int = logging.INFO,
        zip_logs: bool = True,
        rotate: bool = True,
        max_zip_age_days: Optional[int] = 30,
    ):
        """
        Initialize the Logger.

        Args:
            log_dir: Directory to store logs.
            log_name_format: Format for log filenames (strftime format).
            max_bytes: Max size of a log file before rotation (if rotate is True).
            backup_count: Number of backup files to keep.
            level: Logging level (e.g., logging.INFO, logging.DEBUG).
            zip_logs: Whether to zip old logs on startup.
            rotate: Whether to use RotatingFileHandler for size-based rotation.
            max_zip_age_days: Delete zipped logs older than this many days (None to keep forever).
        """
        self.log_dir = log_dir
        self.log_name_format = log_name_format
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.level = level
        self.zip_logs = zip_logs
        self.rotate = rotate
        self.max_zip_age_days = max_zip_age_days
        self.current_log_file = self._get_log_filename()

        self._setup_log_dir()
        self._cleanup_old_latest()

        # Setup logger before zipping so we can use it to log any issues
        self.logger: logging.Logger = self._setup_logger()

        if self.zip_logs:
            self._zip_old_logs()
            self._cleanup_old_zips()

    def _setup_log_dir(self) -> None:
        """Creates the log directory if it doesn't exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def _cleanup_old_latest(self) -> None:
        """Remove old latest.log before creating a new one."""
        latest_log_path = os.path.join(self.log_dir, "latest.log")
        if os.path.exists(latest_log_path):
            try:
                os.remove(latest_log_path)
            except OSError:
                pass  # File might be locked, will be overwritten anyway

    def _get_log_filename(self) -> str:
        """Generates a log filename based on the format."""
        filename = datetime.now().strftime(self.log_name_format)
        filepath = os.path.join(self.log_dir, filename)

        # Handle collision if file already exists
        if os.path.exists(filepath):
            base, ext = os.path.splitext(filepath)
            i = 1
            while os.path.exists(f"{base}_{i}{ext}"):
                i += 1
            filepath = f"{base}_{i}{ext}"

        return filepath

    def _zip_old_logs(self) -> None:
        """Zips .log files in the directory (excluding latest.log and current log)."""
        current_log_name = os.path.basename(self.current_log_file)
        for filename in os.listdir(self.log_dir):
            filepath = os.path.join(self.log_dir, filename)

            # Skip latest.log, current log, non-log files, and directories
            if filename == "latest.log" or filename == current_log_name:
                continue
            if not filename.endswith(".log") or not os.path.isfile(filepath):
                continue

            # Zip the old log file
            zip_name = filepath + ".zip"
            try:
                with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(filepath, arcname=filename)
                os.remove(filepath)
            except (OSError, zipfile.BadZipFile) as e:
                self.logger.warning(f"Could not zip {filepath}: {e}")

    def _cleanup_old_zips(self) -> None:
        """Delete zipped logs older than max_zip_age_days and keep only backup_count items."""
        zip_files = []
        for filename in os.listdir(self.log_dir):
            if filename.endswith(".zip"):
                filepath = os.path.join(self.log_dir, filename)
                zip_files.append((filepath, os.path.getmtime(filepath)))

        # Sort by mtime descending (newest first)
        zip_files.sort(key=lambda x: x[1], reverse=True)

        # 1. Enforce backup_count (keep only first 'backup_count' files)
        if self.backup_count > 0 and len(zip_files) > self.backup_count:
            for filepath, _ in zip_files[self.backup_count :]:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            # Update list for age check
            zip_files = zip_files[: self.backup_count]

        # 2. Enforce max_zip_age_days
        if self.max_zip_age_days is not None:
            cutoff_time = datetime.now().timestamp() - (
                self.max_zip_age_days * 24 * 60 * 60
            )
            for filepath, mtime in zip_files:
                if mtime < cutoff_time:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass

    def _setup_logger(self) -> logging.Logger:
        """Sets up the python logger with dual file output."""
        logger = logging.getLogger("AppLogger")
        logger.setLevel(self.level)

        # Add sanitizing filter
        logger.addFilter(SanitizingFilter())

        # Close and clear existing handlers to avoid ResourceWarnings
        for handler in logger.handlers[:]:
            if handler is not None:
                handler.close()
            logger.removeHandler(handler)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        # Use the already generated dated log filename
        latest_log_path = os.path.join(self.log_dir, "latest.log")

        # Dual file handler - writes to both dated log and latest.log
        dual_handler = DualFileHandler(
            dated_log_path=self.current_log_file,
            latest_log_path=latest_log_path,
            max_bytes=self.max_bytes,
            backup_count=self.backup_count,
            rotate=self.rotate,
        )
        dual_handler.setFormatter(formatter)
        logger.addHandler(dual_handler)

        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        return logger

    def get_logger(self) -> logging.Logger:
        """Returns the configured logger instance."""
        return self.logger

    def get_logs(self) -> List[str]:
        """Returns a list of all log files (both .log and .zip)."""
        return glob.glob(os.path.join(self.log_dir, "*.log")) + glob.glob(
            os.path.join(self.log_dir, "*.zip")
        )

    def get_current_log_path(self) -> str:
        """Returns the path to the current dated log file."""
        return self.current_log_file

    def get_latest_log_path(self) -> str:
        """Returns the path to latest.log."""
        return os.path.join(self.log_dir, "latest.log")
