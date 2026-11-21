"""Regression: operator narrowing of CORS methods/headers must not be widened.

``ConfigLoader._deep_merge`` concatenates string lists, so an operator who
narrows ``api.cors_allow_methods`` or ``api.cors_allow_headers`` silently got
the broad code defaults (``DELETE``/``PATCH``, ``X-API-Access-Token``,
``X-RateLimit-Bypass``) re-added.  With credentialed CORS from a trusted but
deliberately limited origin, that re-enables cross-origin state changes and
privileged headers the operator had removed - a fail-open widening of the same
class already fixed for ``cors_origins``.  The loader now restores the explicit
value from the raw config file for these allowlists too.
"""

import pytest
import yaml


WIDE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
WIDE_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Requested-With",
    "Accept",
    "Origin",
    "X-API-Access-Token",
    "X-RateLimit-Bypass",
]


@pytest.fixture
def config_sandbox(monkeypatch):
    """Let a test call config.setup() and restore the process-wide instance."""
    import utils.config as cfg

    monkeypatch.setattr(cfg, "_config_instance", cfg._config_instance)
    monkeypatch.setattr(cfg, "_setup_called", cfg._setup_called)
    return cfg


def _setup_operator_config(cfg, tmp_path, api_block):
    path = tmp_path / "cors_methods.yaml"
    path.write_text(yaml.safe_dump({"api": api_block}), encoding="utf-8")
    cfg.setup(
        str(path),
        default_config={
            "api": {
                "cors_allow_methods": WIDE_METHODS,
                "cors_allow_headers": WIDE_HEADERS,
            }
        },
    )
    return path


def test_explicit_narrow_methods_replace_widened_defaults(config_sandbox, tmp_path):
    """A read-only method allowlist stays read-only for every config consumer."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"cors_allow_methods": ["GET"]})

    effective = cfg.get("api", {})["cors_allow_methods"]
    assert effective == ["GET"]
    assert not (set(effective) & {"POST", "PUT", "PATCH", "DELETE"})


def test_explicit_narrow_headers_replace_widened_defaults(config_sandbox, tmp_path):
    """Dropping privileged headers from the allowlist actually removes them."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"cors_allow_headers": ["Authorization"]})

    effective = cfg.get("api", {})["cors_allow_headers"]
    assert effective == ["Authorization"]
    assert "X-RateLimit-Bypass" not in effective
    assert "X-API-Access-Token" not in effective


def test_explicit_empty_allowlists_are_honored(config_sandbox, tmp_path):
    """Explicit empty allowlists reach consumers as empty (fail closed)."""
    cfg = config_sandbox
    _setup_operator_config(
        cfg,
        tmp_path,
        {"cors_allow_methods": [], "cors_allow_headers": []},
    )

    api = cfg.get("api", {})
    assert api["cors_allow_methods"] == []
    assert api["cors_allow_headers"] == []


def test_absent_allowlists_preserve_merged_defaults(config_sandbox, tmp_path):
    """With no explicit value the merged defaults still apply (no regression)."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"debug": False})

    api = cfg.get("api", {})
    assert api["cors_allow_methods"] == WIDE_METHODS
    assert api["cors_allow_headers"] == WIDE_HEADERS


def test_api_config_sees_narrowed_methods_and_headers(config_sandbox, tmp_path):
    """End-to-end: the APIConfig fed to CORSMiddleware is narrowed too."""
    cfg = config_sandbox
    _setup_operator_config(
        cfg,
        tmp_path,
        {"cors_allow_methods": ["GET"], "cors_allow_headers": ["Authorization"]},
    )

    from src.api.config import get_api_config

    api_cfg = get_api_config()
    assert api_cfg.cors_allow_methods == ["GET"]
    assert api_cfg.cors_allow_headers == ["Authorization"]
