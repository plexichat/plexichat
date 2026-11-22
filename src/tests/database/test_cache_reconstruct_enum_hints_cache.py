"""Regression tests for the memoized enum-field metadata on cache reconstruction.

``reconstruct_object`` runs on every cache hit. Resolving a dataclass's type
hints with ``typing.get_type_hints`` there used to happen once per reconstructed
object; the field-to-enum mapping depends only on the class definition (no
request/user data, identical on every node), so it is now computed once per
class and memoized. These tests pin both the call-count reduction and unchanged
coercion behaviour.
"""

import dataclasses
import enum
import typing
from unittest.mock import patch

from src.core.database.cache import serialization as s


class _Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class _Shape(enum.Enum):
    ROUND = "round"
    SQUARE = "square"


@dataclasses.dataclass
class _WithEnum:
    label: str
    color: _Color
    shape: typing.Optional[_Shape] = None


@dataclasses.dataclass
class _OtherEnum:
    label: str
    shape: _Shape


@dataclasses.dataclass
class _Unresolvable:
    # A forward reference that can never resolve: get_type_hints raises, and
    # reconstruction must fail closed (leave the raw value untouched).
    value: "NoSuchTypeInThisModule"  # noqa: F821 - deliberately unresolvable


s.register_reconstructable_type(_Color, _Shape, _WithEnum, _OtherEnum, _Unresolvable)


def _type_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _payload(cls: type, **fields):
    return {"__type__": _type_name(cls), **fields}


def test_type_hints_resolved_once_per_class_not_per_reconstruction():
    """The hot path must not call get_type_hints on every cache hit."""
    s.reset_type_hints_cache_internal()
    real = typing.get_type_hints
    resolved = []

    def counting(cls, *args, **kwargs):
        resolved.append(cls)
        return real(cls, *args, **kwargs)

    payload_a = _payload(_WithEnum, label="a", color="red", shape="round")
    payload_b = _payload(_OtherEnum, label="b", shape="square")

    with patch.object(typing, "get_type_hints", counting):
        for _ in range(50):
            s.reconstruct_object(payload_a)
            s.reconstruct_object(payload_b)

    assert resolved.count(_WithEnum) == 1
    assert resolved.count(_OtherEnum) == 1
    assert len(resolved) == 2

    # A second burst reuses both memoized mappings without another resolution.
    with patch.object(typing, "get_type_hints", counting):
        for _ in range(50):
            s.reconstruct_object(payload_a)
            s.reconstruct_object(payload_b)
    assert len(resolved) == 2


def test_enum_and_optional_enum_fields_still_coerced():
    s.reset_type_hints_cache_internal()
    out = s.reconstruct_object(
        _payload(_WithEnum, label="x", color="blue", shape="square")
    )
    assert isinstance(out, _WithEnum)
    assert out.color is _Color.BLUE
    assert out.shape is _Shape.SQUARE


def test_already_typed_enum_value_is_left_alone():
    s.reset_type_hints_cache_internal()
    out = s.reconstruct_object(
        _payload(_WithEnum, label="x", color=_Color.RED, shape=None)
    )
    assert out.color is _Color.RED
    assert out.shape is None


def test_invalid_enum_value_falls_back_to_raw_value():
    s.reset_type_hints_cache_internal()
    out = s.reconstruct_object(
        _payload(_WithEnum, label="x", color="not-a-color", shape=None)
    )
    assert out.color == "not-a-color"


def test_enum_field_mapping_is_isolated_per_class():
    """A memo entry for one class must never be reused for another."""
    s.reset_type_hints_cache_internal()
    out_a = s.reconstruct_object(
        _payload(_WithEnum, label="a", color="red", shape=None)
    )
    out_b = s.reconstruct_object(_payload(_OtherEnum, label="b", shape="round"))
    assert isinstance(out_a, _WithEnum) and out_a.color is _Color.RED
    assert isinstance(out_b, _OtherEnum) and out_b.shape is _Shape.ROUND
    # Distinct classes, distinct field sets -> distinct memoized mappings.
    assert s._enum_fields_cache[_WithEnum] == {"color": _Color, "shape": _Shape}
    assert s._enum_fields_cache[_OtherEnum] == {"shape": _Shape}


def test_unresolvable_hints_fail_closed():
    s.reset_type_hints_cache_internal()
    out = s.reconstruct_object(_payload(_Unresolvable, value="raw"))
    assert isinstance(out, _Unresolvable)
    assert out.value == "raw"
    assert s._enum_fields_cache[_Unresolvable] == {}
