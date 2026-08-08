"""Dense matrix tests for the ConfigLoader interpolation + typed coercion."""

import pytest
import yaml

from src.utils.common_utils.utils.config.core import ConfigLoader


@pytest.fixture
def cfg(tmp_path):
    path = str(tmp_path / "c.yaml")

    def build(mapping, defaults=None):
        with open(path, "w") as f:
            yaml.dump(mapping, f)
        return ConfigLoader(path, default_config=defaults or {})

    return build


# ---------------------------------------------------------------------------
# Environment interpolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_name", ["PX_M_A", "PX_M_B", "PX_M_C"])
def test_env_substitution(cfg, monkeypatch, var_name):
    monkeypatch.setenv(var_name, "resolved-value")
    c = cfg({"value": f"${{{var_name}}}"})
    assert c.get("value") == "resolved-value"


def test_missing_required_env_raises(cfg, monkeypatch):
    monkeypatch.delenv("PX_MISSING_REQUIRED", raising=False)
    with pytest.raises(ValueError, match="PX_MISSING_REQUIRED"):
        cfg({"value": "${PX_MISSING_REQUIRED}"})


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("${PX_UNDEF:-fallback}", "fallback"),
        ("${PX_UNDEF:-with spaces}", "with spaces"),
        ("${PX_UNDEF:-}", ""),
        ("${PX_UNDEF:-123}", "123"),
    ],
)
def test_env_default_values(cfg, monkeypatch, expression, expected):
    monkeypatch.delenv("PX_UNDEF", raising=False)
    c = cfg({"value": expression})
    assert c.get("value") == expected


def test_set_env_beats_default(cfg, monkeypatch):
    monkeypatch.setenv("PX_SET_DEFAULT", "env-wins")
    c = cfg({"value": "${PX_SET_DEFAULT:-default-loses}"})
    assert c.get("value") == "env-wins"


def test_empty_env_value_beats_default(cfg, monkeypatch):
    # An env var set to "" is a value, not a missing var -> no default.
    monkeypatch.setenv("PX_EMPTY", "")
    c = cfg({"value": "${PX_EMPTY:-not-used}"})
    assert c.get("value") == ""


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("a-${PX_PART_A}-b", "a-one-b"),
        ("${PX_PART_A}${PX_PART_B}", "onetwo"),
        ("${PX_PART_A}-${PX_PART_B}-${PX_PART_A}", "one-two-one"),
        ("plain-${PX_PART_A}", "plain-one"),
        ("${PX_PART_B}-end", "two-end"),
    ],
)
def test_multiple_interpolations(cfg, monkeypatch, template, expected):
    monkeypatch.setenv("PX_PART_A", "one")
    monkeypatch.setenv("PX_PART_B", "two")
    c = cfg({"value": template})
    assert c.get("value") == expected


def test_interpolation_nested_dict(cfg, monkeypatch):
    monkeypatch.setenv("PX_NEST_HOST", "db.internal")
    c = cfg({"db": {"host": "${PX_NEST_HOST}", "port": 5432}})
    assert c.get("db")["host"] == "db.internal"
    assert c.get("db")["port"] == 5432


def test_interpolation_in_list(cfg, monkeypatch):
    monkeypatch.setenv("PX_LIST_ITEM", "item-x")
    c = cfg({"items": ["a", "${PX_LIST_ITEM}", "c"]})
    assert c.get("items") == ["a", "item-x", "c"]


# ---------------------------------------------------------------------------
# Typed coercion of interpolated values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "default", "expected", "expected_type"),
    [
        ("${PX_T_PORT}", 5432, 7000, int),
        ("${PX_T_RATIO}", 1.5, 2.25, float),
        ("${PX_T_BOOL}", False, True, bool),
        ("${PX_T_NAME}", "localhost", "db.internal", str),
    ],
)
def test_typed_coercion_of_interpolated(
    cfg, monkeypatch, expr, default, expected, expected_type
):
    monkeypatch.setenv("PX_T_PORT", "7000")
    monkeypatch.setenv("PX_T_RATIO", "2.25")
    monkeypatch.setenv("PX_T_BOOL", "1")
    monkeypatch.setenv("PX_T_NAME", "db.internal")
    c = cfg({"value": expr}, defaults={"value": default})
    got = c.get("value")
    assert got == expected
    assert isinstance(got, expected_type)


@pytest.mark.parametrize(
    ("expr", "default", "expected"),
    [
        ("${PX_T_DEF_PORT:-8080}", 5432, 8080),
        ("${PX_T_DEF_BOOL:-0}", True, False),
        ("${PX_T_DEF_INT:-99}", 1, 99),
    ],
)
def test_typed_coercion_of_interpolated_defaults(
    cfg, monkeypatch, expr, default, expected
):
    # No env var set: the ${VAR:-default} fallback is used, then coerced to
    # the typed default's type.
    monkeypatch.delenv("PX_T_DEF_PORT", raising=False)
    monkeypatch.delenv("PX_T_DEF_BOOL", raising=False)
    monkeypatch.delenv("PX_T_DEF_INT", raising=False)
    c = cfg({"value": expr}, defaults={"value": default})
    assert c.get("value") == expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("no", False),
        ("off", False),
        ("0", False),
    ],
)
def test_bool_coercion_matrix(cfg, monkeypatch, env_value, expected):
    monkeypatch.setenv("PX_BOOL_VAL", env_value)
    c = cfg({"value": "${PX_BOOL_VAL}"}, defaults={"value": False})
    assert c.get("value") is expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("42", 42),
        ("0", 0),
        ("-7", -7),
        (" 42 ", 42),
    ],
)
def test_int_coercion_matrix(cfg, monkeypatch, env_value, expected):
    monkeypatch.setenv("PX_INT_VAL", env_value)
    c = cfg({"value": "${PX_INT_VAL}"}, defaults={"value": 1})
    assert c.get("value") == expected


def test_uncoercible_interpolated_value_stays_string(cfg, monkeypatch):
    monkeypatch.setenv("PX_BAD_INT", "abc")
    c = cfg({"value": "${PX_BAD_INT}"}, defaults={"value": 1})
    assert c.get("value") == "abc"
    assert isinstance(c.get("value"), str)


def test_nested_typed_coercion_via_interpolation(cfg, monkeypatch):
    monkeypatch.setenv("PX_DB_PORT", "5433")
    c = cfg({"db": {"port": "${PX_DB_PORT}"}}, defaults={"db": {"port": 5432}})
    assert c.get("db")["port"] == 5433
    assert isinstance(c.get("db")["port"], int)


# ---------------------------------------------------------------------------
# get() semantics
# ---------------------------------------------------------------------------


def test_get_missing_returns_default(cfg):
    c = cfg({"a": 1})
    assert c.get("missing") is None
    assert c.get("missing", 42) == 42


def test_get_missing_falsy_default(cfg):
    c = cfg({})
    assert c.get("x", 0) == 0
    assert c.get("x", "") == ""
    assert c.get("x", False) is False


def test_get_different_defaults_do_not_share_cache(cfg):
    c = cfg({"v": 1})
    assert c.get("v") == 1
    assert c.get("v", 99) == 1  # key exists -> default ignored
    assert c.get("missing", "a") == "a"
    assert c.get("missing", "b") == "b"
