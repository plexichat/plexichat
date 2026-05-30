import os
import sys
import shutil
import time
import logging
import zipfile
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.logger import Logger

TEMP_LOG_DIR = os.path.abspath("temp/logs")


@pytest.fixture
def clean_log_dir():
    if os.path.exists(TEMP_LOG_DIR):
        shutil.rmtree(TEMP_LOG_DIR)
    yield
    if os.path.exists(TEMP_LOG_DIR):
        shutil.rmtree(TEMP_LOG_DIR)


def cleanup_logger(logger):
    """Helper to cleanup logger handlers."""
    if hasattr(logger, "handlers"):
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


class TestBasicLoggerCreation:
    """Tests for basic logger creation and initialization."""

    def test_logger_creation(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        assert os.path.exists(TEMP_LOG_DIR)
        assert logger.level == logging.INFO

        logger.info("Test message")
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert any(f.endswith(".log") for f in files)
        assert "latest.log" in files

    def test_custom_log_level(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, level=logging.DEBUG)
        logger = logger_manager.get_logger()

        assert logger.level == logging.DEBUG
        cleanup_logger(logger)

    def test_warning_level(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, level=logging.WARNING)
        logger = logger_manager.get_logger()

        assert logger.level == logging.WARNING
        cleanup_logger(logger)

    def test_error_level(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, level=logging.ERROR)
        logger = logger_manager.get_logger()

        assert logger.level == logging.ERROR
        cleanup_logger(logger)

    def test_critical_level(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, level=logging.CRITICAL)
        logger = logger_manager.get_logger()

        assert logger.level == logging.CRITICAL
        cleanup_logger(logger)


class TestLogContent:
    """Tests for log content and message writing."""

    def test_log_content(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        msg = "Unique message content"
        logger.info(msg)
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert msg in content

    def test_multiple_log_messages(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        messages = ["Message 1", "Message 2", "Message 3"]
        for msg in messages:
            logger.info(msg)

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            for msg in messages:
                assert msg in content

    def test_different_log_levels_content(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, level=logging.DEBUG)
        logger = logger_manager.get_logger()

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert "Debug message" in content
            assert "Info message" in content
            assert "Warning message" in content
            assert "Error message" in content
            assert "Critical message" in content

    def test_log_format_includes_level(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        logger.info("Test info")
        logger.error("Test error")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert "INFO" in content
            assert "ERROR" in content

    def test_log_format_includes_timestamp(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        logger.info("Timestamped message")
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert "-" in content


class TestDualFileHandler:
    """Tests for the DualFileHandler functionality."""

    def test_dual_file_handler_writes_to_both_files(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        msg = "Test dual write"
        logger.info(msg)
        cleanup_logger(logger)

        dated_log = logger_manager.get_current_log_path()
        latest_log = logger_manager.get_latest_log_path()

        with open(dated_log, "r") as f:
            assert msg in f.read()

        with open(latest_log, "r") as f:
            assert msg in f.read()

    def test_latest_log_cleared_on_startup(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        latest_path = os.path.join(TEMP_LOG_DIR, "latest.log")

        with open(latest_path, "w") as f:
            f.write("Old content that should be removed")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()
        logger.info("New message")
        cleanup_logger(logger)

        with open(latest_path, "r") as f:
            content = f.read()
            assert "Old content" not in content
            assert "New message" in content

    def test_dual_handler_with_rotation(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, max_bytes=100, rotate=True)
        logger = logger_manager.get_logger()

        for i in range(10):
            logger.info("A" * 50)

        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        log_files = [f for f in files if ".log" in f]
        assert len(log_files) >= 2

    def test_dual_handler_without_rotation(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, rotate=False)
        logger = logger_manager.get_logger()

        for i in range(100):
            logger.info("No rotation test")

        cleanup_logger(logger)

        dated_log = logger_manager.get_current_log_path()
        assert os.path.exists(dated_log)


class TestLogZipping:
    """Tests for log file zipping functionality."""

    def test_log_zipping(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        with open(os.path.join(TEMP_LOG_DIR, "old.log"), "w") as f:
            f.write("old logs")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=True)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "old.log.zip" in files
        assert "old.log" not in files

    def test_zip_logs_disabled(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        with open(os.path.join(TEMP_LOG_DIR, "old.log"), "w") as f:
            f.write("old logs")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=False)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "old.log" in files
        assert "old.log.zip" not in files

    def test_zip_multiple_logs(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        for i in range(3):
            with open(os.path.join(TEMP_LOG_DIR, f"old{i}.log"), "w") as f:
                f.write(f"old log {i}")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=True)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        zip_files = [f for f in files if f.endswith(".zip")]
        assert len(zip_files) == 3

    def test_latest_log_not_zipped(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "w") as f:
            f.write("latest should not be zipped")
        with open(os.path.join(TEMP_LOG_DIR, "old.log"), "w") as f:
            f.write("old should be zipped")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=True)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "latest.log" in files
        assert "latest.log.zip" not in files
        assert "old.log.zip" in files

    def test_zip_content_preserved(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)
        original_content = "This is the original log content"
        with open(os.path.join(TEMP_LOG_DIR, "old.log"), "w") as f:
            f.write(original_content)

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=True)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        zip_path = os.path.join(TEMP_LOG_DIR, "old.log.zip")
        with zipfile.ZipFile(zip_path, "r") as zipf:
            with zipf.open("old.log") as f:
                content = f.read().decode()
                assert content == original_content


class TestOldZipCleanup:
    """Tests for old zip file cleanup functionality."""

    def test_cleanup_old_zips(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        old_zip = os.path.join(TEMP_LOG_DIR, "very_old.log.zip")
        with zipfile.ZipFile(old_zip, "w") as zipf:
            zipf.writestr("log.txt", "old content")

        old_time = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(old_zip, (old_time, old_time))

        logger_manager = Logger(
            log_dir=TEMP_LOG_DIR, zip_logs=True, max_zip_age_days=30
        )
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "very_old.log.zip" not in files

    def test_keep_recent_zips(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        recent_zip = os.path.join(TEMP_LOG_DIR, "recent.log.zip")
        with zipfile.ZipFile(recent_zip, "w") as zipf:
            zipf.writestr("log.txt", "recent content")

        logger_manager = Logger(
            log_dir=TEMP_LOG_DIR, zip_logs=True, max_zip_age_days=30
        )
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "recent.log.zip" in files

    def test_no_cleanup_when_max_age_none(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        old_zip = os.path.join(TEMP_LOG_DIR, "ancient.log.zip")
        with zipfile.ZipFile(old_zip, "w") as zipf:
            zipf.writestr("log.txt", "ancient content")

        old_time = (datetime.now() - timedelta(days=365)).timestamp()
        os.utime(old_zip, (old_time, old_time))

        logger_manager = Logger(
            log_dir=TEMP_LOG_DIR, zip_logs=True, max_zip_age_days=None
        )
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "ancient.log.zip" in files


class TestCustomLogFormat:
    """Tests for custom log name formatting."""

    def test_custom_format(self, clean_log_dir):
        fmt = "test_%Y.log"
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, log_name_format=fmt)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        current_year = time.strftime("%Y")
        expected = f"test_{current_year}.log"
        assert expected in files

    def test_date_format(self, clean_log_dir):
        fmt = "app_%Y-%m-%d.log"
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, log_name_format=fmt)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        today = datetime.now().strftime("%Y-%m-%d")
        expected = f"app_{today}.log"
        assert expected in files

    def test_collision_handling(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        fmt = "static.log"
        existing_file = os.path.join(TEMP_LOG_DIR, "static.log")
        with open(existing_file, "w") as f:
            f.write("existing")

        logger_manager = Logger(
            log_dir=TEMP_LOG_DIR, log_name_format=fmt, zip_logs=False
        )
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        assert "static.log" in files
        assert "static_1.log" in files


class TestRotation:
    """Tests for log rotation functionality."""

    def test_rotation(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, max_bytes=100, rotate=True)
        logger = logger_manager.get_logger()

        for _ in range(10):
            logger.info("A" * 50)

        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        log_files = [f for f in files if ".log" in f and "latest" not in f]
        assert len(log_files) >= 1

    def test_backup_count(self, clean_log_dir):
        logger_manager = Logger(
            log_dir=TEMP_LOG_DIR, max_bytes=100, backup_count=3, rotate=True
        )
        logger = logger_manager.get_logger()

        for _ in range(50):
            logger.info("X" * 50)

        cleanup_logger(logger)

        files = os.listdir(TEMP_LOG_DIR)
        log_files = [f for f in files if ".log" in f]
        assert len(log_files) <= 6

    def test_no_rotation_large_file(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR, rotate=False)
        logger = logger_manager.get_logger()

        for _ in range(100):
            logger.info("Large message without rotation " * 10)

        cleanup_logger(logger)

        dated_log = logger_manager.get_current_log_path()
        assert os.path.exists(dated_log)
        assert os.path.getsize(dated_log) > 1000


class TestGetMethods:
    """Tests for getter methods."""

    def test_get_logger(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        assert isinstance(logger, logging.Logger)
        assert logger.name == "AppLogger"
        cleanup_logger(logger)

    def test_get_logs(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        with open(os.path.join(TEMP_LOG_DIR, "old1.log"), "w") as f:
            f.write("log1")
        with open(os.path.join(TEMP_LOG_DIR, "old2.log"), "w") as f:
            f.write("log2")

        logger_manager = Logger(log_dir=TEMP_LOG_DIR, zip_logs=True)
        logger = logger_manager.get_logger()
        cleanup_logger(logger)

        logs = logger_manager.get_logs()
        zip_files = [f for f in logs if f.endswith(".zip")]
        log_files = [f for f in logs if f.endswith(".log")]

        assert len(zip_files) == 2
        assert len(log_files) >= 1

    def test_get_current_log_path(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        path = logger_manager.get_current_log_path()
        assert os.path.exists(path)
        assert path.endswith(".log")
        assert "latest" not in path
        cleanup_logger(logger)

    def test_get_latest_log_path(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        path = logger_manager.get_latest_log_path()
        assert path == os.path.join(TEMP_LOG_DIR, "latest.log")
        cleanup_logger(logger)


class TestSecurityLogInjection:
    """Security tests for log injection attacks."""

    def test_newline_injection_attempt(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        malicious_msg = "User login\nFAKE ERROR: System compromised"
        logger.info(malicious_msg)
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert malicious_msg in content

    def test_carriage_return_injection(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        malicious_msg = "Normal log\rERROR: Fake error message"
        logger.info(malicious_msg)
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert "Normal log" in content

    def test_format_string_injection(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        malicious_msg = "User: %s%s%s%s"
        logger.info(malicious_msg)
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert malicious_msg in content

    def test_xss_in_log_messages(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        xss_attempts = [
            "<script>alert('xss')</script>",
            "javascript:alert(1)",
            "<img src=x onerror=alert(1)>",
            "<iframe src='evil.com'></iframe>",
        ]

        for attempt in xss_attempts:
            logger.info(f"User input: {attempt}")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            for attempt in xss_attempts:
                assert attempt in content

    def test_sql_injection_in_logs(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        sql_attempts = [
            "'; DROP TABLE users; --",
            "1' OR '1'='1",
            "admin'--",
            "UNION SELECT * FROM passwords",
        ]

        for attempt in sql_attempts:
            logger.warning(f"SQL injection attempt detected: {attempt}")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            for attempt in sql_attempts:
                assert attempt in content

    def test_path_traversal_in_logs(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        path_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "/etc/shadow",
            "C:\\Windows\\System32\\config\\SAM",
        ]

        for attempt in path_attempts:
            logger.error(f"Path traversal attempt: {attempt}")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            for attempt in path_attempts:
                assert attempt in content

    def test_unicode_and_special_chars(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        special_msgs = [
            "Unicode: 你好世界",
            "Emoji: 🔒🔐🛡️",
            "Special: !@#$%^&*()",
            "Quotes: \"'`",
            "Null byte: \x00",
        ]

        for msg in special_msgs:
            logger.info(msg)

        cleanup_logger(logger)

        assert os.path.exists(os.path.join(TEMP_LOG_DIR, "latest.log"))

    def test_very_long_log_message(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        long_msg = "A" * 100000
        logger.info(long_msg)
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert long_msg in content

    def test_rapid_logging(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        for i in range(1000):
            logger.info(f"Rapid log message {i}")

        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            lines = f.readlines()
            assert len(lines) >= 1000


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_log_dir_already_exists(self, clean_log_dir):
        os.makedirs(TEMP_LOG_DIR)

        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        assert os.path.exists(TEMP_LOG_DIR)
        cleanup_logger(logger)

    def test_concurrent_logger_instances(self, clean_log_dir):
        logger_manager1 = Logger(log_dir=TEMP_LOG_DIR)
        logger1 = logger_manager1.get_logger()

        logger_manager2 = Logger(log_dir=TEMP_LOG_DIR)
        logger2 = logger_manager2.get_logger()

        logger1.info("From logger 1")
        logger2.info("From logger 2")

        cleanup_logger(logger1)
        cleanup_logger(logger2)

    def test_empty_log_message(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        logger.info("")
        cleanup_logger(logger)

        with open(os.path.join(TEMP_LOG_DIR, "latest.log"), "r") as f:
            content = f.read()
            assert "INFO" in content

    def test_handler_cleanup(self, clean_log_dir):
        logger_manager = Logger(log_dir=TEMP_LOG_DIR)
        logger = logger_manager.get_logger()

        initial_handler_count = len(logger.handlers)
        assert initial_handler_count > 0

        cleanup_logger(logger)
        assert len(logger.handlers) == 0
