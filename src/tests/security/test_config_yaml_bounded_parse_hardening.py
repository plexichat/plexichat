"""Regression: parsing an operator config must be bounded, not a DoS primitive.

``ConfigLoader._load_config`` fed the whole config file to ``yaml.safe_load``
with no limit on file size, nesting depth, or node count.  A crafted YAML file
is enough to break the security boundary of the loader itself:

* a few hundred nested sequences make PyYAML's recursive composer raise
  ``RecursionError``.  That is *not* a ``yaml.YAMLError``, so it escaped the
  loader's malformed-config handler and crashed the process at startup;
* an arbitrarily large file was read and decoded in full before any check.

The loader now parses through a bounded SafeLoader (size / depth / node caps)
and reports any over-limit input through the normal malformed-config path,
which fails closed instead of crashing with an uncaught interpreter error.
"""

import pytest


pytestmark = [pytest.mark.security, pytest.mark.unit]


def _deep_yaml(depth: int) -> str:
    """A valid YAML document nested ``depth`` levels deep."""
    return "root: " + "[" * depth + "x" + "]" * depth + "\n"


def test_deeply_nested_config_is_reported_as_malformed_not_recursionerror(tmp_path):
    """Deep YAML must fail closed via the malformed-config path, not crash."""
    path = tmp_path / "config.yaml"
    path.write_text(_deep_yaml(600), encoding="utf-8")

    # Pre-fix PyYAML raises RecursionError, which is not a ValueError and is
    # not caught by the loader -> pytest.raises(ValueError) fails on old code.
    with pytest.raises(ValueError) as exc:
        from src.utils.common_utils.utils.config.core import ConfigLoader

        ConfigLoader(str(path), default_config={"root": []})

    message = str(exc.value).lower()
    assert "config file is malformed" in message
    assert "depth" in message or "nesting" in message or "bound" in message


def test_oversize_config_is_rejected_before_any_parse(tmp_path, monkeypatch):
    """An oversized config must be rejected before bytes reach the parser."""
    import src.utils.common_utils.utils.config.core as core

    path = tmp_path / "config.yaml"
    # Well above any sane config limit; a flat scalar parses trivially fast,
    # but the loader must refuse to read/parse it at all.
    path.write_text("a: " + ("x" * (8 * 1024 * 1024)), encoding="utf-8")

    calls = {"n": 0}
    real_loader = core._BoundedSafeLoader

    class SpyLoader(real_loader):
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(core, "_BoundedSafeLoader", SpyLoader)

    with pytest.raises(ValueError) as exc:
        core.ConfigLoader(str(path))

    assert "size" in str(exc.value).lower() or "exceed" in str(exc.value).lower()
    assert calls["n"] == 0, "oversize config was parsed instead of rejected up front"


def test_bounded_loader_api_rejects_bomb_and_accepts_normal_yaml():
    """The bounded helper rejects abusive input and preserves normal config."""
    from src.utils.common_utils.utils.config.core import (
        ConfigParseError,
        safe_load_bounded,
    )

    with pytest.raises(ConfigParseError):
        safe_load_bounded(_deep_yaml(600).encode("utf-8"))

    assert safe_load_bounded(b"api:\n  title: ok\n") == {"api": {"title": "ok"}}


def test_legitimate_config_still_loads(tmp_path):
    """Non-vacuity: a normal config with an explicit security allowlist works."""
    from src.utils.common_utils.utils.config.core import ConfigLoader

    path = tmp_path / "config.yaml"
    path.write_text(
        "api:\n  title: ok\n  cors_origins:\n    - https://example.com\n",
        encoding="utf-8",
    )
    loader = ConfigLoader(str(path), default_config={"api": {"title": "default"}})
    assert loader.get("api")["title"] == "ok"
    assert loader.get("api")["cors_origins"] == ["https://example.com"]
