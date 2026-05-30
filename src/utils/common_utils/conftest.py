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
    yield

    import utils.logger as logger
    import utils.config as config

    if hasattr(logger, "_logger_instance"):
        logger._logger_instance = None
    if hasattr(logger, "_setup_called"):
        logger._setup_called = False
    if hasattr(config, "_config_instance"):
        config._config_instance = None
    if hasattr(config, "_setup_called"):
        config._setup_called = False


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
