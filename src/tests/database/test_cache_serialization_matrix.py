"""Dense matrix tests for cache serialization (``ensure_serializable`` /
``reconstruct_object`` / ``generate_cache_key``)."""

import dataclasses
import enum

import pytest

from src.core.database.cache.serialization import (
    ensure_serializable,
    reconstruct_object,
    generate_cache_key,
)


class _State(enum.Enum):
    ON = "on"
    OFF = "off"


@dataclasses.dataclass
class _Inner:
    label: str


@dataclasses.dataclass
class _Outer:
    name: str
    inner: _Inner


class _PydanticLike:
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def model_dump(self):
        return {"a": self.a, "b": self.b}


class _OldStyle:
    def __init__(self, n):
        self.n = n

    def dict(self):
        return {"n": self.n}


@dataclasses.dataclass
class _StrictNegative:
    x: int

    def __post_init__(self):
        if self.x < 0:
            raise ValueError("no negatives")


@dataclasses.dataclass
class _WithInitVar:
    label: str
    secret: dataclasses.InitVar[str] = ""


# ---------------------------------------------------------------------------
# Primitive round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        1,
        -1,
        2**53,
        -(2**53),
        3.14159,
        0.0,
        "string",
        "",
        "unicode ünïcode 🚀",
        [1, "two", 3.0, None],
        {"k": "v", "n": 1, "b": True},
        {"nested": {"deep": [1, {"x": 2}]}},
    ],
)
def test_primitive_round_trip(value):
    assert reconstruct_object(ensure_serializable(value)) == value


@pytest.mark.parametrize("value", [("t", 1), (1, 2, 3), ()])
def test_tuple_becomes_list(value):
    out = ensure_serializable(value)
    assert out == list(value)
    assert isinstance(out, list)


@pytest.mark.parametrize("value", [{1, 2, 3}, set(), {1}])
def test_set_becomes_sorted_list(value):
    out = ensure_serializable(value)
    assert sorted(out) == sorted(value)
    assert isinstance(out, list)


def test_enum_serializes_to_value():
    assert ensure_serializable(_State.ON) == "on"


# ---------------------------------------------------------------------------
# Dataclass round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        _Inner("x"),
        _Outer("n", _Inner("deep")),
        _PydanticLike(1, 2),
        _OldStyle(7),
    ],
)
def test_object_round_trip_preserves_type(obj):
    data = ensure_serializable(obj)
    assert "__type__" in data
    back = reconstruct_object(data)
    assert type(back) is type(obj)


def test_dataclass_fields_round_trip():
    back = reconstruct_object(ensure_serializable(_Outer("n", _Inner("deep"))))
    assert back.name == "n"
    assert back.inner.label == "deep"


def test_pydantic_like_fields_round_trip():
    back = reconstruct_object(ensure_serializable(_PydanticLike(3, 4)))
    assert back.a == 3
    assert back.b == 4


def test_old_style_fields_round_trip():
    back = reconstruct_object(ensure_serializable(_OldStyle(9)))
    assert back.n == 9


def test_nested_objects_inside_containers():
    data = ensure_serializable({"item": _Inner("z"), "list": [_State.ON, _OldStyle(1)]})
    back = reconstruct_object(data)
    assert isinstance(back["item"], _Inner)
    assert back["item"].label == "z"
    assert back["list"][0] == "on"
    assert isinstance(back["list"][1], _OldStyle)
    assert back["list"][1].n == 1


# ---------------------------------------------------------------------------
# Reconstruction fallbacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"__type__": "no.such.module.Cls", "a": 1}, {"a": 1}),
        ({"__type__": "builtins.does_not_exist"}, {}),
    ],
)
def test_unknown_type_returns_dict(data, expected):
    out = reconstruct_object(data)
    assert out == expected
    assert isinstance(out, dict)


def test_enum_reconstructs_member():
    data = {"__type__": f"{_State.__module__}.{_State.__qualname__}", "value": "off"}
    assert reconstruct_object(data) is _State.OFF


def test_initvar_field_skipped_and_not_serialized():
    # InitVar pseudo-fields are not instance attributes; before the guard in
    # ensure_serializable they raised AttributeError. They must neither
    # crash serialization nor leak into the serialized payload.
    obj = _WithInitVar(label="x", secret="hidden")
    data = ensure_serializable(obj)
    assert data["label"] == "x"
    assert "secret" not in data
    back = reconstruct_object(data)
    assert type(back) is _WithInitVar
    assert back.label == "x"
    assert back.secret == ""  # InitVar default applied on reconstruction


def test_bad_constructor_falls_back_to_params():
    # A dataclass whose ctor rejects the serialized fields returns the params.
    data = {
        "__type__": f"{_StrictNegative.__module__}.{_StrictNegative.__qualname__}",
        "x": -1,
    }
    assert reconstruct_object(data) == {"x": -1}


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1},
        [{"a": 1}, 2],
        "plain",
        42,
        None,
    ],
)
def test_plain_values_passthrough(value):
    assert reconstruct_object(value) == value


# ---------------------------------------------------------------------------
# generate_cache_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(1,), (1, 2), ("a",), ({"k": 1},)])
def test_key_stable_for_same_args(args):
    assert generate_cache_key("p", *args) == generate_cache_key("p", *args)


def test_key_differs_on_different_values():
    assert generate_cache_key("p", 1) != generate_cache_key("p", 2)
    assert generate_cache_key("p", "a") != generate_cache_key("p", "b")
    assert generate_cache_key("p", 1, 2) != generate_cache_key("p", 2, 1)


def test_key_kwargs_order_insensitive():
    assert generate_cache_key("p", a=1, b=2) == generate_cache_key("p", b=2, a=1)


def test_key_dict_content_hashed():
    assert generate_cache_key("p", {"a": 1, "b": 2}) == generate_cache_key(
        "p", {"b": 2, "a": 1}
    )
    assert generate_cache_key("p", {"a": 1}) != generate_cache_key("p", {"a": 2})


def test_key_prefix_separates_namespaces():
    assert generate_cache_key("user", 1) != generate_cache_key("session", 1)
    assert generate_cache_key("user", 1).startswith("user:")


@pytest.mark.parametrize("prefix", ["a", "longer-prefix", ""])
def test_key_prefix_appears(prefix):
    key = generate_cache_key(prefix, 1)
    if prefix:
        assert key.startswith(prefix + ":")
    else:
        assert ":int:1" in key


def test_key_differs_by_kwarg_name():
    assert generate_cache_key("p", x=1) != generate_cache_key("p", y=1)
