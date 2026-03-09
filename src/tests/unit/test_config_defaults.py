"""Regression tests for branding-sensitive configuration defaults."""

from pathlib import Path


def test_default_config_uses_expected_branding_and_paths():
    """Default config should keep case-sensitive values stable."""
    from src.config_defaults import get_default_config

    config = get_default_config()

    assert config["application"]["name"] == "Plexichat"
    assert config["database"]["path"] == str(
        Path.home() / ".plexichat" / "data" / "plexichat.db"
    )
    assert config["database"]["postgres"]["dbname"] == "plexichat"
    assert config["redis"]["key_prefix"] == "plexichat:"
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
    """main.py should keep env var names and filesystem paths case-safe."""
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "PLEXICHAT_CONFIG" in main_source
    assert "PLEXICHAT_SYSTEM_KEY" in main_source
    assert "PLEXICHAT_SMTP_PASSWORD" in main_source
    assert "PLEXICHAT_MESSAGE_KEY" in main_source
    assert ".plexichat" in main_source

    assert "Plexichat_CONFIG" not in main_source
    assert "Plexichat_SYSTEM_KEY" not in main_source
    assert "Plexichat_SMTP_PASSWORD" not in main_source
    assert "Plexichat_MESSAGE_KEY" not in main_source
    assert ".Plexichat" not in main_source