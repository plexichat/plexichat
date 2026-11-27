"""Regression: an operator's explicit CORS allowlist must not be widened.

``ConfigLoader._deep_merge`` concatenates string lists, so every consumer of
``config.get("api")`` saw the code defaults (loopback + vendor origins) added
to whatever an operator configured.  With ``cors_allow_credentials: true``
that silently re-permitted credentialed cross-origin requests from origins the
operator had deliberately removed.  The loader now honours the explicit value
from the raw config file for this security allowlist.
"""

import pytest
import yaml


BROAD_DEFAULTS = [
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://plexichat.com",
    "https://app.plexichat.com",
    "https://api.plexichat.com",
    "http://localhost:8443",
]


@pytest.fixture
def config_sandbox(monkeypatch):
    """Let a test call config.setup() and restore the process-wide instance."""
    import utils.config as cfg
    from src.api.config import clear_api_config_cache

    monkeypatch.setattr(cfg, "_config_instance", cfg._config_instance)
    monkeypatch.setattr(cfg, "_setup_called", cfg._setup_called)
    # ``APIConfig`` is a process-wide, version-keyed snapshot: without dropping
    # it the first test to build one pins its values for every later test in
    # this worker, which made the end-to-end assertions order-dependent.
    clear_api_config_cache()
    yield cfg
    clear_api_config_cache()


def _setup_operator_config(cfg, tmp_path, api_block):
    path = tmp_path / "cors.yaml"
    path.write_text(yaml.safe_dump({"api": api_block}), encoding="utf-8")
    cfg.setup(str(path), default_config={"api": {"cors_origins": BROAD_DEFAULTS}})
    return path


def test_explicit_cors_allowlist_replaces_widened_defaults(config_sandbox, tmp_path):
    """A narrowed allowlist stays narrowed for every config.get() consumer."""
    cfg = config_sandbox
    _setup_operator_config(
        cfg, tmp_path, {"cors_origins": ["https://chat.example.invalid"]}
    )

    effective = cfg.get("api", {})["cors_origins"]
    # Before the fix the deep merge produced all 8 defaults + the one entry.
    assert effective == ["https://chat.example.invalid"]
    assert not (set(effective) & set(BROAD_DEFAULTS))


def test_explicit_empty_cors_allowlist_is_honored(config_sandbox, tmp_path):
    """An explicit empty allowlist reaches consumers as empty (fail closed)."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"cors_origins": []})

    assert cfg.get("api", {})["cors_origins"] == []


def test_absent_cors_allowlist_preserves_merged_defaults(config_sandbox, tmp_path):
    """With no explicit value the merged defaults still apply (no regression)."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"debug": False})

    assert cfg.get("api", {})["cors_origins"] == BROAD_DEFAULTS


def test_api_config_sees_narrowed_allowlist(config_sandbox, tmp_path):
    """End-to-end: the APIConfig consumed by CORS middleware is narrowed too."""
    cfg = config_sandbox
    _setup_operator_config(
        cfg, tmp_path, {"cors_origins": ["https://chat.example.invalid"]}
    )

    from src.api.config import get_api_config

    api_cfg = get_api_config()
    assert api_cfg.cors_origins == ["https://chat.example.invalid"]
