"""Tests for ``src.server.config_loader`` env-var coercion and overrides."""

import pytest

import utils.config as config
from src.server.config_loader import (
    _apply_bool,
    _apply_env_overrides,
    _apply_float,
    _apply_int,
    _as_bool,
)


@pytest.fixture
def fresh_config(tmp_path):
    """Point the global config at a fresh temp file with minimal defaults."""
    config.setup(
        config_path=str(tmp_path / "config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": str(tmp_path / "data.db"),
                "postgres": {},
                "connection_pool": {},
            },
            "monitoring": {},
            "media": {},
            "valkey": {},
        },
    )
    yield


class TestCoercionHelpers:
    def test_as_bool_true_values(self):
        for raw in ("true", "True", "TRUE", "1", "yes", "YES"):
            assert _as_bool(raw) is True

    def test_as_bool_false_values(self):
        for raw in ("false", "False", "0", "no", "off", "on", "", "garbage"):
            assert _as_bool(raw) is False

    def test_apply_int_sets_value(self):
        target = {}
        assert _apply_int(target, "port", "5432", "TEST_PORT") is True
        assert target["port"] == 5432

    def test_apply_int_skips_falsy(self):
        target = {"port": 5432}
        assert _apply_int(target, "port", "", "TEST_PORT") is False
        assert _apply_int(target, "port", None, "TEST_PORT") is False
        assert target["port"] == 5432

    def test_apply_int_rejects_unparseable(self):
        target = {}
        assert _apply_int(target, "port", "abc", "TEST_PORT") is False
        assert "port" not in target

    def test_apply_int_rejects_out_of_range(self):
        target = {}
        assert (
            _apply_int(target, "port", "70000", "TEST_PORT", max_value=65535) is False
        )
        assert _apply_int(target, "port", "0", "TEST_PORT", min_value=1) is False
        assert "port" not in target

    def test_apply_float_sets_and_rejects(self):
        target = {}
        assert _apply_float(target, "rate", "2.5", "TEST_RATE") is True
        assert target["rate"] == 2.5
        assert _apply_float(target, "rate", "abc", "TEST_RATE") is False
        assert target["rate"] == 2.5

    def test_apply_bool_sets(self):
        target = {}
        assert _apply_bool(target, "enabled", "true") is True
        assert target["enabled"] is True
        assert _apply_bool(target, "enabled", "false") is True
        assert target["enabled"] is False


class TestApplyEnvOverrides:
    def test_postgres_env_overrides(self, fresh_config, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "db.internal")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_USER", "pguser")
        monkeypatch.setenv("POSTGRES_SSLMODE", "require")

        _apply_env_overrides()

        pg = config.get("database")["postgres"]
        assert pg["host"] == "db.internal"
        assert pg["port"] == 5433
        assert pg["user"] == "pguser"
        assert pg["sslmode"] == "require"

    def test_database_url_takes_precedence(self, fresh_config, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://u:p@dbhost:5433/mydb?sslmode=require"
        )
        # These are ignored when DATABASE_URL is set.
        monkeypatch.setenv("POSTGRES_HOST", "should-not-win")

        _apply_env_overrides()

        db = config.get("database")
        assert db["type"] == "postgres"
        assert db["postgres"]["host"] == "dbhost"
        assert db["postgres"]["port"] == 5433
        assert db["postgres"]["dbname"] == "mydb"
        assert db["postgres"]["sslmode"] == "require"

    def test_pool_and_monitoring_overrides(self, fresh_config, monkeypatch):
        monkeypatch.setenv("DB_POOL_MAX_CONNECTIONS", "42")
        monkeypatch.setenv("DB_POOL_ENABLE_VALIDATION", "true")
        monkeypatch.setenv("MONITORING_ENABLED", "false")
        monkeypatch.setenv("MONITORING_ALERT_CPU_THRESHOLD", "92.5")
        monkeypatch.setenv("SCANNER_ENABLED", "1")

        _apply_env_overrides()

        pool = config.get("database")["connection_pool"]
        assert pool["max_connections"] == 42
        assert pool["enable_validation"] is True
        assert config.get("monitoring")["enabled"] is False
        assert config.get("monitoring")["alert_thresholds"]["cpu_percent"] == 92.5
        assert config.get("media")["scanner_enabled"] is True

    def test_invalid_values_keep_defaults(self, fresh_config, monkeypatch):
        monkeypatch.setenv("POSTGRES_PORT", "70000")  # out of range
        monkeypatch.setenv("DB_POOL_MIN_CONNECTIONS", "-5")  # below min
        monkeypatch.setenv("MONITORING_ALERT_CPU_THRESHOLD", "not-a-float")

        _apply_env_overrides()

        assert "port" not in config.get("database")["postgres"]
        assert "min_connections" not in config.get("database")["connection_pool"]
        assert "cpu_percent" not in config.get("monitoring")["alert_thresholds"]

    def test_valkey_enabled_applies_empty_string_as_false(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("VALKEY_ENABLED", "")
        monkeypatch.setenv("VALKEY_PORT", "6380")

        _apply_env_overrides()

        valkey = config.get("valkey")
        assert valkey["enabled"] is False
        assert valkey["port"] == 6380
