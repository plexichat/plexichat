"""Extended targeted tests for the ConfigLoader (coercion, interpolation, atomic saves)."""

import json
import os
import stat

import pytest
import yaml

from src.utils.common_utils.utils.config.core import (
    ConfigLoader,
    MalformedConfigAction,
)


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "test_config.yaml")


def _write(path, data):
    with open(path, "w") as f:
        if path.endswith(".json"):
            json.dump(data, f)
        else:
            yaml.dump(data, f)


# ---------------------------------------------------------------------------
# Creation / loading
# ---------------------------------------------------------------------------


def test_missing_file_creates_defaults(config_path, tmp_path):
    defaults = {"host": "localhost", "port": 6379}
    c = ConfigLoader(config_path, default_config=defaults)
    assert os.path.exists(config_path)
    assert c.get("host") == "localhost"
    assert c.get("port") == 6379


def test_yaml_round_trip(config_path):
    _write(config_path, {"a": 1, "b": {"c": "x"}})
    c = ConfigLoader(config_path, default_config={"a": 0})
    assert c.get("a") == 1
    assert c.get("b") == {"c": "x"}


def test_json_round_trip(tmp_path):
    p = str(tmp_path / "c.json")
    _write(p, {"a": 1})
    c = ConfigLoader(p, default_config={"a": 0})
    assert c.get("a") == 1


def test_unsupported_extension_raises(tmp_path):
    p = str(tmp_path / "c.toml")
    with open(p, "w") as f:
        f.write("a = 1")
    with pytest.raises(ValueError):
        ConfigLoader(p)


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


def test_interpolation_env(monkeypatch, config_path):
    monkeypatch.setenv("PX_TEST_HOST", "db.internal")
    _write(config_path, {"host": "${PX_TEST_HOST}"})
    c = ConfigLoader(config_path)
    assert c.get("host") == "db.internal"


def test_interpolation_default_value(config_path):
    _write(config_path, {"host": "${PX_MISSING_VAR:-fallback}"})
    c = ConfigLoader(config_path)
    assert c.get("host") == "fallback"


def test_interpolation_missing_required_raises(monkeypatch, config_path):
    monkeypatch.delenv("PX_REQUIRED_VAR", raising=False)
    _write(config_path, {"host": "${PX_REQUIRED_VAR}"})
    with pytest.raises(ValueError, match="PX_REQUIRED_VAR"):
        ConfigLoader(config_path)


def test_interpolation_nested(config_path):
    _write(config_path, {"server": {"host": "${PX_NESTED:-nested-default}"}})
    c = ConfigLoader(config_path)
    assert c.get("server") == {"host": "nested-default"}


def test_interpolation_typed_default(monkeypatch, config_path):
    monkeypatch.setenv("PX_PORT", "5432")
    _write(config_path, {"port": "${PX_PORT}"})
    c = ConfigLoader(config_path, default_config={"port": 6379})
    assert c.get("port") == 5432
    assert isinstance(c.get("port"), int)


# ---------------------------------------------------------------------------
# Type coercion (fast path: plain strings with typed defaults)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_value", "default_value", "expected", "expected_type"),
    [
        ("6379", 5432, 6379, int),
        ("0", 5432, 0, int),
        ("-5", 5432, -5, int),
        (" 42 ", 5432, 42, int),  # whitespace tolerated by int()
        ("1.5", 3.14, 1.5, float),
        ("0.0", 3.14, 0.0, float),
        ("true", False, True, bool),
        ("True", False, True, bool),
        ("TRUE", False, True, bool),
        ("1", False, True, bool),
        ("yes", False, True, bool),
        ("on", False, True, bool),
        ("false", True, False, bool),
        ("0", True, False, bool),
        ("no", True, False, bool),
        ("off", True, False, bool),
        # Unparseable for the typed default -> left as string
        ("abc", 5432, "abc", str),
        ("", 5432, "", str),
        ("12.34.56", 5432, "12.34.56", str),
        # String default stays string even for numeric-looking values
        ("6379", "5432", "6379", str),
    ],
)
def test_fast_path_coercion(
    config_path, file_value, default_value, expected, expected_type
):
    _write(config_path, {"value": file_value})
    c = ConfigLoader(config_path, default_config={"value": default_value})
    got = c.get("value")
    assert got == expected
    assert isinstance(got, expected_type)


@pytest.mark.parametrize("value", ["abc", "", "123abc"])
def test_plain_strings_stay_strings(config_path, value):
    _write(config_path, {"name": value})
    c = ConfigLoader(config_path, default_config={"name": "default"})
    assert c.get("name") == value


def test_no_default_no_coercion(config_path):
    _write(config_path, {"value": "42"})
    c = ConfigLoader(config_path, default_config={})
    assert c.get("value") == "42"
    assert isinstance(c.get("value"), str)


