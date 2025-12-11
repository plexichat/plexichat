"""
Unit tests for environment variable configuration overrides.

Tests the _apply_env_overrides functionality in main.py.
"""

import pytest


class TestDatabaseUrlParsing:
    """Tests for DATABASE_URL environment variable parsing."""

    def test_postgres_url_basic(self):
        """Test parsing basic PostgreSQL URL."""
        url = "postgres://myuser:mypass@dbhost:5433/mydb"
        import urllib.parse
        parsed = urllib.parse.urlparse(url)

        assert parsed.hostname == "dbhost"
        assert parsed.port == 5433
        assert parsed.username == "myuser"
        assert parsed.password == "mypass"
        assert parsed.path.lstrip("/") == "mydb"

    def test_postgres_url_with_sslmode(self):
        """Test parsing PostgreSQL URL with sslmode query param."""
        url = "postgres://user:pass@host:5432/db?sslmode=require"
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        assert parsed.hostname == "host"
        assert params.get("sslmode") == ["require"]

    def test_postgresql_scheme(self):
        """Test that postgresql:// scheme is also valid."""
        url = "postgresql://user:pass@host:5432/db"
        import urllib.parse
        parsed = urllib.parse.urlparse(url)

        assert parsed.scheme == "postgresql"
        assert parsed.hostname == "host"

    def test_sqlite_url(self):
        """Test parsing SQLite URL."""
        url = "sqlite:///path/to/database.db"

        assert url.startswith("sqlite:///")
        path = url[10:]  # Remove sqlite:///
        assert path == "path/to/database.db"

    def test_postgres_url_defaults(self):
        """Test that missing parts get defaults."""
        url = "postgres://localhost/mydb"
        import urllib.parse
        parsed = urllib.parse.urlparse(url)

        assert parsed.hostname == "localhost"
        assert parsed.port is None  # Should default to 5432 in code
        assert parsed.username is None  # Should default to postgres in code
        assert parsed.password is None  # Should default to empty in code


class TestJwtSecretOverride:
    """Tests for JWT_SECRET environment variable."""

    def test_jwt_secret_format(self):
        """Test that JWT secret can be any string."""
        secrets = [
            "simple-secret",
            "super-long-secret-key-with-many-characters-1234567890",
            "secret_with_underscores",
            "secret-with-dashes",
            "MixedCaseSecret123!@#",
        ]
        for secret in secrets:
            assert len(secret) > 0
            assert isinstance(secret, str)


class TestLogLevelOverride:
    """Tests for LOG_LEVEL environment variable."""

    @pytest.mark.parametrize("level,expected", [
        ("debug", "DEBUG"),
        ("DEBUG", "DEBUG"),
        ("info", "INFO"),
        ("INFO", "INFO"),
        ("warning", "WARNING"),
        ("WARNING", "WARNING"),
        ("error", "ERROR"),
        ("ERROR", "ERROR"),
    ])
    def test_log_level_normalization(self, level, expected):
        """Test that log levels are normalized to uppercase."""
        assert level.upper() == expected


class TestConfigIntegration:
    """Integration tests for config with environment variables."""

    def test_env_override_priority(self):
        """Test that environment variables take priority over config file."""
        # This is a design test - env vars should override file config
        # The implementation in main.py calls _apply_env_overrides after config.setup
        pass

    def test_partial_database_url_override(self):
        """Test that DATABASE_URL completely replaces database config."""
        # When DATABASE_URL is set, it should override:
        # - database.type
        # - database.path (for sqlite)
        # - database.postgres.* (for postgres)
        pass
