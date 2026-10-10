"""Regression tests for branding-sensitive configuration defaults."""

from pathlib import Path


def test_default_config_version_matches_the_running_server():
    """A generated config must advertise the version the server actually runs.

    Regression: build 132 bumped ``VERSION`` in src/server/lifecycle.py in a
    commit that did not touch this module's own version literal, so the two
    drifted and every generated config claimed a.1.0-131 while the running
    process (and /api/v1/version) reported a.1.0-132.
    """
    from src.config_defaults import get_default_config
    from src.server.lifecycle import VERSION

    config = get_default_config()

    assert config["application"]["version"] == VERSION
    assert config["versioning"]["min_supported_version"] == VERSION
    # Callers that pin a version (deployment tooling, tests) still win.
    assert get_default_config(version="a.1.0-1")["application"]["version"] == "a.1.0-1"


def test_default_config_uses_expected_branding_and_paths():
    """Default config should keep case-sensitive values stable."""
    from src.config_defaults import get_default_config

    config = get_default_config()

    assert config["application"]["name"] == "Plexichat"
    assert config["database"]["path"] == str(
        Path.home() / ".plexichat" / "data" / "plexichat.db"
    )
    # dbname uses interpolation: ${POSTGRES_DBNAME:-plexichat}
    assert config["database"]["postgres"]["dbname"] == "${POSTGRES_DBNAME:-plexichat}"
    assert config["valkey"]["key_prefix"] == "plexichat:"
    assert config["authentication"]["totp"]["issuer"] == "Plexichat"
    assert config["api"]["title"] == "Plexichat API"
    assert config["docs"]["title"] == "Plexichat API Documentation"
    assert config["email"]["from_email"] == "noreply@plexichat.internal"
    assert config["api"]["cors_origins"] == [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://plexichat.com",
        "https://app.plexichat.com",
        "https://api.plexichat.com",
        "http://localhost:8443",
    ]


def test_main_source_uses_uppercase_env_vars_and_lowercase_home_dir():
    """src/cli/main.py should keep env var names uppercase and paths lowercase."""
    main_source = Path("src/cli/main.py").read_text(encoding="utf-8")

    assert "PLEXICHAT_CONFIG" in main_source
    assert "PLEXICHAT_SYSTEM_KEY" in main_source
    assert "PLEXICHAT_MESSAGE_KEY" in main_source
    assert ".plexichat" in main_source

    assert "Plexichat_CONFIG" not in main_source
    assert "Plexichat_SYSTEM_KEY" not in main_source
    assert "Plexichat_MESSAGE_KEY" not in main_source
    assert ".Plexichat" not in main_source