def test_bool_default_with_int_style_value(config_path):
    _write(config_path, {"enabled": "1"})
    c = ConfigLoader(config_path, default_config={"enabled": False})
    assert c.get("enabled") is True


def test_nested_coercion_applies_recursively(config_path):
    _write(config_path, {"db": {"port": "5433", "host": "localhost"}})
    c = ConfigLoader(
        config_path,
        default_config={"db": {"port": 5432, "host": "localhost"}},
    )
    assert c.get("db")["port"] == 5433
    assert isinstance(c.get("db")["port"], int)
    assert c.get("db")["host"] == "localhost"


def test_list_values_coerced_recursively(config_path):
    _write(config_path, {"ports": ["1", "2"]})
    c = ConfigLoader(config_path, default_config={"ports": [1, 2]})
    # List elements are coerced against the typed default list (same
    # recursive rule as nested dicts).
    assert c.get("ports") == [1, 2]
    assert all(isinstance(p, int) for p in c.get("ports"))


def test_list_without_typed_default_stays_strings(config_path):
    _write(config_path, {"ports": ["1", "2"]})
    c = ConfigLoader(config_path, default_config={"ports": []})
    assert c.get("ports") == ["1", "2"]


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


def test_deep_merge_nested(config_path):
    _write(config_path, {"a": {"b": 2, "c": 3}})
    c = ConfigLoader(config_path, default_config={"a": {"b": 1, "d": 4}, "e": 5})
    merged = c.config
    assert merged["a"] == {"b": 2, "c": 3, "d": 4}
    assert merged["e"] == 5


# ---------------------------------------------------------------------------
# get / set / cache invalidation
# ---------------------------------------------------------------------------


def test_get_default_when_missing(config_path):
    c = ConfigLoader(config_path, default_config={})
    assert c.get("missing") is None
    assert c.get("missing", 7) == 7


def test_set_updates_and_persists(config_path):
    c = ConfigLoader(config_path, default_config={"a": 1})
    c.set("a", 2)
    assert c.get("a") == 2
    c2 = ConfigLoader(config_path, default_config={"a": 1})
    assert c2.get("a") == 2


def test_get_cache_invalidated_on_set(config_path):
    c = ConfigLoader(config_path, default_config={"a": 1})
    assert c.get("a") == 1
    c.set("a", 99)
    assert c.get("a") == 99
    c.set("a", 100)
    assert c.get("a") == 100


# ---------------------------------------------------------------------------
# Malformed config handling
# ---------------------------------------------------------------------------


def test_malformed_crash_on_single(config_path):
    with open(config_path, "w") as f:
        f.write("{ invalid yaml: [")
    with pytest.raises(ValueError):
        ConfigLoader(
            config_path, malformed_action=MalformedConfigAction.CRASH_ON_SINGLE
        )


def test_malformed_crash_on_many(config_path):
    with open(config_path, "w") as f:
        f.write(": : : :")
    with pytest.raises(ValueError):
        ConfigLoader(config_path, malformed_action=MalformedConfigAction.CRASH_ON_MANY)


def test_malformed_ignore_uses_defaults(config_path):
    with open(config_path, "w") as f:
        f.write(": : : :")
    c = ConfigLoader(
        config_path,
        default_config={"a": 1},
        malformed_action=MalformedConfigAction.IGNORE,
    )
    assert c.get("a") == 1


# ---------------------------------------------------------------------------
# Atomic save: mode preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o644, 0o600, 0o640, 0o664])
def test_save_preserves_existing_mode(config_path, mode):
    _write(config_path, {"a": 1})
    os.chmod(config_path, mode)
    c = ConfigLoader(config_path, default_config={"a": 1})
    c.set("a", 2)
    assert stat.S_IMODE(os.stat(config_path).st_mode) == mode


def test_save_new_file_uses_umask(config_path):
    old = os.umask(0)
    os.umask(old)
    c = ConfigLoader(config_path, default_config={"a": 1})
    c.set("a", 2)
    os.umask(old)
    assert stat.S_IMODE(os.stat(config_path).st_mode) == (0o666 & ~old)


def test_save_leaves_no_temp_files(config_path, tmp_path):
    c = ConfigLoader(config_path, default_config={"a": 1})
    for i in range(5):
        c.set("a", i)
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


def test_save_round_trip_after_multiple_sets(config_path):
    c = ConfigLoader(config_path, default_config={"a": 1, "nested": {"x": "y"}})
    c.set("a", 42)
    c.set("nested", {"x": "z", "w": 1})
    c2 = ConfigLoader(config_path, default_config={})
    assert c2.get("a") == 42
    assert c2.get("nested") == {"x": "z", "w": 1}


def test_save_json_mode(tmp_path):
    p = str(tmp_path / "c.json")
    _write(p, {"a": 1})
    c = ConfigLoader(p, default_config={"a": 1})
    c.set("a", 5)
    with open(p) as f:
        assert json.load(f)["a"] == 5
