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


def _dummy_user():
    from types import SimpleNamespace

    return SimpleNamespace(user_id=1, username="u", avatar_url=None, badges=[])


def _rejects_bad_id(coro, args):
    import asyncio
    from unittest.mock import patch

    import pytest
    from fastapi import HTTPException

    with (
        patch("src.api.get_messaging", return_value=object()),
        patch("src.api.get_servers", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(coro(*args, _dummy_user()))
    assert exc.value.status_code == 400


def test_pin_message_rejects_out_of_range_id():
    from src.api.routes.messages.pins import pin_message, unpin_message

    _rejects_bad_id(pin_message, ("123", "-1"))
    _rejects_bad_id(unpin_message, ("123", "99000000000000000000"))


def test_get_message_rejects_out_of_range_id():
    from src.api.routes.messages.messages_crud.retrieve import RetrieveMixin

    _rejects_bad_id(RetrieveMixin().get_message, ("123", "-5"))


def test_delete_message_rejects_out_of_range_id():
    from src.api.routes.messages.messages_crud.delete import DeleteMixin

    _rejects_bad_id(DeleteMixin().delete_message, ("123", "0"))


def test_send_payload_counts_are_capped():
    import pytest
    from fastapi import HTTPException
    from types import SimpleNamespace

    from src.api.routes.messages.messages_crud.send import _check_payload_counts

    ok = SimpleNamespace(attachments=[object()] * 10, embeds=[{}] * 10)
    _check_payload_counts(ok)

    too_many_attachments = SimpleNamespace(attachments=[object()] * 11, embeds=None)
    with pytest.raises(HTTPException) as exc:
        _check_payload_counts(too_many_attachments)
    assert exc.value.status_code == 400

    too_many_embeds = SimpleNamespace(attachments=None, embeds=[{}] * 11)
    with pytest.raises(HTTPException) as exc:
        _check_payload_counts(too_many_embeds)
    assert exc.value.status_code == 400


def test_ack_rejects_out_of_range_cursor():
    from src.api.routes.messages.read_receipts import acknowledge_messages

    _rejects_bad_id(acknowledge_messages, ("123", "99000000000000000000"))


def test_bulk_ack_rejects_out_of_range_ids():
    import asyncio
    from unittest.mock import patch

    import pytest
    from fastapi import HTTPException

    from src.api.routes.messages.read_receipts import acknowledge_messages_bulk

    with patch("src.api.get_messaging", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(acknowledge_messages_bulk({"123": "-1"}, _dummy_user()))
    assert exc.value.status_code == 400


def test_read_all_server_rejects_out_of_range_id():
    import asyncio
    from unittest.mock import patch

    import pytest
    from fastapi import HTTPException

    from src.api.routes.messages.read_receipts import mark_all_server_read

    with (
        patch("src.api.get_messaging", return_value=object()),
        patch("src.api.get_servers", return_value=object()),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mark_all_server_read("0", _dummy_user()))
    assert exc.value.status_code == 400


def test_export_id_is_bounded():
    import pytest
    from fastapi import HTTPException

    from src.api.routes.messages.export import _validate_export_id

    good = "export_" + "A" * 43
    assert _validate_export_id(good) == good

    for bad in ("", "A" * 129, "export_abc\ndef", None):
        with pytest.raises(HTTPException) as exc:
            _validate_export_id(bad)
        assert exc.value.status_code == 400
