"""Regression tests for serialize-side metadata memoization in ``ensure_serializable``.

``ensure_serializable`` runs on every cache write. For a cached dataclass it
used to call ``dataclasses.fields(obj)`` (which allocates a fresh tuple of
``Field`` objects on every call) and rebuild the ``module.Name`` ``__type__``
tag with an f-string, even though both depend only on the class definition,
never on the instance. They are now computed once per class and memoized. These
tests pin both the call-count reduction and unchanged serialization behaviour.
"""

import dataclasses
from unittest.mock import patch

from src.core.database.cache import serialization as s


@dataclasses.dataclass
class _Payload:
    a: int
    b: str
    c: list


@dataclasses.dataclass
class _WithInitVar:
    label: str
    secret: dataclasses.InitVar[str] = ""


class _PydanticLike:
    def __init__(self, a):
        self.a = a

    def model_dump(self):
        return {"a": self.a}


class _OldStyle:
    def __init__(self, n):
        self.n = n

    def dict(self):
        return {"n": self.n}


def _tag(cls: type) -> str:
    return f"{cls.__module__}.{cls.__name__}"


def test_fields_resolved_once_per_class_not_per_serialization():
    """The hot write path must not call dataclasses.fields on every write."""
    s.reset_serialization_meta_cache_internal()
    real_fields = dataclasses.fields
    calls = []

    def counting(cls, *args, **kwargs):
        calls.append(cls)
        return real_fields(cls, *args, **kwargs)

    payload = _Payload(1, "x", [1])

    with patch.object(dataclasses, "fields", counting):
        out = None
        for _ in range(50):
            out = s.ensure_serializable(payload)

    assert calls.count(_Payload) == 1
    assert out["__type__"] == _tag(_Payload)

    # A second burst reuses both memoized metadata entries without recomputing.
    with patch.object(dataclasses, "fields", counting):
        for _ in range(50):
            s.ensure_serializable(payload)
    assert calls.count(_Payload) == 1


def test_dataclass_serialization_output_unchanged():
    s.reset_serialization_meta_cache_internal()
    out = s.ensure_serializable(_Payload(2, "hi", [1, 2]))
    assert out == {"a": 2, "b": "hi", "c": [1, 2], "__type__": _tag(_Payload)}


def test_initvar_field_still_excluded():
    s.reset_serialization_meta_cache_internal()
    out = s.ensure_serializable(_WithInitVar("x", "hidden"))
    assert "secret" not in out
    assert out == {"label": "x", "__type__": _tag(_WithInitVar)}
    assert s._serialized_dataclass_fields_cache[_WithInitVar] == ("label",)


def test_type_tag_memoized_for_model_dump_and_dict_paths():
    s.reset_serialization_meta_cache_internal()
    assert s.ensure_serializable(_PydanticLike(3)) == {
        "a": 3,
        "__type__": _tag(_PydanticLike),
    }
    assert s.ensure_serializable(_OldStyle(4)) == {
        "n": 4,
        "__type__": _tag(_OldStyle),
    }
    assert s._serialized_type_tag_cache[_PydanticLike] == _tag(_PydanticLike)
    assert s._serialized_type_tag_cache[_OldStyle] == _tag(_OldStyle)


def test_per_class_isolation_and_reset():
    s.reset_serialization_meta_cache_internal()
    assert s.ensure_serializable(_Payload(1, "a", []))["__type__"] == _tag(_Payload)
    assert s.ensure_serializable(_WithInitVar("b"))["__type__"] == _tag(_WithInitVar)
    assert set(s._serialized_dataclass_fields_cache) == {_Payload, _WithInitVar}

    s.reset_serialization_meta_cache_internal()
    assert s._serialized_dataclass_fields_cache == {}
    assert s._serialized_type_tag_cache == {}
