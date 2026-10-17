import os
import sys
import shutil
import pytest

project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

TEMP_DIR = os.path.join(project_root, "temp")
TEMP_CONFIG_DIR = os.path.join(TEMP_DIR, "config")
TEMP_LOG_DIR = os.path.join(TEMP_DIR, "logs")


@pytest.fixture(scope="session")
def project_root_dir():
    return project_root


@pytest.fixture(scope="session")
def temp_base_dir():
    return TEMP_DIR


@pytest.fixture(scope="function")
def clean_temp_dir():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR)
    yield TEMP_DIR
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)


@pytest.fixture(scope="function")
def clean_config_dir():
    if os.path.exists(TEMP_CONFIG_DIR):
        shutil.rmtree(TEMP_CONFIG_DIR)
    os.makedirs(TEMP_CONFIG_DIR)
    yield TEMP_CONFIG_DIR
    if os.path.exists(TEMP_CONFIG_DIR):
        shutil.rmtree(TEMP_CONFIG_DIR)


@pytest.fixture(scope="function")
def clean_log_dir():
    if os.path.exists(TEMP_LOG_DIR):
        shutil.rmtree(TEMP_LOG_DIR)
    os.makedirs(TEMP_LOG_DIR)
    yield TEMP_LOG_DIR
    if os.path.exists(TEMP_LOG_DIR):
        shutil.rmtree(TEMP_LOG_DIR)


@pytest.fixture(scope="function")
def isolated_temp_dir(tmp_path):
    yield tmp_path


@pytest.fixture(scope="function", autouse=True)
def reset_singletons():
    """Isolate the module-level config/logger singletons between tests.

    Snapshots the pre-test state and restores it afterwards, so tests that
    mutate the singletons (via ``setup()``) cannot leak into each other —
    while preserving any configuration performed by an outer test suite
    (e.g. ``src/tests/conftest.py``) that shares this worker process under
    xdist ``--dist loadfile``. Previously this fixture unconditionally
    clobbered the singletons, which broke every later ``src/tests`` file in
    the same worker with "Config not configured".

    It also restores the process-global ``"AppLogger"`` level. Constructing
    ``Logger(...)`` (as ``tests/logger/test_logger.py`` does, with the default
    ``INFO`` level) runs ``_setup_logger`` and calls
    ``logging.getLogger("AppLogger").setLevel(...)``; without this restore
    that level leaks into every later ``src/tests`` file in the worker.

    Handlers/filters are detached before the test so a ``setup()`` inside
    the test cannot close the outer suite's handlers; the scoped handlers
    are closed afterwards and the originals re-attached.
    """
    import logging

    import utils.logger as logger
    import utils.config as config

    app_logger = logging.getLogger("AppLogger")
    snapshot = (
        getattr(config, "_setup_called", False),
        getattr(config, "_config_instance", None),
        getattr(logger, "_setup_called", False),
        getattr(logger, "_logger_instance", None),
        app_logger.level,
        app_logger.propagate,
        app_logger.handlers[:],
        app_logger.filters[:],
    )
    for handler in snapshot[6]:
        app_logger.removeHandler(handler)
    for filt in snapshot[7]:
        app_logger.removeFilter(filt)
    yield
    for handler in app_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        app_logger.removeHandler(handler)
    for filt in app_logger.filters[:]:
        app_logger.removeFilter(filt)
    for handler in snapshot[6]:
        app_logger.addHandler(handler)
    for filt in snapshot[7]:
        app_logger.addFilter(filt)
    config._setup_called = snapshot[0]
    config._config_instance = snapshot[1]
    logger._setup_called = snapshot[2]
    logger._logger_instance = snapshot[3]
    app_logger.setLevel(snapshot[4])
    app_logger.propagate = snapshot[5]


@pytest.fixture(scope="function")
def sample_config_file(clean_config_dir):
    config_path = os.path.join(clean_config_dir, "test_config.yaml")
    config_content = """
key1: value1
key2: 42
nested:
  inner_key: inner_value
list_key:
  - item1
  - item2
"""
    with open(config_path, "w") as f:
        f.write(config_content.strip())
    return config_path


@pytest.fixture(scope="function")
def sample_json_config_file(clean_config_dir):
    import json

    config_path = os.path.join(clean_config_dir, "test_config.json")
    config_content = {
        "key1": "value1",
        "key2": 42,
        "nested": {"inner_key": "inner_value"},
        "list_key": ["item1", "item2"],
    }
    with open(config_path, "w") as f:
        json.dump(config_content, f, indent=2)
    return config_path


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: Unit tests for individual components")
    config.addinivalue_line(
        "markers", "integration: Integration tests for cross-module functionality"
    )
    config.addinivalue_line("markers", "config: Tests for the ConfigLoader utility")
    config.addinivalue_line("markers", "logger: Tests for the Logger utility")
    config.addinivalue_line("markers", "validator: Tests for the Validator utility")
    config.addinivalue_line("markers", "version: Tests for the Version utility")
    config.addinivalue_line("markers", "slow: Tests that take significant time to run")
    config.addinivalue_line("markers", "io: Tests that perform file I/O operations")
    config.addinivalue_line(
        "markers", "requires_temp: Tests that require temp directory"
    )
