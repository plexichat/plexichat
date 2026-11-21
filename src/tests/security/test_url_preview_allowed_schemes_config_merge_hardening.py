"""Regression: an operator's explicit URL-preview scheme allowlist must not be widened.

``ConfigLoader._deep_merge`` concatenates string lists, so every consumer of
``config.get("embeds")["url_preview"]`` saw the code defaults (``http`` +
``https``) added to whatever an operator configured.  An operator who narrows
``allowed_schemes`` to ``["https"]`` to block cleartext link previews (an SSRF /
TLS-hardening measure) silently got ``http`` back.  The loader now honours the
explicit value from the raw config file for this security allowlist, and the
link-preview SSRF scheme gate consumes the narrowed value.
"""

import pytest
import yaml


WIDE_DEFAULTS = ["http", "https"]


@pytest.fixture
def config_sandbox(monkeypatch):
    """Let a test call config.setup() and restore the process-wide instance."""
    import utils.config as cfg

    monkeypatch.setattr(cfg, "_config_instance", cfg._config_instance)
    monkeypatch.setattr(cfg, "_setup_called", cfg._setup_called)
    return cfg


def _setup_operator_config(cfg, tmp_path, preview_block):
    path = tmp_path / "embeds.yaml"
    path.write_text(
        yaml.safe_dump({"embeds": {"url_preview": preview_block}}), encoding="utf-8"
    )
    cfg.setup(
        str(path),
        default_config={"embeds": {"url_preview": {"allowed_schemes": WIDE_DEFAULTS}}},
    )
    return path


def test_explicit_allowed_schemes_replace_widened_defaults(config_sandbox, tmp_path):
    """A narrowed scheme allowlist stays narrowed for every config consumer."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"allowed_schemes": ["https"]})

    effective = cfg.get("embeds", {})["url_preview"]["allowed_schemes"]
    assert effective == ["https"]
    assert "http" not in effective


def test_explicit_empty_allowed_schemes_is_honored(config_sandbox, tmp_path):
    """An explicit empty allowlist reaches consumers as empty (fail closed)."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"allowed_schemes": []})

    assert cfg.get("embeds", {})["url_preview"]["allowed_schemes"] == []


def test_absent_allowed_schemes_preserves_merged_defaults(config_sandbox, tmp_path):
    """With no explicit value the merged defaults still apply (no regression)."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"proxy_images": False})

    assert cfg.get("embeds", {})["url_preview"]["allowed_schemes"] == WIDE_DEFAULTS


def test_link_preview_ssrf_gate_consumes_narrowed_schemes(config_sandbox, tmp_path):
    """End-to-end: the link-preview scheme gate blocks http after narrowing."""
    cfg = config_sandbox
    _setup_operator_config(cfg, tmp_path, {"allowed_schemes": ["https"]})

    from src.core.embeds.link_preview import LinkPreviewService

    service = LinkPreviewService(db=None, media_proxy=None)
    assert service._config["allowed_schemes"] == ["https"]

    # Isolate the scheme gate from DNS/network: an allowed scheme must reach the
    # URLValidator, a blocked one must be rejected before it ever gets there.
    sentinel = ("ok.example", "93.184.216.34")
    service._url_validator.validate_url_for_request = lambda url: sentinel

    assert service._validate_url("https://ok.example/x")[0] == "ok.example"
    with pytest.raises(ValueError, match="Scheme not allowed"):
        service._validate_url("http://ok.example/x")
