"""Shared configuration for the bundled ``common_utils`` test suite.

The licensing / validator / version modules log through ``utils.logger``,
which requires ``logger.setup()`` before any log call (it raises
``RuntimeError`` otherwise).

The package-level ``conftest.py`` (one level up) intentionally resets the
logger singleton after *every* test via its autouse ``reset_singletons``
fixture, so a one-off setup at import time is not enough. This autouse
fixture re-configures a quiet logger (writing to a temp directory) before
each test, keeping the suite self-contained and order-independent.
"""

import tempfile

import pytest

import utils.logger as logger

_LOG_DIR = tempfile.mkdtemp(prefix="plexichat-common-utils-test-logs-")


@pytest.fixture(autouse=True)
def _configure_logger():
    logger.setup(
        log_dir=_LOG_DIR,
        level="WARNING",
        zip_logs=False,
        rotate=False,
    )
    yield
