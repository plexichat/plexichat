"""Focused unit tests for the message-route request boundaries.

These check the guard metadata/callables directly rather than booting the full
app: every fix here is a request-boundary constraint, so a signature check or a
direct guard call is both faster and more precise than an end-to-end request.
"""

import inspect


def _query_metadata(func, name):
    """Constraint objects attached to a FastAPI Query parameter."""
    param = inspect.signature(func).parameters[name].default
    return list(getattr(param, "metadata", []))


def _has_constraint(func, name, cls_name):
    return any(type(m).__name__ == cls_name for m in _query_metadata(func, name))


_CONSTRAINT_ATTRS = {
    "MaxLen": "max_length",
    "MinLen": "min_length",
    "Ge": "ge",
    "Le": "le",
    "Gt": "gt",
    "Lt": "lt",
}


def _constraint_value(func, name, cls_name):
    for m in _query_metadata(func, name):
        if type(m).__name__ == cls_name:
            return getattr(m, _CONSTRAINT_ATTRS[cls_name])
    raise AssertionError(f"no {cls_name} on {name}")


def test_search_query_is_capped():
    from src.api.routes.messages.search import search_messages

    assert _has_constraint(search_messages, "content", "MaxLen")
    assert _constraint_value(search_messages, "content", "MaxLen") <= 200


def test_pinned_message_list_is_capped():
    from src.api.routes.messages.messages_list import get_pinned_messages

    assert _has_constraint(get_pinned_messages, "limit", "Le")
    assert _constraint_value(get_pinned_messages, "limit", "Le") <= 200
    assert _has_constraint(get_pinned_messages, "limit", "Ge")


def test_require_snowflake_accepts_real_ids():
    from src.api.routes.messages.helpers import require_snowflake

    assert require_snowflake("123456789") == 123456789
    assert require_snowflake(42) == 42


def test_require_snowflake_rejects_out_of_range_ids():
    import pytest
    from fastapi import HTTPException

    from src.api.routes.messages.helpers import require_snowflake

    for bad in ("0", "-1", "99000000000000000000", "abc", None):
        with pytest.raises(HTTPException) as exc:
            require_snowflake(bad, "message_id", "Invalid message ID")
        assert exc.value.status_code == 400
